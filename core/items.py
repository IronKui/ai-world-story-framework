"""AI 动态道具生成。

框架里**没有任何预设道具表**，道具全部在游玩过程中由 AI 现场生成。

输入上下文：世界观 + 当前世界状态 + 玩家位置与属性 + 玩家背包。
输出结构：名称、描述、背景故事、类型、效果、稀有度。

两条质量闸门，任一不过就带着原因重试（最多 3 次）：
  1. JSON 解析 —— 结构不对直接重试，不浪费一次校验调用
  2. 世界观校验 —— 解析通过后再交给阶段 6 的校验链路
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from core.api_client import DeepSeekClient
from core.models import RARITY_LEVELS, Item, WorldState
from core.prompts import JSON_REQUIREMENT, context_block
from core.savegame import HistoryLog, PlayerState
from core.validator import (
    DEFAULT_MAX_RETRIES,
    GenerationAttempt,
    GuardedResult,
    guarded_generate,
)
from core.world import WorldDocument

# ----------------------------------------------------------------------
# 触发场景
# ----------------------------------------------------------------------

TRIGGER_EXPLORE = "探索"
TRIGGER_CHEST = "开箱"
TRIGGER_REWARD = "战斗奖励"
TRIGGER_TRADE = "交易"
TRIGGER_GIFT = "赠予"
TRIGGER_MANUAL = "手动生成"

#: 触发场景 → 给 AI 的场景说明。写法会影响道具的合理性：
#: 「开箱」该出容器里的东西，「赠予」该带人情往来，不能都写成捡到宝
TRIGGER_HINTS: dict[str, str] = {
    TRIGGER_EXPLORE: "玩家在探索途中发现了它",
    TRIGGER_CHEST: "玩家打开了一个容器，里面的东西已被存放了很久",
    TRIGGER_REWARD: "玩家结束了一场冲突，这是从中得到的东西",
    TRIGGER_TRADE: "玩家完成了一笔交易，这是交易所得的货物",
    TRIGGER_GIFT: "有人把它送给了玩家，物件本身应带有人情往来的痕迹",
    TRIGGER_MANUAL: "需要为当前场景补充一件合乎情理的物件",
}

ALL_TRIGGERS = list(TRIGGER_HINTS.keys())

#: 单次生成的数量上限
MAX_ITEMS_PER_CALL = 5

#: 生成用的 max_tokens。一件道具有描述 + 背景，给足空间
GENERATE_MAX_TOKENS = 2000

#: 各字段的长度上限，防止模型写出长篇大论把存档撑爆
LIMITS = {
    "name": 40,
    "category": 16,
    "description": 200,
    "lore": 500,
    "effect": 200,
}


# ----------------------------------------------------------------------
# 提示词
# ----------------------------------------------------------------------

ITEM_SYSTEM = f"""你是一个文字游戏的道具生成器。根据给定的世界观设定与当前局势，现场生成道具。

硬性要求：
1. 道具必须完全符合世界观。名称、材质、用途都要能在这个世界里存在。
   如果世界观里没有某种技术或材料，就不要让道具依赖它。
2. 道具要有具体感：谁做的、为什么做、怎么流落到此。避免「神秘的力量」这类空话。
3. 反面例子：
   - 与世界观规则冲突（世界观说灰晶遇水失效，却生成储水灰晶壶）
   - 空泛无信息（「一把普通的剑」「神秘的宝石」）
   - 现代或异世界词汇混入（「能量电池」「魔法阵」出现在没有这些概念的世界里）
4. 稀有度按物件在这个世界里的罕见程度来定，不要全是稀有以上。
   普通与精良应当占多数。

{JSON_REQUIREMENT}

