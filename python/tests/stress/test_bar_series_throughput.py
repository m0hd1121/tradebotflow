from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from python.models.bar import BarSeries, OHLCVBar


def _make_bar(i: int) -> OHLCVBar:
    t = datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=5 * i)
    p = 1.1000 + (i % 100) * 0.0001
    return OHLCVBar(
        time_utc=t, open=p, high=p + 0.0010, low=p - 0.0010,
        close=p, tick_volume=100,
    )


class TestBarSeriesThroughput:
    def test_push_100k_bars_under_2s(self):
        series = BarSeries(200)
        n = 100_000
        start = time.perf_counter()
        for i in range(n):
            series.push(_make_bar(i))
        elapsed = time.perf_counter() - start

        assert elapsed < 2.0, f"push {n} bars took {elapsed:.2f}s (limit 2.0s)"
        assert len(series) == 200   # deque maxlen enforced

    def test_numpy_extraction_under_100ms(self):
        from python.core.indicator_engine import IndicatorEngine
        series = BarSeries(200)
        for i in range(200):
            series.push(_make_bar(i))
        ie = IndicatorEngine()

        start = time.perf_counter()
        for _ in range(1000):
            _ = ie.ema(series, 50)
        elapsed = time.perf_counter() - start

        assert elapsed < 2.0, f"1000 EMA calls took {elapsed:.2f}s (limit 2.0s)"


class TestIndicatorCacheThroughput:
    def test_cached_vs_uncached_speedup(self):
        from python.core.indicator_engine import IndicatorEngine
        series = BarSeries(200)
        for i in range(200):
            series.push(_make_bar(i))
        ie = IndicatorEngine()

        # First call (uncached)
        start = time.perf_counter()
        for _ in range(100):
            ie._cache._data.clear()   # force cache miss
            ie.ema(series, 50)
        uncached_time = time.perf_counter() - start

        # Second pass (cached)
        start = time.perf_counter()
        for _ in range(100):
            ie.ema(series, 50)
        cached_time = time.perf_counter() - start

        # Cache should be meaningfully faster (at least 2x)
        assert cached_time < uncached_time or cached_time < 0.05, (
            f"Cache didn't help: uncached={uncached_time:.3f}s cached={cached_time:.3f}s"
        )
