"""AI 动态事件生成。

框架里**没有预设事件池**。玩家每次操作后，AI 结合世界观、世界状态、
玩家历史与当前局面，实时生成事件文本、NPC 和行动选项。

毁灭约束的落实见 core/doom.py —— 这里是它的执行端：
生成前把「本回合描写上限」写进提示词，生成后由框架自己决定
是否接受 AI 提议的毁灭增量（没有理由的增量直接丢弃），
并且额外跑一遍毁灭专项校验。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from core.api_client import DeepSeekClient
from core.doom import DOOM_MAX, DoomState
from core.models import WorldState
from core.prompts import JSON_REQUIREMENT, context_block
from core.savegame import HistoryLog, PlayerState
from core.validator import (
    DEFAULT_MAX_RETRIES,
    GenerationAttempt,
    guarded_generate,
)
from core.world import WorldDocument

#: 事件生成给多少 token。事件文本 + NPC 对话 + 若干选项 + 状态变更
GENERATE_MAX_TOKENS = 2400

#: 选项数量范围
MIN_OPTIONS = 2
MAX_OPTIONS = 5

#: 各字段长度上限
LIMITS = {
    "narrative": 1200,
    "npc": 40,
    "npc_dialog": 300,
    "option": 120,
    "flag": 60,
    "status": 40,
    "notes": 200,
}

#: 势力关系的合法取值，与 core.models.RELATION_COLORS 对齐
VALID_RELATIONS = {"盟友", "友好", "中立", "疏远", "敌对", "死敌"}


# ----------------------------------------------------------------------
# 提示词
# ----------------------------------------------------------------------

EVENT_SYSTEM = f"""你是一个文字游戏的实时事件生成器。玩家的上一次操作已经发生，请描写其结果并推进局面。

要求：
1. 严格不违背世界观设定，但可以合理延伸设定中未提及的部分。
2. 事件要承接玩家刚才的操作 —— 玩家做了什么，世界就回应什么。
   不要写出与玩家操作无关的突发事件。
3. 文本要有具体感：具体的地点、人物、声响、气味。不要空泛。
4. **必须分段**。这是一款被当作「可互动小说」阅读的游戏，读者在看
   一整屏密密麻麻的文字时会直接跳过。要求：
     · 每段只写一到两句话，**每段不超过 40 个汉字**
     · 段与段之间用空行分隔（JSON 里写成 \n\n）
     · 一次叙事拆成 3~8 个自然段，不要写成一大坨
     · 对话、动作、环境描写各占一段，不要把几件事塞进同一段
5. 选项要彼此差异化，代表不同的立场或风险，不要是同一件事的三种说法。
   其中可以包含一两个明显危险或激进的选项。
6. 状态变更只写真正发生了变化的项，没变的一律留空。
7. 【重要】玩家的状态列表是**累加**的，不会自动清理。所以一旦新状态
   取代了旧状态，必须把旧状态原样写进 player_status_remove，否则列表里
   会同时留着互相矛盾的条目（例如「干渴」和「喝足了水」并存），
   进而污染后续所有生成。宁可多删也不要留下已被取代的旧状态。

{JSON_REQUIREMENT}