输出格式：
{{
  "items": [
    {{
      "name": "道具名称",
      "category": "类型，如 消耗品 / 武器 / 材料 / 信物 / 文献 / 杂物",
      "rarity": "普通 / 精良 / 稀有 / 史诗 / 传说",
      "description": "一到两句话的外观与用途描述",
      "lore": "背景故事：来历、经手过谁、有什么传闻",
      "effect": "实际效果，用这个世界的话语描述"
    }}
  ]
}}"""


@dataclass
class ItemRequest:
    """一次道具生成请求的上下文。"""

    trigger: str = TRIGGER_EXPLORE
    count: int = 1
    #: 额外的场景说明，例如「在码头帮的仓库里」
    scene: str = ""
    #: 稀有度偏好，留空则不限制
    rarity_hint: str = ""
    #: 是否允许生成与当前局势无关的纯氛围物件
    allow_filler: bool = False

    def describe(self) -> str:
        lines = [f"触发场景：{self.trigger}（{TRIGGER_HINTS.get(self.trigger, '')}）"]
        lines.append(f"需要生成 {self.count} 件道具")
        if self.scene:
            lines.append(f"具体情境：{self.scene}")
        if self.rarity_hint:
            lines.append(
                f"稀有度倾向：以「{self.rarity_hint}」为主，"
                "但不必全部都是这一档"
            )
        return "\n".join(lines)


def build_item_prompt(
    world: WorldDocument | None,
    request: ItemRequest,
    *,
    player: PlayerState | None = None,
    state: WorldState | None = None,
    inventory: list[Item] | None = None,
    history: HistoryLog | None = None,
) -> str:
    """组装道具生成请求。

    背包必须带上：没有它，AI 会反复生成玩家已经有的东西。
    """
    blocks = [context_block(world, player, state, history)]

    blocks.append(f"【本次任务】\n{request.describe()}")
    blocks.append(_inventory_block(inventory))

    return "\n\n".join(b for b in blocks if b.strip())


def _inventory_block(inventory: list[Item] | None) -> str:
    if not inventory:
        return "【玩家当前背包】\n（空）\n请勿生成玩家已经持有的道具。"

    lines = [
        f"- {item.name}（{item.rarity}·{item.category or '未分类'}）"
        + (f" ×{item.quantity}" if item.quantity > 1 else "")
        for item in inventory[:40]
    ]
    more = f"\n（另有 {len(inventory) - 40} 件未列出）" if len(inventory) > 40 else ""
    return (
        "【玩家当前背包】\n"
        + "\n".join(lines)
        + more
        + "\n请勿生成与上述道具重复或高度相似的东西。"
    )


# ----------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------


def parse_items(raw: str) -> tuple[list[Item], str]:
    """把模型返回解析成 Item 列表。

    返回 (道具列表, 错误说明)。错误说明非空表示这一轮没生成出可用结构，
    调用方应带着它重试，而不是拿这段文本去跑世界观校验。
    """
    payload = _load_json(raw)
    if payload is None:
        return [], "返回的不是合法 json"

    entries = payload.get("items")
    if isinstance(entries, dict):
        entries = [entries]  # 模型偶尔只给一个对象而不是数组
    if not isinstance(entries, list):
        return [], "json 中缺少 items 数组"
    if not entries:
        return [], "items 数组为空"

    items: list[Item] = []
    skipped: list[str] = []

    for entry in entries:
        if not isinstance(entry, dict):
            skipped.append("存在不是对象的条目")
            continue

        name = str(entry.get("name") or "").strip()
        if not name:
            skipped.append("有条目缺少 name 字段")
            continue

        items.append(
            Item(
                name=name[: LIMITS["name"]],
                rarity=_normalize_rarity(entry.get("rarity")),
                category=_clip(entry.get("category"), LIMITS["category"]),
                description=_clip(entry.get("description"), LIMITS["description"]),
                lore=_clip(entry.get("lore"), LIMITS["lore"]),
                effect=_clip(entry.get("effect"), LIMITS["effect"]),
                source="AI 生成",
            )
        )

    if not items:
        return [], "；".join(skipped) or "没有任何可用的道具条目"

    return items, ""


def _load_json(raw: str) -> dict | None:
    """宽容地取出 json 对象。

    即使开了 JSON 模式，模型偶尔仍会包 ```json 或加前后说明。
    """
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


#: 模型可能写出的前缀，例如「稀有度：史诗」
_RARITY_PREFIX = re.compile(
    r"^\s*(稀有度|稀有级别|品质|等级|rarity)\s*[:：]\s*", re.IGNORECASE
)


def _normalize_rarity(value) -> str:
    """把模型给的稀有度归一到框架的五档。

    模型常写「稀有度：史诗」「Legendary」这类形式。识别不出来一律兜到
    「普通」—— 宁可低估，也不要让界面拿到无法着色的值。

    注意不能简单地按顺序找第一个「包含」的档位：
    「稀有度：史诗」里「稀有度」这三个字本身含「稀有」，
    顺序遍历会先命中「稀有」而把真正的「史诗」丢掉。
    所以先剥前缀，再取出现位置最靠后的那个档位。
    """
    text = str(value or "").strip()
    if not text:
        return "普通"

    cleaned = _RARITY_PREFIX.sub("", text).strip()

    # 精确匹配优先
    for level in RARITY_LEVELS:
        if cleaned == level:
            return level

    # 包含匹配：取最靠后的一个
    best, best_pos = "", -1
    for level in RARITY_LEVELS:
        position = cleaned.rfind(level)
        if position > best_pos:
            best, best_pos = level, position
    if best:
        return best

    lowered = cleaned.lower()
    for alias, level in (
        ("legend", "传说"), ("myth", "传说"),
        ("epic", "史诗"),
        ("rare", "稀有"),
        ("uncommon", "精良"), ("good", "精良"),
        ("common", "普通"), ("normal", "普通"),
    ):
        if alias in lowered:
            return level

    return "普通"


def _clip(value, limit: int) -> str:
    """截断到 limit 个字符（含省略号在内）。"""
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    # 省略号本身占一个字符，所以正文只留 limit - 1
    return text[: limit - 1].rstrip() + "…"


# ----------------------------------------------------------------------
# 生成
# ----------------------------------------------------------------------


@dataclass
class ItemGenerationResult:
    """一次道具生成的结果。"""

    items: list[Item] = field(default_factory=list)
    attempts: list[GenerationAttempt] = field(default_factory=list)
    request: ItemRequest = field(default_factory=ItemRequest)

    @property
    def retries_used(self) -> int:
        return max(len(self.attempts) - 1, 0)

    @property
    def had_conflicts(self) -> bool:
        return self.retries_used > 0

    @property
    def final_verdict(self):
        from core.validator import ValidationVerdict

        return self.attempts[-1].verdict if self.attempts else ValidationVerdict()


def generate_items(
    client: DeepSeekClient,
    world: WorldDocument | None,
    request: ItemRequest,
    *,
    player: PlayerState | None = None,
    state: WorldState | None = None,
    inventory: list[Item] | None = None,
    history: HistoryLog | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    on_progress: Callable[[str], None] | None = None,
    on_attempt: Callable[[GenerationAttempt], None] | None = None,
    on_delta: Callable[[str], None] | None = None,
    on_usage: Callable[[str, dict, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ItemGenerationResult:
    """生成道具，带解析与世界观双重把关。

    连续失败时抛 ValidationExhausted，调用方负责弹窗。
    """
    base_prompt = build_item_prompt(
        world, request, player=player, state=state, inventory=inventory, history=history
    )

    def report_usage(model: str, usage: dict, reason: str) -> None:
        if on_usage is not None and usage:
            on_usage(model, usage, reason)

    def generate(feedback: list[str]) -> str:
        from core.prompts import retry_note

        messages = [
            {"role": "system", "content": ITEM_SYSTEM},
            {
                "role": "user",
                "content": f"{base_prompt}{retry_note(feedback)}",
            },
        ]
        result = client.stream_chat(
            messages,
            on_delta=on_delta,
            should_stop=should_stop,
            json_mode=True,
            max_tokens=GENERATE_MAX_TOKENS,
            temperature=1.0,
        )
        report_usage(result.model, result.usage, "道具生成")
        return result.content

    guarded: GuardedResult = guarded_generate(
        client,
        world,
        generate=generate,
        parse=parse_items,
        render_for_validation=describe_items_for_validation,
        kind="道具",
        max_retries=max_retries,
        on_progress=on_progress,
        on_attempt=on_attempt,
        on_usage=on_usage,
        should_stop=should_stop,
    )

    items = list(guarded.parsed or [])

    # 模型给多了就截断到请求数量，给少了不强补
    if len(items) > request.count:
        items = items[: request.count]

    return ItemGenerationResult(
        items=items, attempts=guarded.attempts, request=request
    )


# ----------------------------------------------------------------------
# 校验文本
# ----------------------------------------------------------------------


def describe_items_for_validation(items: list[Item]) -> str:
    """把道具列表拼成给校验模型看的文本。

    逐字段列出，比让校验模型去读 json 更不容易漏看。
    """
    blocks: list[str] = []
    for index, item in enumerate(items, 1):
        blocks.append(
            f"{index}. 名称：{item.name}\n"
            f"   类型：{item.category or '未标注'}\n"
            f"   稀有度：{item.rarity}\n"
            f"   描述：{item.description or '（无）'}\n"
            f"   背景：{item.lore or '（无）'}\n"
            f"   效果：{item.effect or '（无）'}"
        )
    return "\n\n".join(blocks)
