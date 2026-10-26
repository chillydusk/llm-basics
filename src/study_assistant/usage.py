"""用量与计费。

DeepSeek 的计费口径（官网原话：费用 = token 数 × 单价）：

    输入分成两笔 —— 缓存命中（便宜 50 倍）和未命中
    输出单独一笔
    三笔相加，再按峰谷时段决定要不要 ×2

⚠️ 用 prompt_tokens 一把乘，会把成本算错 —— 这是 Part B 的原始教训。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# 北京时间固定 UTC+8（中国不实行夏令时，不必装 tzdata）
BEIJING = timezone(timedelta(hours=8))

# 元 / 百万 token，查证日期 2026-10-08，官方 2026-09-10 12:00 起生效
PRICE_CACHE_HIT = 0.02
PRICE_CACHE_MISS = 1.00
PRICE_OUTPUT = 4.00
PEAK_MULTIPLIER = 2.0
TOKENS_PER_UNIT = 1_000_000


@dataclass(frozen=True)
class Usage:
    """一次调用的用量。字段名对齐 DeepSeek 原始返回，避免二次改名。"""

    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def prompt_tokens(self) -> int:
        return self.prompt_cache_hit_tokens + self.prompt_cache_miss_tokens

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @classmethod
    def from_sdk(cls, raw) -> Usage:
        """从 openai SDK 的 usage 对象构造。"""
        details = getattr(raw, "prompt_tokens_details", None)
        hit = getattr(details, "cached_tokens", None) if details else None
        if hit is None:
            # DeepSeek 走的是自家字段名
            hit = getattr(raw, "prompt_cache_hit_tokens", 0) or 0
        miss = getattr(raw, "prompt_cache_miss_tokens", None)
        if miss is None:
            miss = (getattr(raw, "prompt_tokens", 0) or 0) - hit

        completion_details = getattr(raw, "completion_tokens_details", None)
        reasoning = getattr(completion_details, "reasoning_tokens", 0) if completion_details else 0

        return cls(
            prompt_cache_hit_tokens=int(hit or 0),
            prompt_cache_miss_tokens=int(miss or 0),
            completion_tokens=int(getattr(raw, "completion_tokens", 0) or 0),
            reasoning_tokens=int(reasoning or 0),
        )


def is_peak_now(now: datetime | None = None) -> bool:
    """高峰时段 = 周一至周五 9:00–12:00、14:00–18:00（北京时间），价格 ×2。

    已知偏差：官网把法定节假日算作空闲时段，本地拿不到节假日表，
    会把节假日误判成高峰 —— 结果是费用被**高估**（保守方向，可以接受）。
    """
    now = now or datetime.now(BEIJING)
    if now.weekday() >= 5:
        return False
    hour = now.hour + now.minute / 60
    return (9 <= hour < 12) or (14 <= hour < 18)


def estimate_cost(usage: Usage, peak: bool | None = None) -> float:
    """估算一次调用的费用（元）。"""
    cost = (
        usage.prompt_cache_hit_tokens * PRICE_CACHE_HIT
        + usage.prompt_cache_miss_tokens * PRICE_CACHE_MISS
        + usage.completion_tokens * PRICE_OUTPUT
    ) / TOKENS_PER_UNIT
    peak = is_peak_now() if peak is None else peak
    return cost * PEAK_MULTIPLIER if peak else cost


class CostMeter:
    """累计整场会话的花费。退出时给用户一个总数。"""

    def __init__(self) -> None:
        self.calls = 0
        self.total = 0.0
        self.usage = Usage()

    def add(self, usage: Usage) -> float:
        cost = estimate_cost(usage)
        self.calls += 1
        self.total += cost
        self.usage = Usage(
            prompt_cache_hit_tokens=self.usage.prompt_cache_hit_tokens
            + usage.prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=self.usage.prompt_cache_miss_tokens
            + usage.prompt_cache_miss_tokens,
            completion_tokens=self.usage.completion_tokens + usage.completion_tokens,
            reasoning_tokens=self.usage.reasoning_tokens + usage.reasoning_tokens,
        )
        return cost

    def summary(self) -> str:
        u = self.usage
        return (
            f"本次会话 {self.calls} 次调用｜"
            f"输入 {u.prompt_tokens}（命中 {u.prompt_cache_hit_tokens}）｜"
            f"输出 {u.completion_tokens}｜"
            f"合计 ≈ {self.total:.6f} 元"
        )
