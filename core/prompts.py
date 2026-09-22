"""提示词组装。

阶段 6/7/8/9 都要把「世界观 + 世界状态 + 玩家 + 历史摘要」拼进请求，
这里统一收口。各模块不自己拼上下文，否则同一份世界状态会出现
好几种写法，AI 收到的信息也就跟着漂移。
"""

from __future__ import annotations

from core.savegame import HistoryLog, PlayerState
from core.models import WorldState
from core.world import DEFAULT_CONTEXT_BUDGET, WorldDocument

#: JSON 模式的硬性要求：prompt 里必须出现 "json" 字样，
#: 否则 DeepSeek 会直接报错。下面每段要求 JSON 输出的提示都带上了。
JSON_REQUIREMENT = (
    "你必须只输出一个 json 对象，不要输出任何解释文字、"
    "不要用 markdown 代码块包裹。"
)


def world_block(
    world: WorldDocument | None, budget: int = DEFAULT_CONTEXT_BUDGET
) -> str:
    """世界观设定段。这是全局硬性规则。"""
    if world is None:
        return (
            "【世界观设定】\n"
            "（玩家尚未导入世界观文档。你可以自由发挥，但请保持设定自洽，"
            "不要在后续内容中自我矛盾。）"
        )

    text, compressed = world.context_text(budget)
    header = f"【世界观设定（硬性规则，必须严格遵守）：{world.name}】"

    note = ""
    if compressed and not world.summary.strip():
        note = (
            f"\n（原文较长已按章节压缩，完整设定共 {world.char_count} 字。"
            "下方保留的标题代表你尚未看到的小节存在，如需引用请保守处理。）"
        )

    return f"{header}{note}\n{text}"


def state_block(state: WorldState | None) -> str:
    """当前世界状态段。"""
    if state is None:
        return ""

    lines: list[str] = []
    if state.location:
        lines.append(f"当前位置：{state.location}")
    if state.time:
        lines.append(f"世界时间：{state.time}")

    if state.factions:
        relations = "；".join(
            f"{f.get('name', '未知势力')}（{f.get('relation', '中立')}）"
            for f in state.factions
        )
        lines.append(f"势力关系：{relations}")

    if state.flags:
        lines.append("世界印记：" + "；".join(state.flags))

    if not lines:
        return ""

    return "【当前世界状态】\n" + "\n".join(lines)


def player_block(player: PlayerState | None) -> str:
    """玩家段。"""
    if player is None:
        return ""

    lines = [f"姓名：{player.name or '无名者'}"]

    if player.attributes:
        attrs = "；".join(f"{k} {v}" for k, v in player.attributes.items())
        lines.append(f"属性：{attrs}")

    if player.status:
        lines.append("当前状态：" + "；".join(player.status))

    if player.notes:
        lines.append(f"处境：{player.notes}")

    return "【玩家】\n" + "\n".join(lines)


def history_block(history: HistoryLog | None) -> str:
    """历史摘要段。"""
    if history is None or history.is_empty():
        return ""

    text = history.context_text()
    return f"【玩家的过往经历】\n{text}" if text else ""


def context_block(
    world: WorldDocument | None,
    player: PlayerState | None = None,
    state: WorldState | None = None,
    history: HistoryLog | None = None,
    *,
    budget: int = DEFAULT_CONTEXT_BUDGET,
) -> str:
    """把上述各段拼成完整的上下文，空段自动省略。"""
    blocks = [
        world_block(world, budget),
        state_block(state),
        player_block(player),
        history_block(history),
    ]
    return "\n\n".join(block for block in blocks if block.strip())


def retry_note(problems: list[str]) -> str:
    """重试时把上一次的问题回喂给模型。

    不带这个的话重试等于重新抽卡，很可能又踩同一个坑；
    带上具体问题，模型才有机会定向修正。

    problems 既可能是世界观冲突点（阶段 6），
    也可能是输出格式错误（阶段 7 起的结构化生成）。
    """
    if not problems:
        return ""

    bullets = "\n".join(f"  {i}. {problem}" for i, problem in enumerate(problems, 1))
    return (
        "\n\n【重要：上一次生成未通过】\n"
        f"问题：\n{bullets}\n"
        "请重新生成，务必避开上述问题，其余部分可以保持相似的思路。"
    )
