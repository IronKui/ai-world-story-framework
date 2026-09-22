"""核心数据模型。

阶段 1 只定义界面上要用到的形状；阶段 7（道具生成）、
阶段 4（存档序列化）会在此基础上扩展。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict

from core.doom import DoomState

#: 稀有度取值，由低到高。AI 生成结果会按这个集合做归一下
RARITY_LEVELS = ["普通", "精良", "稀有", "史诗", "传说"]


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Item:
    """一件道具。

    全部字段都由 AI 动态生成，框架不预设任何道具库。
    """

    name: str
    rarity: str = "普通"
    category: str = ""          # 类型：消耗品 / 武器 / 材料 / 信物 …
    description: str = ""       # 简短描述，列表里展示
    lore: str = ""              # 背景故事
    effect: str = ""            # 效果说明
    quantity: int = 1
    id: str = field(default_factory=new_id)
    #: 阶段 7 会记录来源场景，便于回溯
    source: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Item":
        """宽松构造：多余字段忽略，缺失字段走默认值，避免旧存档读不进来。"""
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class WorldState:
    """世界状态简览面板展示的内容，也是驱动后续 AI 生成的核心上下文。"""

    location: str = "未知"
    time: str = "未知"
    #: [{ "name": "北境王庭", "relation": "敌对" }, ...]
    factions: list[dict] = field(default_factory=list)
    #: 由玩家行为累积出来的自由描述标签
    flags: list[str] = field(default_factory=list)
    #: 毁灭进度。「禁止随机触发世界毁灭」这条约束就挂在这上面
    doom: DoomState = field(default_factory=DoomState)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WorldState":
        if not isinstance(data, dict):
            return cls()

        allowed = set(cls.__dataclass_fields__) - {"doom"}
        state = cls(**{k: v for k, v in data.items() if k in allowed})

        # doom 是嵌套对象，不能用通用的字段过滤构造
        state.doom = DoomState.from_dict(data.get("doom"))

        # 容器类型兜底，避免旧存档或手改过的档把界面渲染炸了
        if not isinstance(state.factions, list):
            state.factions = []
        if not isinstance(state.flags, list):
            state.flags = []
        return state


# 势力关系的显示颜色属于外观，已移到 ui/styles.py 的 relation_color()，
# 那样切主题时才会跟着变。
