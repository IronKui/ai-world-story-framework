"""token 用量与花费统计。

分两层：
  · session   —— 本次运行累计，退出即丢
  · lifetime  —— 历史累计，持久化到 data/usage.json

持久化的只有 token 数、次数与金额这类统计量，
不含任何 prompt / 回复原文，所以不涉及内容泄露。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from core.paths import DATA_DIR
from core.pricing import (
    DEFAULT_USD_TO_CNY,
    CostBreakdown,
    cost_from_usage,
    resolve_model,
)
from core.storage import read_json, write_json_atomic

USAGE_FILE = DATA_DIR / "usage.json"

#: 内存里保留多少次最近调用，用于「用量统计」窗口展示
RECENT_LIMIT = 50


@dataclass
class UsageTotals:
    """可累加的用量小计。"""

    calls: int = 0
    prompt_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add_breakdown(
        self, breakdown: CostBreakdown, completion_tokens: int, reasoning_tokens: int = 0
    ) -> None:
        self.calls += 1
        self.cache_hit_tokens += breakdown.cache_hit_tokens
        self.cache_miss_tokens += breakdown.cache_miss_tokens
        self.prompt_tokens += breakdown.cache_hit_tokens + breakdown.cache_miss_tokens
        self.completion_tokens += completion_tokens
        self.reasoning_tokens += reasoning_tokens
        self.cost_usd += breakdown.total_usd

    def merge(self, other: "UsageTotals") -> None:
        self.calls += other.calls
        self.prompt_tokens += other.prompt_tokens
        self.cache_hit_tokens += other.cache_hit_tokens
        self.cache_miss_tokens += other.cache_miss_tokens
        self.completion_tokens += other.completion_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.cost_usd += other.cost_usd

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UsageTotals":
        totals = cls()
        if not isinstance(data, dict):
            return totals

        for key in ("calls", "prompt_tokens", "cache_hit_tokens",
                    "cache_miss_tokens", "completion_tokens", "reasoning_tokens"):
            try:
                setattr(totals, key, max(int(data.get(key, 0)), 0))
            except (TypeError, ValueError):
                setattr(totals, key, 0)

        try:
            totals.cost_usd = max(float(data.get("cost_usd", 0.0)), 0.0)
        except (TypeError, ValueError):
            totals.cost_usd = 0.0

        return totals

    def cny(self, rate: float = DEFAULT_USD_TO_CNY) -> float:
        return self.cost_usd * rate


@dataclass
class UsageRecord:
    """一次调用的记录。"""

    at: str
    model: str
    reason: str                    # 用途：剧情 / 道具 / 校验 …，阶段 9 起区分
    prompt_tokens: int = 0
    cache_hit_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    peak: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UsageRecord":
        return cls(
            at=str(data.get("at", "")),
            model=str(data.get("model", "")),
            reason=str(data.get("reason", "")),
            prompt_tokens=int(data.get("prompt_tokens", 0) or 0),
            cache_hit_tokens=int(data.get("cache_hit_tokens", 0) or 0),
            completion_tokens=int(data.get("completion_tokens", 0) or 0),
            reasoning_tokens=int(data.get("reasoning_tokens", 0) or 0),
            cost_usd=float(data.get("cost_usd", 0.0) or 0.0),
            peak=bool(data.get("peak", False)),
        )


@dataclass
class UsageTracker:
    """用量与花费的累计器。线程不安全，只在主线程里调用。"""

    path: Path = field(default_factory=lambda: USAGE_FILE)
    session: UsageTotals = field(default_factory=UsageTotals)
    lifetime: UsageTotals = field(default_factory=UsageTotals)
    by_model: dict[str, UsageTotals] = field(default_factory=dict)
    recent: list[UsageRecord] = field(default_factory=list)

    # ---------- 记录 ----------

    def record(
        self,
        *,
        model: str,
        usage: dict,
        completion_tokens: int = 0,
        reasoning_tokens: int = 0,
        reason: str = "",
        peak: bool | None = None,
        moment: datetime | None = None,
        persist: bool = True,
    ) -> UsageRecord:
        """记录一次调用。返回本次记录，方便调用方即时展示。"""
        breakdown = cost_from_usage(model, usage, peak=peak, moment=moment)

        completion = completion_tokens or _int(usage.get("completion_tokens"))
        reasoning = reasoning_tokens or _reasoning_tokens(usage)

        self.session.add_breakdown(breakdown, completion, reasoning)
        self.lifetime.add_breakdown(breakdown, completion, reasoning)

        key = resolve_model(model)
        self.by_model.setdefault(key, UsageTotals()).add_breakdown(
            breakdown, completion, reasoning
        )

        record = UsageRecord(
            at=datetime.now().isoformat(timespec="seconds"),
            model=model,
            reason=reason,
            prompt_tokens=breakdown.cache_hit_tokens + breakdown.cache_miss_tokens,
            cache_hit_tokens=breakdown.cache_hit_tokens,
            completion_tokens=completion,
            reasoning_tokens=reasoning,
            cost_usd=breakdown.total_usd,
            peak=breakdown.peak,
        )

        self.recent.append(record)
        if len(self.recent) > RECENT_LIMIT:
            self.recent = self.recent[-RECENT_LIMIT :]

        if persist:
            self.save()

        return record

    def reset_lifetime(self) -> None:
        """清空历史累计（session 保留）。用户主动重置时调用。"""
        self.lifetime = UsageTotals()
        self.by_model.clear()
        self.save()

    # ---------- 持久化 ----------

    def load(self) -> None:
        data = read_json(self.path)
        if data is None:
            return

        self.lifetime = UsageTotals.from_dict(data.get("lifetime") or {})

        raw_models = data.get("by_model")
        self.by_model = {}
        if isinstance(raw_models, dict):
            for name, payload in raw_models.items():
                if isinstance(payload, dict):
                    self.by_model[str(name)] = UsageTotals.from_dict(payload)

        raw_recent = data.get("recent")
        self.recent = []
        if isinstance(raw_recent, list):
            for entry in raw_recent[-RECENT_LIMIT:]:
                if isinstance(entry, dict):
                    try:
                        self.recent.append(UsageRecord.from_dict(entry))
                    except (TypeError, ValueError):
                        continue

    def save(self) -> None:
        payload = {
            "lifetime": self.lifetime.to_dict(),
            "by_model": {name: t.to_dict() for name, t in self.by_model.items()},
            "recent": [r.to_dict() for r in self.recent[-RECENT_LIMIT:]],
        }
        try:
            write_json_atomic(self.path, payload)
        except OSError:
            # 统计写不进去不该影响正常游玩
            pass


def _int(value) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _reasoning_tokens(usage: dict) -> int:
    details = (usage or {}).get("completion_tokens_details")
    if isinstance(details, dict):
        return _int(details.get("reasoning_tokens"))
    return 0
