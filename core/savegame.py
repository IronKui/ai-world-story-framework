"""存档系统：玩家状态、历史摘要、多槽位 JSON 快照。

需求约束：
  · 存档保存「玩家历史精简摘要」，**不保存完整对话历史**
    否则 token 消耗会随游戏时长线性增长，玩久了上下文必然失控
  · 存档**不存储 API Key**（Key 只在 data/config.json）
  · 存档包含当前世界观副本，这样换过世界观文档也能读回旧档
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from core.models import Item, WorldState
from core.paths import SAVES_DIR
from core.storage import read_json, write_json_atomic
from core.world import WorldDocument

#: 存档格式版本。读到更高的版本号要拒绝，避免用旧代码解析新结构
SAVE_VERSION = 1

#: 存档槽位数量
DEFAULT_SLOT_COUNT = 5


# ----------------------------------------------------------------------
# 玩家
# ----------------------------------------------------------------------


@dataclass
class PlayerState:
    """玩家信息。属性表由 AI 在游玩中动态增删，框架不预设。"""

    name: str = "无名者"
    attributes: dict[str, str] = field(default_factory=dict)
    #: 伤势、增益、通缉之类的短期状态
    status: list[str] = field(default_factory=list)
    #: 一句话概括玩家当前的处境，阶段 9 每次结算时更新
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PlayerState":
        allowed = set(cls.__dataclass_fields__)
        clean = {k: v for k, v in data.items() if k in allowed}
        # 属性/状态必须是正确容器类型，否则后续渲染会炸
        if not isinstance(clean.get("attributes"), dict):
            clean["attributes"] = {}
        if not isinstance(clean.get("status"), list):
            clean["status"] = []
        return cls(**clean)


# ----------------------------------------------------------------------
# 历史摘要
# ----------------------------------------------------------------------


@dataclass
class HistoryLog:
    """玩家历史：滚动累积摘要 + 最近回合窗口。

    这是控制 token 膨胀的核心结构。设计要点：

    · recent 只保留最近 RECENT_WINDOW 个回合的短文本，是个滑动窗口
    · 每积累 COMPACT_EVERY 个回合，把这批 recent 折叠进 summary，
      然后清空 recent —— 摘要因此是「滚动累积」的
    · summary 有硬上限 MAX_SUMMARY_CHARS，无论玩多久都不会超过

    折叠动作在阶段 5/9 由 AI 完成（把旧摘要 + 新回合润色成新摘要）；
    在没有可用 API 时用 compact_locally() 兜底，保证不失控。
    """

    summary: str = ""
    recent: list[str] = field(default_factory=list)
    turns_since_compaction: int = 0

    #: recent 窗口长度（回合）
    RECENT_WINDOW: int = 6
    #: 每多少回合触发一次摘要折叠
    COMPACT_EVERY: int = 6
    #: 摘要字符数上限，约 1600 token
    MAX_SUMMARY_CHARS: int = 2400
    #: 单条 recent 的字符数上限，防止某回合输出特别长把窗口撑爆
    MAX_ENTRY_CHARS: int = 600

    # ---------- 写入 ----------

    def record(self, text: str) -> bool:
        """记录一个回合，返回是否该触发摘要折叠。"""
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return self.needs_compaction()

        if len(cleaned) > self.MAX_ENTRY_CHARS:
            cleaned = cleaned[: self.MAX_ENTRY_CHARS] + "…"

        self.recent.append(cleaned)
        if len(self.recent) > self.RECENT_WINDOW:
            self.recent = self.recent[-self.RECENT_WINDOW :]

        self.turns_since_compaction += 1
        return self.needs_compaction()

    def needs_compaction(self) -> bool:
        return self.turns_since_compaction >= self.COMPACT_EVERY

    # ---------- 折叠 ----------

    def pending_text(self) -> str:
        """待折叠的内容，阶段 5/9 拿它去喂 AI 生成新摘要。"""
        blocks: list[str] = []
        if self.summary:
            blocks.append(f"【既有摘要】\n{self.summary}")
        if self.recent:
            joined = "\n".join(f"- {entry}" for entry in self.recent)
            blocks.append(f"【新增经历】\n{joined}")
        return "\n\n".join(blocks)

    def apply_compacted(self, new_summary: str) -> None:
        """写入 AI 折叠后的新摘要，并清空待折叠窗口。"""
        self.summary = self._clamp_summary((new_summary or "").strip())
        self.recent.clear()
        self.turns_since_compaction = 0

    def compact_locally(self) -> None:
        """不调用 AI 的兜底折叠。

        只是把新经历追加进摘要再截断 —— 质量远不如 AI 折叠，
        但能保证没有可用 API 时摘要也不会无限膨胀。
        """
        parts: list[str] = []
        if self.summary:
            parts.append(self.summary)
        if self.recent:
            parts.append("；".join(self.recent))

        self.summary = self._clamp_summary("；".join(p for p in parts if p))
        self.recent.clear()
        self.turns_since_compaction = 0

    def _clamp_summary(self, text: str) -> str:
        """摘要超限时保留头尾、丢掉中段。

        不能只留头部：折叠后 recent 会被清空，被丢掉的尾部
        就等于永久丢失 —— 摘要会就此冻结，之后发生什么都不会再被记住。
        头尾各留一段，头部保住身份/立场级别的早期设定，
        尾部保住最近发生的事，只有中段的陈年细节被牺牲。
        """
        if len(text) <= self.MAX_SUMMARY_CHARS:
            return text

        marker = "……"
        keep = max(self.MAX_SUMMARY_CHARS - len(marker), 0)
        head_len = int(keep * 0.6)
        tail_len = keep - head_len
        return text[:head_len].rstrip() + marker + text[-tail_len:].lstrip()

    # ---------- 读取 ----------

    def context_text(self) -> str:
        """组装成喂给 AI 的历史上下文，长度有界。"""
        blocks: list[str] = []
        if self.summary:
            blocks.append(f"【过往经历摘要】\n{self.summary}")
        if self.recent:
            joined = "\n".join(f"- {entry}" for entry in self.recent)
            blocks.append(f"【最近发生】\n{joined}")
        return "\n\n".join(blocks)

    def is_empty(self) -> bool:
        return not self.summary and not self.recent

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "recent": list(self.recent),
            "turns_since_compaction": self.turns_since_compaction,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HistoryLog":
        log = cls()
        if not isinstance(data, dict):
            return log

        log.summary = str(data.get("summary", ""))
        recent = data.get("recent")
        log.recent = [str(x) for x in recent] if isinstance(recent, list) else []

        try:
            log.turns_since_compaction = int(data.get("turns_since_compaction", 0))
        except (TypeError, ValueError):
            log.turns_since_compaction = 0

        # 兼容手工改过的存档：窗口和摘要都不许超限
        log.recent = log.recent[-log.RECENT_WINDOW :]
        log.summary = log._clamp_summary(log.summary)
        return log


# ----------------------------------------------------------------------
# 存档快照
# ----------------------------------------------------------------------


@dataclass
class GameSave:
    """一份完整存档快照。

    刻意不包含 API Key —— Key 属于程序配置，不属于游戏进度。
    """

    slot: int = 1
    version: int = SAVE_VERSION
    saved_at: str = ""
    turns: int = 0
    player: PlayerState = field(default_factory=PlayerState)
    inventory: list[Item] = field(default_factory=list)
    world_state: WorldState = field(default_factory=WorldState)
    world: WorldDocument | None = None
    history: HistoryLog = field(default_factory=HistoryLog)

    def __post_init__(self) -> None:
        if not self.saved_at:
            self.saved_at = datetime.now().isoformat(timespec="seconds")

    # ---------- 概要 ----------

    def headline(self) -> str:
        """一行摘要，存档列表里显示。"""
        world_name = self.world.name if self.world else "未绑定世界观"
        return f"{world_name}　·　{self.world_state.location or '未知地点'}"

    def touch(self) -> None:
        self.saved_at = datetime.now().isoformat(timespec="seconds")

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        return {
            "slot": self.slot,
            "version": self.version,
            "saved_at": self.saved_at,
            "turns": self.turns,
            "player": self.player.to_dict(),
            "inventory": [item.to_dict() for item in self.inventory],
            "world_state": self.world_state.to_dict(),
            "world": self.world.to_dict() if self.world else None,
            "history": self.history.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GameSave":
        save = cls()

        try:
            save.slot = int(data.get("slot", 1))
        except (TypeError, ValueError):
            save.slot = 1

        try:
            save.version = int(data.get("version", SAVE_VERSION))
        except (TypeError, ValueError):
            save.version = SAVE_VERSION

        if save.version > SAVE_VERSION:
            raise SaveFormatError(
                f"该存档由更新版本的程序创建（存档格式 v{save.version}，"
                f"当前程序支持 v{SAVE_VERSION}）。\n\n请升级程序后再读取。"
            )

        save.saved_at = str(data.get("saved_at", "")) or save.saved_at

        try:
            save.turns = int(data.get("turns", 0))
        except (TypeError, ValueError):
            save.turns = 0

        player = data.get("player")
        save.player = PlayerState.from_dict(player) if isinstance(player, dict) else PlayerState()

        inventory = data.get("inventory")
        save.inventory = []
        if isinstance(inventory, list):
            for entry in inventory:
                if not isinstance(entry, dict):
                    continue
                try:
                    save.inventory.append(Item.from_dict(entry))
                except TypeError:
                    # 单件道具结构不对就跳过，不要整个档读不出来
                    continue

        world_state = data.get("world_state")
        save.world_state = (
            WorldState.from_dict(world_state)
            if isinstance(world_state, dict)
            else WorldState()
        )

        world = data.get("world")
        if isinstance(world, dict):
            try:
                save.world = WorldDocument.from_dict(world)
            except TypeError:
                save.world = None

        history = data.get("history")
        save.history = HistoryLog.from_dict(history)

        return save


class SaveFormatError(Exception):
    """存档格式不可用，message 可直接展示。"""


# ----------------------------------------------------------------------
# 槽位元信息
# ----------------------------------------------------------------------


@dataclass
class SlotInfo:
    """存档槽的展示信息。空槽位 exists=False。"""

    index: int
    exists: bool = False
    saved_at: str = ""
    headline: str = ""
    turns: int = 0
    player_name: str = ""
    location: str = ""
    world_name: str = ""
    item_count: int = 0
    #: 文件损坏时为 True，与「空槽位」区分开
    corrupted: bool = False

    @property
    def label(self) -> str:
        return f"存档 {self.index}"


# ----------------------------------------------------------------------
# 存储
# ----------------------------------------------------------------------


@dataclass
class SaveStore:
    """data/saves/ 下的多槽位存档仓库。"""

    directory: Path = field(default_factory=lambda: SAVES_DIR)
    slot_count: int = DEFAULT_SLOT_COUNT

    def path_for(self, slot: int) -> Path:
        return self.directory / f"save_{slot}.json"

    def exists(self, slot: int) -> bool:
        return self.path_for(slot).is_file()

    # ---------- 写 ----------

    def save(self, save: GameSave) -> Path:
        if not 1 <= save.slot <= self.slot_count:
            raise SaveFormatError(
                f"存档槽位超出范围：{save.slot}（有效范围 1~{self.slot_count}）"
            )
        save.version = SAVE_VERSION
        save.touch()

        path = self.path_for(save.slot)
        write_json_atomic(path, save.to_dict())
        return path

    # ---------- 读 ----------

    def load(self, slot: int) -> GameSave:
        """读取存档。槽位不存在或文件损坏时抛 SaveFormatError。"""
        path = self.path_for(slot)
        if not path.is_file():
            raise SaveFormatError(f"存档 {slot} 不存在。")

        data = read_json(path)
        if data is None:
            raise SaveFormatError(
                f"存档 {slot} 已损坏，无法解析。\n\n"
                f"文件位置：{path}\n"
                "如果不需要这份存档，可以直接删除它。"
            )

        return GameSave.from_dict(data)

    # ---------- 管理 ----------

    def delete(self, slot: int) -> bool:
        try:
            self.path_for(slot).unlink()
            return True
        except OSError:
            return False

    def list_slots(self) -> list[SlotInfo]:
        return [self.slot_info(index) for index in range(1, self.slot_count + 1)]

    def slot_info(self, slot: int) -> SlotInfo:
        path = self.path_for(slot)
        if not path.is_file():
            return SlotInfo(index=slot)

        data = read_json(path)
        if data is None:
            return SlotInfo(index=slot, exists=True, corrupted=True)

        try:
            save = GameSave.from_dict(data)
        except SaveFormatError:
            return SlotInfo(index=slot, exists=True, corrupted=True)

        return SlotInfo(
            index=slot,
            exists=True,
            saved_at=save.saved_at,
            headline=save.headline(),
            turns=save.turns,
            player_name=save.player.name,
            location=save.world_state.location,
            world_name=save.world.name if save.world else "",
            item_count=len(save.inventory),
        )

    def latest_slot(self) -> int | None:
        """最近保存过的槽位，用于「继续游戏」。"""
        best: tuple[str, int] | None = None
        for info in self.list_slots():
            if not info.exists or info.corrupted or not info.saved_at:
                continue
            if best is None or info.saved_at > best[0]:
                best = (info.saved_at, info.index)
        return best[1] if best else None
