"""后台线程：所有会阻塞的网络请求都通过这里跑，避免界面卡死。

阶段 5 起承担 AI 生成调用。所有线程都遵循同一套约定：
  · 网络异常在 run() 里被捕获，通过 failed 信号抛出，不让线程静默死掉
  · cancel() 是协作式的：设置标志位，由请求循环自行退出
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QThread, pyqtSignal

from core.api_client import ApiError, ChatResult, DeepSeekClient, TestResult


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
