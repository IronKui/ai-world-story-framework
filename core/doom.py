"""毁灭进度。

需求硬性约束：**禁止随机触发世界毁灭**。毁灭类结局必须由玩家持续
一系列重大选择层层铺垫后才可能出现，不能随机突发世界崩塌。

落实方式是三层，缺一不可：

  1. 进度只有靠玩家的重大选择才会累积，且每个等级都有明确的
     「本回合允许写到什么程度」的描写上限，写进生成提示词
  2. AI 提议的增量必须给出理由，**没有理由的增量直接丢弃** ——
     防止模型某次抽风把进度直接推满
  3. 每次推进都留下一条「履历」，记录是哪个选择推动的。最终结局
     是从这份履历里讲出来的，而不是凭空生成一段末日

所以毁灭是「挣来的」：从 0 到 5 至少需要玩家连续做出 5 次被判定为
重大且不可逆的选择。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

#: 满级即进入毁灭结局
DOOM_MAX = 5

#: 单回合允许的最大增量。即使模型返回更大的值也会被压到这里
MAX_DELTA_PER_TURN = 1

#: 每个等级的说明，以及「本回合允许写到什么程度」。
#: ceiling 会原样进提示词，措辞必须明确 —— 含糊的约束模型不会遵守。
DOOM_LEVELS: dict[int, dict[str, str]] = {
    0: {
        "name": "平静",
        "ceiling": (
            "世界处于常态。禁止出现任何世界级灾难的描写、预言或暗示，"
            "连「不祥的预感」这类措辞也不要写。"
            "本回合只能写日常事件、人际纠纷、局部的小麻烦。"
        ),
    },
    1: {
        "name": "微澜",
        "ceiling": (
            "可以出现局部异常，例如矿脉减产、某地失踪了几个人、"
            "市井间的流言。禁止提及任何横跨大陆的威胁。"
        ),
    },
    2: {
        "name": "暗流",
        "ceiling": (
            "可以出现区域性灾祸与势力间的激烈动作，人物可以议论"
            "「上面在谋划大事」。但仍禁止直接描写世界毁灭、"
            "以及任何不可逆的大规模破坏。"
        ),
    },
    3: {
        "name": "危兆",
        "ceiling": (
            "可以出现明确的不祥征兆：冰墙裂痕扩大、拾灰人开始集体迁徙、"
            "初火山方向的地鸣。可以出现关于毁灭的传言与警告，"
            "但只能是传言与警告，不能真的发生。"
        ),
    },
    4: {
        "name": "临界",
        "ceiling": (
            "毁灭已迫在眉睫。可以描写清晰的、正在逼近的毁灭征兆，"
            "人物的绝望与抉择。但**本回合仍不得发生毁灭本身** —— "
            "只能是前兆。毁灭只允许在进度满级的回合发生。"
        ),
    },
    5: {
        "name": "终局",
        "ceiling": (
            "毁灭的条件已经齐备。本回合可以发生世界毁灭的结局，"
            "且结局必须直接源自玩家此前的一系列选择，"
            "在文本中体现出这条因果链，而不是突然崩塌。"
        ),
    },
}


def ceiling_for(level: int) -> str:
    bounded = max(0, min(int(level), DOOM_MAX))
    return DOOM_LEVELS[bounded]["ceiling"]


def level_name(level: int) -> str:
    bounded = max(0, min(int(level), DOOM_MAX))
    return DOOM_LEVELS[bounded]["name"]


@dataclass
class DoomState:
    """毁灭进度 + 推动它的选择履历。"""

    level: int = 0
    #: 每次推进记录一条：是哪次选择、为什么。结局从这份履历里讲出来
    evidence: list[str] = field(default_factory=list)

    # ---------- 判定 ----------

    @property
    def at_max(self) -> bool:
        return self.level >= DOOM_MAX

    @property
    def ceiling(self) -> str:
        return ceiling_for(self.level)

    @property
    def name(self) -> str:
        return level_name(self.level)

    def progress_text(self) -> str:
        return f"{self.level}/{DOOM_MAX}（{self.name}）"

    def describe(self) -> str:
        """给提示词用的完整描述。"""
        lines = [f"当前毁灭进度：{self.progress_text()}"]
        lines.append(f"本回合的描写上限：{self.ceiling}")

        if self.evidence:
            lines.append(
                "已经发生过的重大选择（毁灭进度由此累积，"
                "结局必须与它们形成因果）："
            )
            lines.extend(f"  {i}. {item}" for i, item in enumerate(self.evidence, 1))
        else:
            lines.append("目前还没有任何推动毁灭的重大选择。")

        return "\n".join(lines)

    # ---------- 推进 ----------

    def advance(self, delta: int, reason: str) -> tuple[int, str]:
        """按 AI 的提议推进进度。

        返回 (实际生效的增量, 说明)。增量为 0 时说明里会写原因，
        供调试日志排查模型是否在乱推。

        这里是最关键的守门逻辑：**没有理由的增量一律丢弃**。
        """
        if delta == 0:
            return 0, ""

        if self.at_max:
            return 0, "毁灭进度已满，不再累积"

        # 没有理由 = 模型在无凭无据地推进，直接拒绝
        if not (reason or "").strip():
            return 0, "提议推进但没有给出理由，已丢弃"

        # 单回合增量封顶
        applied = min(int(delta), MAX_DELTA_PER_TURN)
        if applied <= 0:
            return 0, f"增量 {delta} 不在有效范围，已丢弃"

        before = self.level
        self.level = min(self.level + applied, DOOM_MAX)
        applied = self.level - before

        if applied > 0:
            self.evidence.append(reason.strip())

        return applied, f"毁灭进度 {before} → {self.level}"

    def reset(self) -> None:
        self.level = 0
        self.evidence.clear()

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data) -> "DoomState":
        if not isinstance(data, dict):
            return cls()

        state = cls()
        try:
            state.level = max(0, min(int(data.get("level", 0)), DOOM_MAX))
        except (TypeError, ValueError):
            state.level = 0

        evidence = data.get("evidence")
        if isinstance(evidence, list):
            state.evidence = [str(item) for item in evidence if str(item).strip()]

        return state
