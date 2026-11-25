"""Unit tests for AsyncRateLimiter (10 RPM & 429 penalty backoff)."""

from __future__ import annotations

import pytest

from src.core.pubg_client import AsyncRateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_enforces_min_spacing_and_window() -> None:
    now = 0.0
    sleeps: list[float] = []

    def fake_clock() -> float:
        return now

    async def fake_sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    limiter = AsyncRateLimiter(
        max_requests=3,
        period_seconds=60.0,
        min_interval_seconds=6.5,
        clock=fake_clock,
        sleeper=fake_sleep,
    )

    for _ in range(4):
        await limiter.acquire()

    # 1st immediate; 2nd waits 6.5s; 3rd waits 6.5s; 4th hits 3-per-60s window and waits > 46s
    assert len(sleeps) >= 3
    assert sleeps[0] == pytest.approx(6.5, abs=0.01)
    assert sleeps[1] == pytest.approx(6.5, abs=0.01)
    assert now >= 60.0


@pytest.mark.asyncio
async def test_rate_limiter_429_penalty_delays_next_request() -> None:
    now = 100.0
    sleeps: list[float] = []

    def fake_clock() -> float:
        return now

    async def fake_sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    limiter = AsyncRateLimiter(
        max_requests=10,
        period_seconds=60.0,
        min_interval_seconds=0.0,
        clock=fake_clock,
        sleeper=fake_sleep,
    )

    await limiter.acquire()
    limiter.penalize(12.0)
    await limiter.acquire()

    assert sleeps == [pytest.approx(12.0, abs=0.01)]
    assert now == pytest.approx(112.0, abs=0.01)
