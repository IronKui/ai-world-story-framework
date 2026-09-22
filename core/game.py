"""游戏主循环。

一个回合的完整流程：

    玩家操作（点击选项 / 自由输入）
        ↓
    生成事件（阶段 8）—— 校验不通过就带着原因重试，最多 3 次
        ↓
    把事件里的状态变更落到世界状态上
        ↓
    事件若指示玩家获得了东西 → 现场生成道具（阶段 7）
        ↓
    记入历史；攒够了就折叠成长期摘要（阶段 4 的滚动摘要）
        ↓
    刷新界面，换上新一批行动选项

这个模块不碰界面，所有回调都是普通函数，方便直接跑测试。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from core.api_client import ApiError, DeepSeekClient
from core.debuglog import LOG
from core.events import GameEvent, apply_state_changes, generate_event
from core.items import ItemRequest, generate_items
from core.models import Item, WorldState
from core.prompts import world_block
from core.savegame import HistoryLog, PlayerState
from core.validator import DEFAULT_MAX_RETRIES
from core.world import WorldDocument

#: 开场的合成操作。事件生成器需要一句「玩家做了什么」，
#: 游戏刚开始时没有真实的玩家输入，用这句代替。
OPENING_ACTION = (
    "故事开始。玩家刚刚来到这个世界，请描写他此刻所处的环境、"
    "正在发生的事，以及他当下的处境。"
)

#: 折叠摘要时给多少 token
FOLD_MAX_TOKENS = 900

#: 记进历史时，事件正文保留多少字符。
#: 不必全留 —— 折叠摘要时会再压一遍，留太长只是浪费预算
HISTORY_ENTRY_CHARS = 260


@dataclass
class TurnResult:
    """一个回合的完整结果。"""

    action: str
    event: GameEvent
    #: 框架实际接受的毁灭增量
    applied_doom_delta: int = 0
    doom_note: str = ""
    #: 未接受时 AI 给的理由，用于界面提示
    doom_reason: str = ""
    #: 状态变更说明
    change_notes: list[str] = field(default_factory=list)
    #: 本回合获得的道具
    items_gained: list[Item] = field(default_factory=list)
    #: 本回合是否触发了一次历史折叠
    history_folded: bool = False
    #: 事件生成用掉的重试次数
    retries_used: int = 0
    #: 是否为开场回合
    is_opening: bool = False

    @property
    def doom_rejected(self) -> bool:
        return self.event.doom_delta > 0 and self.applied_doom_delta == 0

    @property
    def has_changes(self) -> bool:
        return bool(self.change_notes)


# ----------------------------------------------------------------------
# 历史折叠
# ----------------------------------------------------------------------

FOLD_SYSTEM = """你在为一位文字游戏玩家维护长期记忆摘要。玩家已经玩了很多回合，
你需要把「既有摘要」和「新增经历」合并成一份新的摘要，替换掉它们。

要求：
1. 输出一段 200~400 字的连续叙述。不要分点、不要小标题、不要 markdown。
2. 必须保留：玩家的重大选择及其后果、立场与关系的转变、结下的恩怨、
   身体与能力的变化、尚未了结的线索与承诺。
3. 必须合并：新摘要要覆盖既有摘要的内容，不是把新的追加在旧的后面。
   越早的经历可以越简略，但不能凭空消失重要的事。
4. 必须舍弃：具体对话、环境描写、重复的日常琐事、已经被后续发展覆盖的细节。
5. 用第三人称写，不要出现「摘要」「以下是」这类元话语。

