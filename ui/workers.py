"""后台线程：所有会阻塞的网络请求都通过这里跑，避免界面卡死。

阶段 5 起承担 AI 生成调用。所有线程都遵循同一套约定：
  · 网络异常在 run() 里被捕获，通过 failed 信号抛出，不让线程静默死掉
  · cancel() 是协作式的：设置标志位，由请求循环自行退出
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QThread, pyqtSignal

from core.api_client import ApiError, ChatResult, DeepSeekClient, TestResult
from core.events import generate_event
from core.game import run_turn
from core.items import ItemRequest, generate_items
from core.jsonstream import JsonFieldStream
from core.models import Item, WorldState
from core.prompts import JSON_REQUIREMENT, retry_note
from core.savegame import HistoryLog, PlayerState
from core.validator import (
    DEFAULT_MAX_RETRIES,
    GenerationAttempt,
    GuardedResult,
    ValidationExhausted,
    guarded_generate,
    validate_content,
)
from core.world import WorldDocument
from core.worldgen import WorldGenError, generate_world, revise_world


class ApiTestThread(QThread):
    """在后台执行 API 连通性测试。

    QThread 自带 finished 信号，这里换个名字避免覆盖。
    """

    #: 测试结束，携带 TestResult
    done = pyqtSignal(object)

    def __init__(
        self,
        client: DeepSeekClient,
        mode: str = "connection",
        parent=None,
    ):
        super().__init__(parent)
        self._client = client
        self._mode = mode
        #: 线程跑完后的原始异常，供调试日志记录
        self.error: BaseException | None = None

    def run(self) -> None:  # noqa: D102 - QThread 入口
        try:
            if self._mode == "completion":
                result = self._client.test_completion()
            else:
                result = self._client.test_connection()
        except BaseException as exc:  # noqa: BLE001
            # test_* 内部已把网络异常转成 TestResult，
            # 走到这里说明是预料外的 bug，也不能让线程静默死掉
            self.error = exc
            result = TestResult(
                ok=False,
                title="内部错误",
                message=f"测试过程中出现未预期的错误：{type(exc).__name__}: {exc}",
            )

        self.done.emit(result)


class ChatStreamThread(QThread):
    """流式对话线程。

    进度通过 delta 信号逐片段推给界面，实现逐字显示。
    注意：信号是在**子线程**里发出的，Qt 会自动排队到主线程执行槽函数，
    所以槽函数里可以安全操作界面。
    """

    #: 收到一个文本片段
    delta = pyqtSignal(str)
    #: 正常结束，携带 ChatResult
    done = pyqtSignal(object)
    #: 失败，携带 ApiError
    failed = pyqtSignal(object)

    def __init__(
        self,
        client: DeepSeekClient,
        messages: list[dict],
        *,
        reason: str = "",
        json_mode: bool = False,
        thinking=None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._client = client
        self._messages = messages
        self._json_mode = json_mode
        self._thinking = thinking
        self._max_tokens = max_tokens
        self._temperature = temperature
        #: 用途标签，写进用量记录，例如「剧情」「道具」「校验」
        self.reason = reason

        self._stop = threading.Event()
        self.result: ChatResult | None = None
        self.error: ApiError | None = None

    # ---------- 控制 ----------

    def cancel(self) -> None:
        """请求取消。下一批数据到达时线程会自行退出。"""
        self._stop.set()

    @property
    def cancelled(self) -> bool:
        return self._stop.is_set()

    def _should_stop(self) -> bool:
        return self._stop.is_set()

    # ---------- 执行 ----------

    def run(self) -> None:  # noqa: D102 - QThread 入口
        try:
            result = self._client.stream_chat(
                self._messages,
                on_delta=self.delta.emit,
                should_stop=self._should_stop,
                json_mode=self._json_mode,
                thinking=self._thinking,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            )
        except ApiError as exc:
            self.error = exc
            self.failed.emit(exc)
            return
        except BaseException as exc:  # noqa: BLE001
            # 预料外的 bug：包成 ApiError，保持调用方的错误处理路径一致
            wrapped = ApiError(
                f"生成过程中出现未预期的错误：{type(exc).__name__}: {exc}",
                kind="unknown",
                detail=repr(exc),
            )
            self.error = wrapped
            self.failed.emit(wrapped)
            return

        self.result = result
        self.done.emit(result)


class ValidateThread(QThread):
    """世界观一致性校验线程（阶段 6）。

    两种模式：
      · mode="validate"  只校验界面里已给出的文本
      · mode="generate"  完整走「生成 → 校验 → 冲突则重试」循环

    每次真实的 API 调用都会通过 usage_ready 信号上报用量，
    由主线程记账 —— 跨线程直接改统计对象不安全。
    """

    #: 阶段说明文字，例如「第 2/3 次校验中…」
    progress = pyqtSignal(str)
    #: 生成阶段的流式文本
    delta = pyqtSignal(str)
    #: 一轮尝试结束，携带 GenerationAttempt
    attempt_done = pyqtSignal(object)
    #: 全部完成，携带 GuardedResult
    done = pyqtSignal(object)
    #: 失败，携带 ValidationExhausted 或 ApiError
    failed = pyqtSignal(object)
    #: (usage_dict, model, reason) —— 交给主线程计入用量
    usage_ready = pyqtSignal(object, str, str)

    def __init__(
        self,
        client: DeepSeekClient,
        world: WorldDocument | None,
        *,
        mode: str = "validate",
        content: str = "",
        instruction: str = "",
        kind: str = "内容",
        max_retries: int = DEFAULT_MAX_RETRIES,
        context_text: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._client = client
        self._world = world
        self._mode = mode
        self._content = content
        self._instruction = instruction
        self._kind = kind
        self._max_retries = max_retries
        self._context_text = context_text

        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    def _should_stop(self) -> bool:
        return self._stop.is_set()

    # ---------- 执行 ----------

    def run(self) -> None:  # noqa: D102
        try:
            if self._mode == "generate":
                self._run_generate()
            else:
                self._run_validate()
        except ValidationExhausted as exc:
            self.failed.emit(exc)
        except ApiError as exc:
            self.failed.emit(exc)
        except BaseException as exc:  # noqa: BLE001
            wrapped = ApiError(
                f"校验过程中出现未预期的错误：{type(exc).__name__}: {exc}",
                kind="unknown",
                detail=repr(exc),
            )
            self.failed.emit(wrapped)

    def _run_validate(self) -> None:
        self.progress.emit("校验中…")
        verdict, result = validate_content(
            self._client,
            self._world,
            self._content,
            kind=self._kind,
            should_stop=self._should_stop,
        )

        if result is not None and result.usage:
            self.usage_ready.emit(result.usage, result.model, "世界观校验")

        attempt = GenerationAttempt(index=1, content=self._content, verdict=verdict)
        self.attempt_done.emit(attempt)
        self.done.emit(
            GuardedResult(content=self._content, attempts=[attempt])
        )

    def _run_generate(self) -> None:
        def generate(conflict_reasons: list[str]) -> str:
            messages = [
                {"role": "system", "content": self._build_system()},
                {
                    "role": "user",
                    "content": (
                        f"{self._context_text}\n\n"
                        f"【本次任务】\n{self._instruction}"
                        f"{retry_note(conflict_reasons)}"
                    ),
                },
            ]

            result = self._client.stream_chat(
                messages,
                on_delta=self.delta.emit,
                should_stop=self._should_stop,
                max_tokens=1200,
                temperature=0.9,
            )
            if result.usage:
                self.usage_ready.emit(result.usage, result.model, self._kind)
            return result.content

        guarded = guarded_generate(
            self._client,
            self._world,
            generate=generate,
            kind=self._kind,
            max_retries=self._max_retries,
            on_progress=self.progress.emit,
            on_attempt=self.attempt_done.emit,
            should_stop=self._should_stop,
        )
        self.done.emit(guarded)

    def _build_system(self) -> str:
        return (
            "你是一个文字游戏的内容生成器。依据给定的世界观设定与当前局势"
            "生成内容，严格不违背世界观中的硬性规则，但可以合理延伸设定中"
            "未提及的部分。直接输出内容本身，不要复述设定，不要加解释。\n\n"
            + JSON_REQUIREMENT.replace("你必须只输出一个 json 对象，", "若任务要求 json 输出，")
        )


class ItemGenThread(QThread):
    """AI 道具生成线程（阶段 7）。

    一次生成会触发两类 API 调用：生成本身、以及紧随其后的世界观校验。
    两者的用量都通过 usage_ready 上报给主线程记账。
    """

    progress = pyqtSignal(str)
    #: 一轮尝试结束，携带 GenerationAttempt
    attempt_done = pyqtSignal(object)
    #: 完成，携带 ItemGenerationResult
    done = pyqtSignal(object)
    #: 失败，携带 ValidationExhausted 或 ApiError
    failed = pyqtSignal(object)
    #: (model, usage_dict, reason)
    usage_ready = pyqtSignal(str, object, str)

    def __init__(
        self,
        client: DeepSeekClient,
        world: WorldDocument | None,
        request: ItemRequest,
        *,
        player: PlayerState | None = None,
        state: WorldState | None = None,
        inventory: list[Item] | None = None,
        history: HistoryLog | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        parent=None,
    ):
        super().__init__(parent)
        self._client = client
        self._world = world
        self._request = request
        self._player = player
        self._state = state
        self._inventory = inventory
        self._history = history
        self._max_retries = max_retries

        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    def run(self) -> None:  # noqa: D102
        try:
            result = generate_items(
                self._client,
                self._world,
                self._request,
                player=self._player,
                state=self._state,
                inventory=self._inventory,
                history=self._history,
                max_retries=self._max_retries,
                on_progress=self.progress.emit,
                on_attempt=self.attempt_done.emit,
                on_usage=lambda model, usage, reason: self.usage_ready.emit(
                    model, usage, reason
                ),
                should_stop=lambda: self._stop.is_set(),
            )
        except ValidationExhausted as exc:
            self.failed.emit(exc)
        except ApiError as exc:
            self.failed.emit(exc)
        except BaseException as exc:  # noqa: BLE001
            self.failed.emit(
                ApiError(
                    f"生成道具时出现未预期的错误：{type(exc).__name__}: {exc}",
                    kind="unknown",
                    detail=repr(exc),
                )
            )
            return

        self.done.emit(result)


class TurnThread(QThread):
    """一个完整回合（阶段 9 主循环）。

    串起事件生成、状态落定、道具生成、历史折叠。
    所有环节都在子线程里跑，进度通过 progress 推给界面。
    """

    progress = pyqtSignal(str)
    #: 正文流式片段 —— 边生成边显示
    delta = pyqtSignal(str)
    #: 携带 TurnResult
    done = pyqtSignal(object)
    #: 携带 ValidationExhausted / ApiError
    failed = pyqtSignal(object)
    #: (model, usage_dict, reason) —— 一个回合可能来好几条
    usage_ready = pyqtSignal(str, object, str)

    def __init__(
        self,
        client: DeepSeekClient,
        world: WorldDocument | None,
        action: str,
        *,
        player: PlayerState,
        state: WorldState,
        history: HistoryLog,
        inventory: list | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        is_opening: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._client = client
        self._world = world
        self._action = action
        self._player = player
        self._state = state
        self._history = history
        self._inventory = inventory
        self._max_retries = max_retries
        self._is_opening = is_opening

        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    def run(self) -> None:  # noqa: D102
        # 事件是 JSON 输出，直接把原始分片打到界面上会看到
        # {"narrative": "铁索 这种噪音，所以只把 narrative 的值抠出来显示
        extractor = JsonFieldStream("narrative")

        def on_delta(piece: str) -> None:
            visible = extractor.feed(piece)
            if visible:
                self.delta.emit(visible)

        try:
            result = run_turn(
                self._client,
                self._world,
                self._action,
                player=self._player,
                state=self._state,
                history=self._history,
                inventory=self._inventory,
                max_retries=self._max_retries,
                is_opening=self._is_opening,
                on_progress=self.progress.emit,
                on_delta=on_delta,
                on_usage=lambda model, usage, reason: self.usage_ready.emit(
                    model, usage, reason
                ),
                should_stop=lambda: self._stop.is_set(),
            )
        except (ValidationExhausted, ApiError) as exc:
            self.failed.emit(exc)
            return
        except BaseException as exc:  # noqa: BLE001
            self.failed.emit(
                ApiError(
                    f"回合结算时出现未预期的错误：{type(exc).__name__}: {exc}",
                    kind="unknown",
                    detail=repr(exc),
                )
            )
            return

        self.done.emit(result)


class WorldGenThread(QThread):
    """世界观生成 / 修改线程。

    用户的大白话描述 → 完整的设定文档。
    生成过程中把文本流式推给界面，让用户看见它在写什么。
    """

    progress = pyqtSignal(str)
    #: 流式片段
    delta = pyqtSignal(str)
    #: 完成，携带 WorldGenResult
    done = pyqtSignal(object)
    #: 失败，携带 WorldGenError 或 ApiError
    failed = pyqtSignal(object)
    #: (model, usage_dict, reason)
    usage_ready = pyqtSignal(str, object, str)

    def __init__(
        self,
        client: DeepSeekClient,
        *,
        description: str = "",
        current_text: str = "",
        change_request: str = "",
        attempts: int = 3,
        parent=None,
    ):
        super().__init__(parent)
        self._client = client
        self._description = description
        self._current_text = current_text
        self._change_request = change_request
        self._attempts = attempts

        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    def run(self) -> None:  # noqa: D102
        common = dict(
            attempts=self._attempts,
            on_progress=self.progress.emit,
            on_delta=self.delta.emit,
            on_usage=lambda model, usage, reason: self.usage_ready.emit(
                model, usage, reason
            ),
            should_stop=lambda: self._stop.is_set(),
        )

        try:
            if self._current_text and self._change_request:
                result = revise_world(
                    self._client, self._current_text, self._change_request, **common
                )
            else:
                result = generate_world(
                    self._client, self._description, **common
                )
        except (WorldGenError, ApiError) as exc:
            self.failed.emit(exc)
            return
        except BaseException as exc:  # noqa: BLE001
            self.failed.emit(
                ApiError(
                    f"生成世界观时出现未预期的错误：{type(exc).__name__}: {exc}",
                    kind="unknown",
                    detail=repr(exc),
                )
            )
            return

        self.done.emit(result)


class EventThread(QThread):
    """AI 事件生成线程（阶段 8）。

    毁灭增量由框架在 generate_event 内部复核，不在这里处理 ——
    AI 只是提议，是否真的推进由 DoomState.advance 决定。
    """

    progress = pyqtSignal(str)
    attempt_done = pyqtSignal(object)
    #: 完成，携带 EventGenerationResult
    done = pyqtSignal(object)
    #: 失败，携带 ValidationExhausted 或 ApiError
    failed = pyqtSignal(object)
    #: (model, usage_dict, reason)
    usage_ready = pyqtSignal(str, object, str)

    def __init__(
        self,
        client: DeepSeekClient,
        world: WorldDocument | None,
        action: str,
        *,
        player: PlayerState | None = None,
        state: WorldState | None = None,
        inventory: list | None = None,
        history: HistoryLog | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        parent=None,
    ):
        super().__init__(parent)
        self._client = client
        self._world = world
        self._action = action
        self._player = player
        self._state = state
        self._inventory = inventory
        self._history = history
        self._max_retries = max_retries

        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    def run(self) -> None:  # noqa: D102
        try:
            result = generate_event(
                self._client,
                self._world,
                self._action,
                player=self._player,
                state=self._state,
                inventory=self._inventory,
                history=self._history,
                max_retries=self._max_retries,
                on_progress=self.progress.emit,
                on_attempt=self.attempt_done.emit,
                on_usage=lambda model, usage, reason: self.usage_ready.emit(
                    model, usage, reason
                ),
                should_stop=lambda: self._stop.is_set(),
            )
        except ValidationExhausted as exc:
            self.failed.emit(exc)
        except ApiError as exc:
            self.failed.emit(exc)
        except BaseException as exc:  # noqa: BLE001
            self.failed.emit(
                ApiError(
                    f"生成事件时出现未预期的错误：{type(exc).__name__}: {exc}",
                    kind="unknown",
                    detail=repr(exc),
                )
            )
            return

        self.done.emit(result)
