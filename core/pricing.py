"""DeepSeek 计价与花费估算。

价格来源：https://api-docs.deepseek.com/quick_start/pricing
抓取日期：2026-09-22。官方原文要点：

  · 峰谷计价，「低谷价 = 高峰价的一半」
  · 高峰时段 = UTC 周一至周五 01:00-04:00 与 06:00-10:00
    其余时间（含周末与中国法定节假日全天）为低谷
  · deepseek-chat / deepseek-reasoner / deepseek-v4-flash 等旧模型名
    仍被接受，但对应模型已下线，请求由 DeepSeek-V4.1-Flash 服务，
    并按 Flash 价格计费

重要：这里算出来的是**估算值**，不是账单。
官方页原话是「价格可能变动，DeepSeek 保留调整权利」，
请以 https://platform.deepseek.com 的实际账单为准。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

#: 单位：美元 / 每百万 token
MODEL_PRICING: dict[str, dict[str, dict[str, float]]] = {
    "deepseek-flash": {
        "peak": {"cache_hit": 0.006, "cache_miss": 0.30, "output": 1.20},
        "off_peak": {"cache_hit": 0.003, "cache_miss": 0.15, "output": 0.60},
    },
    "deepseek-v4-pro": {
        "peak": {"cache_hit": 0.044, "cache_miss": 1.32, "output": 3.96},
        "off_peak": {"cache_hit": 0.022, "cache_miss": 0.66, "output": 1.98},
    },
}

#: 官方说明这些名字仍被接受、按 Flash 计费
MODEL_ALIASES: dict[str, str] = {
    "deepseek-chat": "deepseek-flash",
    "deepseek-reasoner": "deepseek-flash",
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek-v4-flash-vision-exp": "deepseek-flash",
    "deepseek-v4.1-flash": "deepseek-flash",
    "deepseek-v4-pro-0813": "deepseek-v4-pro",
}

#: 高峰时段（UTC），左闭右开
PEAK_WINDOWS_UTC: tuple[tuple[int, int], ...] = ((1, 4), (6, 10))

#: 未知模型的兜底计价，按最便宜的那档处理
FALLBACK_MODEL = "deepseek-flash"

#: 人民币展示用的默认汇率。这是估算值，用户可在配置里改
DEFAULT_USD_TO_CNY = 7.1


def resolve_model(model: str) -> str:
    """把模型名（含旧别名）归一到计价表里的键。"""
    key = (model or "").strip().lower()
    if key in MODEL_PRICING:
        return key
    if key in MODEL_ALIASES:
        return MODEL_ALIASES[key]
    return FALLBACK_MODEL


def is_known_model(model: str) -> bool:
    key = (model or "").strip().lower()
    return key in MODEL_PRICING or key in MODEL_ALIASES


def is_peak_now(moment: datetime | None = None) -> bool:
    """当前是否处于高峰时段。

    moment 必须是 UTC。不传则取当前时间。
    注意：中国法定节假日这里**无法自动判断** —— 那天官方按低谷计价，
    而本函数会按工作日算成高峰，结果偏保守（估高不估低）。
    """
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    # 周六(5)、周日(6) 全天低谷
    if current.weekday() >= 5:
        return False

    hour = current.hour
    return any(start <= hour < end for start, end in PEAK_WINDOWS_UTC)


@dataclass
class CostBreakdown:
    """一次调用或一批累计的花费明细。"""

    model_key: str = FALLBACK_MODEL
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    output_tokens: int = 0
    input_cost: float = 0.0
    output_cost: float = 0.0
    peak: bool = False
    #: 模型名不在计价表里，按兜底价估算的
    estimated_model: bool = False

    @property
    def total_tokens(self) -> int:
        return self.cache_hit_tokens + self.cache_miss_tokens + self.output_tokens

    @property
    def total_usd(self) -> float:
        return self.input_cost + self.output_cost

    def total_cny(self, usd_to_cny: float = DEFAULT_USD_TO_CNY) -> float:
        return self.total_usd * usd_to_cny

    def add(self, other: "CostBreakdown") -> None:
        self.cache_hit_tokens += other.cache_hit_tokens
        self.cache_miss_tokens += other.cache_miss_tokens
        self.output_tokens += other.output_tokens
        self.input_cost += other.input_cost
        self.output_cost += other.output_cost

    def describe(self, usd_to_cny: float = DEFAULT_USD_TO_CNY) -> str:
        return (
            f"${self.total_usd:.6f}（约 ¥{self.total_cny(usd_to_cny):.4f}）"
        )


def calculate_cost(
    model: str,
    *,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
    output_tokens: int = 0,
    peak: bool | None = None,
    moment: datetime | None = None,
) -> CostBreakdown:
    """按 token 用量算钱。

    peak=None 时自动按当前时刻判断峰谷；显式传 True/False 可强制。
    """
    model_key = resolve_model(model)
    table = MODEL_PRICING[model_key]
    used_peak = peak if peak is not None else is_peak_now(moment)
    rates = table["peak" if used_peak else "off_peak"]

    # 价格单位是「每百万 token」，所以除以 1e6
    input_cost = (
        cache_hit_tokens * rates["cache_hit"] + cache_miss_tokens * rates["cache_miss"]
    ) / 1_000_000
    output_cost = output_tokens * rates["output"] / 1_000_000

    return CostBreakdown(
        model_key=model_key,
        cache_hit_tokens=int(cache_hit_tokens),
        cache_miss_tokens=int(cache_miss_tokens),
        output_tokens=int(output_tokens),
        input_cost=input_cost,
        output_cost=output_cost,
        peak=used_peak,
        estimated_model=not is_known_model(model),
    )


def cost_from_usage(
    model: str,
    usage: dict,
    *,
    peak: bool | None = None,
    moment: datetime | None = None,
) -> CostBreakdown:
    """直接从 API 返回的 usage 对象算钱。

    实测 deepseek-flash 返回的字段：
        prompt_cache_hit_tokens / prompt_cache_miss_tokens
        prompt_tokens / completion_tokens / total_tokens
    不是所有模型都返回 cache 拆分，缺失时退回 prompt_tokens 全额按未命中算
    （宁可估高不估低）。
    """
    usage = usage or {}

    hit = _as_int(usage.get("prompt_cache_hit_tokens"))
    miss = _as_int(usage.get("prompt_cache_miss_tokens"))
    prompt_total = _as_int(usage.get("prompt_tokens"))

    if hit == 0 and miss == 0:
        # 没给出缓存拆分，全部按未命中计
        miss = prompt_total

    return calculate_cost(
        model,
        cache_hit_tokens=hit,
        cache_miss_tokens=miss,
        output_tokens=_as_int(usage.get("completion_tokens")),
        peak=peak,
        moment=moment,
    )


def _as_int(value) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0