直接输出摘要正文。"""


def fold_history(
    client: DeepSeekClient,
    history: HistoryLog,
    world: WorldDocument | None,
    *,
    on_usage: Callable[[str, dict, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> bool:
    """把积累的近期经历折叠进长期摘要。

    失败时退回本地的兜底折叠 —— 摘要的膨胀控制不能依赖网络是否可用。
    返回是否成功用 AI 生成（False 表示走了兜底）。
    """
    pending = history.pending_text()
    if not pending.strip():
        history.compact_locally()
        return False

    messages = [
        {"role": "system", "content": FOLD_SYSTEM},
        {
            "role": "user",
            "content": (
                f"{world_block(world)}\n\n"
                f"{pending}\n\n"
                "请输出合并后的新摘要。"
            ),
        },
    ]

    try:
        result = client.stream_chat(
            messages,
            should_stop=should_stop,
            max_tokens=FOLD_MAX_TOKENS,
            temperature=0.3,
        )
    except ApiError as exc:
        LOG.warn("摘要", f"折叠调用失败，改用本地兜底：{exc.message}")
        history.compact_locally()
        return False

    if on_usage is not None and result.usage:
        on_usage(result.model, result.usage, "摘要折叠")

    summary = (result.content or "").strip()
    if not summary:
        LOG.warn("摘要", "折叠返回为空，改用本地兜底")
        history.compact_locally()
        return False

    history.apply_compacted(summary)
    LOG.info(
        "摘要",
        f"历史已折叠，摘要 {len(summary)} 字，本回合窗口清空",
    )
    return True


# ----------------------------------------------------------------------
# 主循环
# ----------------------------------------------------------------------


def run_turn(
    client: DeepSeekClient,
    world: WorldDocument | None,
    action: str,
    *,
    player: PlayerState,
    state: WorldState,
    history: HistoryLog,
    inventory: list[Item] | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    is_opening: bool = False,
    on_progress: Callable[[str], None] | None = None,
    on_usage: Callable[[str, dict, str], None] | None = None,
    on_delta: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> TurnResult:
    """执行一个回合。

    任何一个环节失败都会向上抛（ValidationExhausted / ApiError），
    由调用方决定怎么提示玩家 —— 主循环不吞异常。
    """
    inventory = list(inventory or [])

    def report(message: str) -> None:
        if on_progress is not None:
            on_progress(message)

    def report_usage(model: str, usage: dict, reason: str) -> None:
        if on_usage is not None and usage:
            on_usage(model, usage, reason)

    # ---- 1. 生成事件 ----
    report("世界正在回应你的行动…")
    event_result = generate_event(
        client,
        world,
        action,
        player=player,
        state=state,
        inventory=inventory,
        history=history,
        max_retries=max_retries,
        on_progress=on_progress,
        on_usage=report_usage,
        on_delta=on_delta,
        should_stop=should_stop,
    )
    event = event_result.event

    # ---- 2. 落状态 ----
    change_notes = apply_state_changes(event.changes, state, player)

    # ---- 3. 事件说玩家拿到了东西 → 现场生成道具 ----
    items_gained: list[Item] = []
    if not event.item_gain.is_empty():
        report(f"生成道具（{event.item_gain.trigger}）…")
        try:
            item_result = generate_items(
                client,
                world,
                ItemRequest(
                    trigger=event.item_gain.trigger,
                    count=event.item_gain.count,
                    scene=event.item_gain.hint,
                ),
                player=player,
                state=state,
                inventory=inventory,
                history=history,
                max_retries=max_retries,
                on_progress=on_progress,
                on_usage=report_usage,
                should_stop=should_stop,
            )
            items_gained = item_result.items
        except ApiError as exc:
            # 道具生成失败不该让整个回合作废 —— 事件本身已经成立了。
            # 玩家没拿到东西，但剧情继续推进。
            LOG.warn("道具", f"本回合道具生成失败，已跳过：{exc.message}")
            report("道具生成失败，本回合跳过")

    # ---- 4. 记入历史，攒够了就折叠 ----
    entry = event.narrative[:HISTORY_ENTRY_CHARS]
    needs_fold = history.record(f"玩家{action}。{entry}")

    history_folded = False
    if needs_fold:
        report("整理长期记忆…")
        history_folded = fold_history(
            client, history, world, on_usage=report_usage, should_stop=should_stop
        )

    return TurnResult(
        action=action,
        event=event,
        applied_doom_delta=event_result.applied_doom_delta,
        doom_note=event_result.doom_note,
        doom_reason=event.doom_reason,
        change_notes=change_notes,
        items_gained=items_gained,
        history_folded=history_folded,
        retries_used=event_result.retries_used,
        is_opening=is_opening,
    )


def run_opening(
    client: DeepSeekClient,
    world: WorldDocument | None,
    **kwargs,
) -> TurnResult:
    """开场。复用主循环，只是操作是合成的。"""
    return run_turn(client, world, OPENING_ACTION, is_opening=True, **kwargs)
