"""AsyncTokenBucket: the Gemini fallback's sliding-window quota limiter."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest import mock

from backend.app.config import get_settings
from backend.app.pipeline import llm_live
from backend.app.pipeline.llm_live import AsyncTokenBucket, _gemini_limiter
from tests.support import IsolatedAsyncTestCase, IsolatedTestCase


class FakeClock:
    """Only the limiter's view of time moves; the event loop keeps the real clock."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class SlidingWindowTest(IsolatedAsyncTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.clock = FakeClock()
        for patcher in (
            mock.patch.object(llm_live, "time", SimpleNamespace(monotonic=self.clock.monotonic)),
            mock.patch.object(llm_live, "asyncio", SimpleNamespace(Lock=asyncio.Lock, sleep=self.clock.sleep)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_a_whole_turn_fires_back_to_back(self) -> None:
        bucket = AsyncTokenBucket(rate=15, burst=6)
        waits = [await bucket.acquire() for _ in range(6)]
        self.assertEqual(waits, [0.0] * 6)
        self.assertEqual(self.clock.slept, [])

    async def test_the_seventh_call_waits_out_the_burst_window(self) -> None:
        bucket = AsyncTokenBucket(rate=15, burst=6)
        for _ in range(6):
            await bucket.acquire()
        waited_ms = await bucket.acquire()
        self.assertAlmostEqual(waited_ms, 10_000.0, places=3)

    async def test_the_per_minute_cap_holds(self) -> None:
        bucket = AsyncTokenBucket(rate=15, burst=15)
        for _ in range(15):
            await bucket.acquire()
        waited_ms = await bucket.acquire()
        self.assertAlmostEqual(waited_ms, 60_000.0, places=3)
        window_start = self.clock.now - bucket.window
        self.assertLessEqual(len([t for t in bucket._slot_times if t > window_start]), bucket.rate)

    async def test_burst_is_capped_at_the_rate(self) -> None:
        self.assertEqual(AsyncTokenBucket(rate=15, burst=99).burst, 15)
        self.assertEqual(AsyncTokenBucket(rate=15, burst=0).burst, 15)


class LimiterSettingsTest(IsolatedTestCase):
    def test_zero_rpm_disables_the_limiter(self) -> None:
        with mock.patch.dict(os.environ, {"GEMINI_RPM": "0"}):
            get_settings.cache_clear()
            self.assertIsNone(_gemini_limiter())

    def test_the_limiter_is_one_process_wide_instance(self) -> None:
        self.assertIs(_gemini_limiter(), _gemini_limiter())