输出格式：
{{
  "narrative": "事件文本，描写玩家操作之后的局面。200~400 字，拆成 3~8 个自然段，每段不超过 40 字，段间用 \n\n 分隔",
  "npc": "本回合出现的关键人物姓名；没有则空字符串",
  "npc_dialog": "该人物说的一句话；没有则空字符串",
  "options": ["行动选项1", "行动选项2", "行动选项3"],
  "doom_delta": 0,
  "doom_reason": "若 doom_delta 大于 0，说明玩家的什么选择推动了毁灭进度；否则空字符串",
  "item_gain": {{
    "trigger": "仅当玩家本回合确实获得了东西时填写：探索 / 开箱 / 战斗奖励 / 交易 / 赠予；否则空字符串",
    "count": 1,
    "hint": "物件所在的具体位置或来源，例如「铁匣夹层」「死者腰间」"
  }},
  "state_changes": {{
    "location": "位置变化后的名称；未变化则空字符串",
    "time": "时间推进后的描述；未变化则空字符串",
    "faction_changes": [{{"name": "势力名", "relation": "盟友/友好/中立/疏远/敌对/死敌"}}],
    "add_flags": ["本回合新产生的世界印记"],
    "player_status_add": ["玩家新获得或发生变化的状态"],
    "player_status_remove": ["被新状态取代、因而不该再保留的旧状态（必须填，见要求 7）"],
    "player_notes": "一句话概括玩家当前处境；没变化则空字符串"
  }}
}}"""


#: 毁灭专项校验规则。作为 extra_rules 追加到阶段 6 的校验请求里。
#: 单独写一条，是因为通用校验只关心「是否违背世界观设定」，
#: 而毁灭约束是玩法层面的硬性限制，与设定是否自洽无关。
def doom_validation_rule(level: int) -> str:
    from core.doom import ceiling_for, level_name

    return (
        "【额外的硬性规则：毁灭约束】\n"
        f"本次内容生成时，毁灭进度为 {level}/{DOOM_MAX}（{level_name(level)}）。\n"
        f"允许的描写上限：{ceiling_for(level)}\n\n"
        "请判断「待校验内容」是否**超出**了上述上限。具体地：\n"
        f"  · 若进度不足 {DOOM_MAX}，内容中出现了世界毁灭、大陆级不可逆灾难"
        "已经发生或正在发生的描写 → 判为冲突\n"
        f"  · 若进度为 0，内容中出现任何世界级威胁的暗示或预言 → 判为冲突\n"
        "  · 仅仅是紧张局势、局部灾祸、角色口中的传言（且进度允许） → 不算冲突\n"
        "此项只判是否越界，不要因为别的理由判冲突。"
    )


def build_event_prompt(
    world: WorldDocument | None,
    action: str,
    *,
    player: PlayerState | None = None,
    state: WorldState | None = None,
    inventory: list | None = None,
    history: HistoryLog | None = None,
) -> str:
    """组装事件生成请求。"""
    blocks = [context_block(world, player, state, history)]

    doom = state.doom if state is not None else DoomState()
    blocks.append(f"【毁灭进度约束】\n{doom.describe()}")

    blocks.append(
        f"【玩家刚才的操作】\n{action}\n\n"
        "请描写这一操作带来的结果与随之而来的局面。"
    )

    if inventory:
        names = "、".join(
            f"{item.name}（{item.rarity}）" for item in inventory[:20]
        )
        blocks.append(f"【玩家背包】\n{names}")

    return "\n\n".join(b for b in blocks if b.strip())


# ----------------------------------------------------------------------
# 事件结构
# ----------------------------------------------------------------------


@dataclass
class StateChanges:
    """AI 建议的世界状态变更。全部可选，未变化的字段留空。"""

    location: str = ""
    time: str = ""
    faction_changes: list[dict] = field(default_factory=list)
    add_flags: list[str] = field(default_factory=list)
    player_status_add: list[str] = field(default_factory=list)
    player_status_remove: list[str] = field(default_factory=list)
    player_notes: str = ""

    def is_empty(self) -> bool:
        return not any(
            [
                self.location,
                self.time,
                self.faction_changes,
                self.add_flags,
                self.player_status_add,
                self.player_status_remove,
                self.player_notes,
            ]
        )

    def to_dict(self) -> dict:
        return {
            "location": self.location,
            "time": self.time,
            "faction_changes": self.faction_changes,
            "add_flags": self.add_flags,
            "player_status_add": self.player_status_add,
            "player_status_remove": self.player_status_remove,
            "player_notes": self.player_notes,
        }


@dataclass
class ItemGain:
    """本回合玩家获得了道具的指示。

    AI 只负责说「这里该有东西」并给出场景类型，
    真正的道具由阶段 7 的生成器依据世界观现场造出来。
    这样事件与道具不会各说各话。
    """

    trigger: str = ""
    count: int = 1
    hint: str = ""

    def is_empty(self) -> bool:
        return not self.trigger.strip()


@dataclass
class GameEvent:
    """一个实时生成的事件。"""

    narrative: str
    options: list[str] = field(default_factory=list)
    npc: str = ""
    npc_dialog: str = ""
    #: AI 提议的毁灭推进增量（尚未被框架接受）
    doom_delta: int = 0
    doom_reason: str = ""
    changes: StateChanges = field(default_factory=StateChanges)
    #: 本回合的获得道具指示，空表示没有
    item_gain: ItemGain = field(default_factory=ItemGain)

    def has_npc(self) -> bool:
        return bool(self.npc.strip())


@dataclass
class EventGenerationResult:
    """一次事件生成的结果。"""

    event: GameEvent
    attempts: list[GenerationAttempt] = field(default_factory=list)
    #: 框架实际接受的毁灭增量（可能小于 AI 提议的）
    applied_doom_delta: int = 0
    #: 未接受时说明原因，写进调试日志
    doom_note: str = ""

    @property
    def retries_used(self) -> int:
        return max(len(self.attempts) - 1, 0)

    @property
    def had_conflicts(self) -> bool:
        return self.retries_used > 0

    @property
    def doom_rejected(self) -> bool:
        """AI 提议了推进，但被框架拦下了。"""
        return self.event.doom_delta > 0 and self.applied_doom_delta == 0


# ----------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------


def parse_event(raw: str) -> tuple[GameEvent | None, str]:
    """解析事件 json。返回 (事件, 错误说明)。"""
    payload = _load_json(raw)
    if payload is None:
        return None, "返回的不是合法 json"

    narrative = str(payload.get("narrative") or "").strip()
    if not narrative:
        return None, "缺少 narrative 字段或事件文本为空"

    options = _parse_options(payload.get("options"))
    if len(options) < MIN_OPTIONS:
        return None, f"行动选项少于 {MIN_OPTIONS} 个"

    return (
        GameEvent(
            narrative=_clip_text(narrative, LIMITS["narrative"]),
            options=options,
            npc=_clip(payload.get("npc"), LIMITS["npc"]),
            npc_dialog=_clip(payload.get("npc_dialog"), LIMITS["npc_dialog"]),
            doom_delta=_parse_int(payload.get("doom_delta")),
            doom_reason=_clip(payload.get("doom_reason"), LIMITS["flag"]),
            changes=_parse_changes(payload.get("state_changes")),
            item_gain=_parse_item_gain(payload.get("item_gain")),
        ),
        "",
    )


def _parse_item_gain(value) -> ItemGain:
    """解析获得道具的指示。

    触发器不在白名单里就当作没有 —— 否则会把未知类型丢给道具生成器，
    生成出的东西和事件场景对不上。
    """
    from core.items import MAX_ITEMS_PER_CALL, TRIGGER_HINTS

    if not isinstance(value, dict):
        return ItemGain()

    trigger = str(value.get("trigger") or "").strip()
    if trigger not in TRIGGER_HINTS:
        return ItemGain()

    count = _parse_int(value.get("count")) or 1
    count = max(1, min(count, MAX_ITEMS_PER_CALL))

    return ItemGain(
        trigger=trigger,
        count=count,
        hint=_clip(value.get("hint"), LIMITS["flag"]),
    )


def _parse_options(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []

    options: list[str] = []
    for entry in value:
        text = " ".join(str(entry or "").split())
        if text:
            options.append(_clip(text, LIMITS["option"]))

    return options[:MAX_OPTIONS]


def _parse_changes(value) -> StateChanges:
    if not isinstance(value, dict):
        return StateChanges()

    changes = StateChanges(
        location=_clip(value.get("location"), LIMITS["npc"]),
        time=_clip(value.get("time"), LIMITS["npc"]),
        player_notes=_clip(value.get("player_notes"), LIMITS["notes"]),
    )

    raw_factions = value.get("faction_changes")
    if isinstance(raw_factions, list):
        for entry in raw_factions:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            relation = str(entry.get("relation") or "").strip()
            if not name:
                continue
            # 关系值不在白名单里就跳过，免得界面拿到无法着色的值
            if relation not in VALID_RELATIONS:
                continue
            changes.faction_changes.append(
                {"name": _clip(name, LIMITS["npc"]), "relation": relation}
            )

    changes.add_flags = _parse_str_list(value.get("add_flags"), LIMITS["flag"], 8)
    changes.player_status_add = _parse_str_list(
        value.get("player_status_add"), LIMITS["status"], 5
    )
    changes.player_status_remove = _parse_str_list(
        value.get("player_status_remove"), LIMITS["status"], 5
    )

    return changes


def _parse_str_list(value, limit: int, cap: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []

    result: list[str] = []
    for entry in value:
        text = " ".join(str(entry or "").split())
        if text and text not in result:
            result.append(_clip(text, limit))
    return result[:cap]


def _parse_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _load_json(raw: str) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None

    candidates = [text]
    if text.startswith("```"):
        stripped = text.strip("`")
        if "\n" in stripped:
            first, rest = stripped.split("\n", 1)
            if first.strip().lower() in ("json", ""):
                candidates.insert(0, rest)

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed

    return None


def _clip(value, limit: int) -> str:
    """压缩空白后截断。用于选项、姓名这类单行字段。"""
    return _truncate(" ".join(str(value or "").split()), limit)


def _clip_text(value, limit: int) -> str:
    """保留换行地截断。用于事件正文 —— 段落结构是叙事的一部分。"""
    return _truncate(str(value or "").strip(), limit)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def describe_event_for_validation(event: GameEvent) -> str:
    """把事件拼成给校验模型看的可读文本。"""
    lines = [f"事件文本：\n{event.narrative}"]

    if event.has_npc():
        lines.append(f"出现的人物：{event.npc}")
    if event.npc_dialog:
        lines.append(f"人物台词：{event.npc_dialog}")

    if event.options:
        lines.append("行动选项：")
        lines.extend(f"  {i}. {opt}" for i, opt in enumerate(event.options, 1))

    if event.doom_delta > 0:
        lines.append(
            f"（本次生成试图推进毁灭进度 {event.doom_delta} 级，"
            f"理由：{event.doom_reason or '未给出'}）"
        )

    if not event.item_gain.is_empty():
        lines.append(
            f"（本回合玩家获得道具：{event.item_gain.trigger}"
            f"，来源：{event.item_gain.hint or '未说明'}）"
        )

    changes = event.changes
    if changes.add_flags:
        lines.append(f"新增世界印记：{'、'.join(changes.add_flags)}")

    return "\n".join(lines)


# ----------------------------------------------------------------------
# 生成
# ----------------------------------------------------------------------


def generate_event(
    client: DeepSeekClient,
    world: WorldDocument | None,
    action: str,
    *,
    player: PlayerState | None = None,
    state: WorldState | None = None,
    inventory: list | None = None,
    history: HistoryLog | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    on_progress: Callable[[str], None] | None = None,
    on_attempt: Callable[[GenerationAttempt], None] | None = None,
    on_usage: Callable[[str, dict, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> EventGenerationResult:
    """生成事件。

    注意毁灭增量的处理方式：**AI 只是提议，框架来决定**。
    校验通过后由 DoomState.advance 复核，没有理由的增量会被丢弃。
    """
    doom = state.doom if state is not None else DoomState()
    base_prompt = build_event_prompt(
        world, action, player=player, state=state, inventory=inventory, history=history
    )
    rules = doom_validation_rule(doom.level)

    def generate(feedback: list[str]) -> str:
        from core.prompts import retry_note

        messages = [
            {"role": "system", "content": EVENT_SYSTEM},
            {"role": "user", "content": f"{base_prompt}{retry_note(feedback)}"},
        ]
        result = client.stream_chat(
            messages,
            should_stop=should_stop,
            json_mode=True,
            max_tokens=GENERATE_MAX_TOKENS,
            temperature=1.0,
        )
        if on_usage is not None and result.usage:
            on_usage(result.model, result.usage, "事件生成")
        return result.content

    guarded = guarded_generate(
        client,
        world,
        generate=generate,
        parse=parse_event,
        render_for_validation=describe_event_for_validation,
        kind="事件",
        max_retries=max_retries,
        on_progress=on_progress,
        on_attempt=on_attempt,
        on_usage=on_usage,
        extra_rules=rules,
        should_stop=should_stop,
    )

    event: GameEvent = guarded.parsed

    # 框架复核毁灭增量：AI 提议不算数
    applied, note = doom.advance(event.doom_delta, event.doom_reason)

    return EventGenerationResult(
        event=event,
        attempts=guarded.attempts,
        applied_doom_delta=applied,
        doom_note=note,
    )


# ----------------------------------------------------------------------
# 状态应用
# ----------------------------------------------------------------------


def apply_state_changes(
    changes: StateChanges,
    state: WorldState,
    player: PlayerState | None = None,
) -> list[str]:
    """把 AI 建议的变更落到世界状态上。

    只接受合法值，返回一条条「改了什么」的说明，用于剧情区提示。
    """
    notes: list[str] = []

    if changes.location.strip():
        state.location = changes.location.strip()
        notes.append(f"位置：{state.location}")

    if changes.time.strip():
        state.time = changes.time.strip()
        notes.append(f"时间：{state.time}")

    for change in changes.faction_changes:
        name = change["name"]
        relation = change["relation"]
        existing = next(
            (f for f in state.factions if f.get("name") == name), None
        )
        if existing is None:
            state.factions.append({"name": name, "relation": relation})
            notes.append(f"势力关系：{name} → {relation}")
        elif existing.get("relation") != relation:
            existing["relation"] = relation
            notes.append(f"势力关系：{name} → {relation}")

    for flag in changes.add_flags:
        if flag not in state.flags:
            state.flags.append(flag)
            notes.append(f"世界印记：{flag}")

    # 印记数量要有上限，否则长局会无限膨胀进 prompt
    if len(state.flags) > 20:
        state.flags = state.flags[-20:]

    if player is not None:
        for status in changes.player_status_add:
            if status not in player.status:
                player.status.append(status)
                notes.append(f"状态：{status}")

        for status in changes.player_status_remove:
            if status in player.status:
                player.status.remove(status)
                notes.append(f"状态解除：{status}")

        if len(player.status) > 10:
            player.status = player.status[-10:]

        if changes.player_notes.strip():
            player.notes = changes.player_notes.strip()

    return notes
