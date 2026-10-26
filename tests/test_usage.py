"""计费：三个数必须分开算，不能拿 prompt_tokens 一把乘。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from study_assistant.usage import (
    BEIJING,
    CostMeter,
    Usage,
    estimate_cost,
    is_peak_now,
)


def test_cost_splits_cache_hit_and_miss():
    u = Usage(prompt_cache_hit_tokens=1_000_000, prompt_cache_miss_tokens=0, completion_tokens=0)
    assert estimate_cost(u, peak=False) == 0.02  # 命中价


def test_cost_miss_is_50x_hit():
    hit = estimate_cost(Usage(prompt_cache_hit_tokens=1_000_000), peak=False)
    miss = estimate_cost(Usage(prompt_cache_miss_tokens=1_000_000), peak=False)
    assert miss / hit == 50


def test_output_price():
    u = Usage(completion_tokens=1_000_000)
    assert estimate_cost(u, peak=False) == 4.0


def test_peak_doubles():
    u = Usage(prompt_cache_miss_tokens=1_000_000)
    assert estimate_cost(u, peak=True) == estimate_cost(u, peak=False) * 2


def test_is_peak_now_weekend_is_off():
    saturday = datetime(2026, 10, 24, 10, 0, tzinfo=BEIJING)
    assert is_peak_now(saturday) is False


def test_is_peak_now_weekday_morning():
    monday = datetime(2026, 10, 26, 10, 0, tzinfo=BEIJING)
    assert is_peak_now(monday) is True


def test_is_peak_now_lunch_is_off():
    monday = datetime(2026, 10, 26, 13, 0, tzinfo=BEIJING)
    assert is_peak_now(monday) is False


def test_from_sdk_deepseek_fields():
    raw = SimpleNamespace(
        prompt_tokens=100,
        prompt_cache_hit_tokens=30,
        prompt_cache_miss_tokens=70,
        completion_tokens=20,
        prompt_tokens_details=None,
        completion_tokens_details=None,
    )
    u = Usage.from_sdk(raw)
    assert u.prompt_cache_hit_tokens == 30
    assert u.prompt_cache_miss_tokens == 70
    assert u.prompt_tokens == 100
    assert u.total_tokens == 120


def test_from_sdk_openai_style_cached_tokens():
    raw = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=20,
        prompt_tokens_details=SimpleNamespace(cached_tokens=40),
        completion_tokens_details=None,
    )
    u = Usage.from_sdk(raw)
    assert u.prompt_cache_hit_tokens == 40
    assert u.prompt_cache_miss_tokens == 60


def test_meter_accumulates():
    meter = CostMeter()
    meter.add(Usage(prompt_cache_miss_tokens=1000, completion_tokens=100))
    meter.add(Usage(prompt_cache_miss_tokens=1000, completion_tokens=100))
    assert meter.calls == 2
    assert meter.usage.prompt_cache_miss_tokens == 2000
    assert "2 次调用" in meter.summary()
