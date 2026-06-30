from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pytest

from python.core.indicator_engine import IndicatorEngine
from python.models.bar import BarSeries, OHLCVBar


def _bar(close: float, high: float | None = None, low: float | None = None,
         idx: int = 0) -> OHLCVBar:
    h = high if high is not None else close + 0.0010
    l = low if low is not None else close - 0.0010
    return OHLCVBar(
        time_utc=datetime(2024, 1, 2, idx, 0, tzinfo=timezone.utc),
        open=close - 0.0005,
        high=h,
        low=l,
        close=close,
        tick_volume=100,
    )


def _series(*closes: float) -> BarSeries:
    s = BarSeries(200)
    for i, c in enumerate(closes):
        s.push(_bar(c, idx=i))
    return s


class TestAvgRange:
    def test_simple_average(self):
        ie = IndicatorEngine()
        s = _series(1.1000, 1.1010, 1.1020, 1.1030, 1.1040)
        result = ie.avg_range(s, 5)
        # Each bar has range of 0.0020 (high=close+0.001, low=close-0.001)
        assert math.isclose(result, 0.0020, rel_tol=1e-6)

    def test_insufficient_bars_returns_zero(self):
        ie = IndicatorEngine()
        s = _series(1.1000, 1.1010)
        assert ie.avg_range(s, 5) == 0.0


class TestATR:
    def test_wilder_atr_positive(self):
        ie = IndicatorEngine()
        closes = [1.1000 + i * 0.0010 for i in range(20)]
        s = _series(*closes)
        atr = ie.atr(s, 14)
        assert atr > 0.0

    def test_atr_returns_zero_insufficient_bars(self):
        ie = IndicatorEngine()
        s = _series(1.1000, 1.1010)
        assert ie.atr(s, 14) == 0.0


class TestEMA:
    def test_ema_last_value_close_to_price(self):
        ie = IndicatorEngine()
        closes = [1.1000] * 60
        s = _series(*closes)
        ema = ie.ema(s, 50)
        assert math.isclose(ema, 1.1000, rel_tol=1e-4)

    def test_ema_rising_series_below_price(self):
        ie = IndicatorEngine()
        closes = [1.1000 + i * 0.0001 for i in range(60)]
        s = _series(*closes)
        ema = ie.ema(s, 50)
        assert ema < closes[-1]


class TestSwingPoints:
    def test_swing_high_detected(self):
        ie = IndicatorEngine()
        closes = [1.1000, 1.1010, 1.1020, 1.1010, 1.1000, 1.1010]
        highs  = [1.1005, 1.1015, 1.1030, 1.1015, 1.1005, 1.1015]
        lows   = [1.0995, 1.1005, 1.1015, 1.1005, 1.0995, 1.1005]
        s = BarSeries(200)
        for i, (c, h, l) in enumerate(zip(closes, highs, lows)):
            s.push(OHLCVBar(
                time_utc=datetime(2024, 1, 2, i, 0, tzinfo=timezone.utc),
                open=c, high=h, low=l, close=c, tick_volume=100
            ))
        swings = ie.swing_highs(s, lookback=2)
        assert len(swings) >= 1

    def test_no_swing_high_flat(self):
        ie = IndicatorEngine()
        s = _series(*([1.1000] * 10))
        assert ie.swing_highs(s, lookback=2) == []


class TestLRUCache:
    def test_cache_hit(self):
        ie = IndicatorEngine()
        s = _series(*[1.1000 + i * 0.0001 for i in range(60)])
        v1 = ie.ema(s, 50)
        v2 = ie.ema(s, 50)
        assert v1 == v2

    def test_cache_eviction(self):
        from python.core.indicator_engine import _LRUCache
        cache = _LRUCache(maxsize=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)   # evicts "a"
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3
