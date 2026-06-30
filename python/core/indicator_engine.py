from __future__ import annotations

from collections import OrderedDict
from typing import Any

import numpy as np

from python.models.bar import BarSeries, OHLCVBar


class _LRUCache:
    def __init__(self, maxsize: int = 500) -> None:
        self._data: OrderedDict[Any, Any] = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: Any) -> Any | None:
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return None

    def set(self, key: Any, value: Any) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        else:
            if len(self._data) >= self._maxsize:
                self._data.popitem(last=False)
        self._data[key] = value


class IndicatorEngine:
    """Computes all technical indicators needed by the strategy."""

    def __init__(self, maxsize: int = 500) -> None:
        self._cache = _LRUCache(maxsize)

    def avg_range(self, series: BarSeries, period: int) -> float:
        """Simple average of (high-low) for the last `period` bars."""
        bars = series.as_list()
        if len(bars) < period:
            return 0.0
        key = ("avg_range", bars[-1].time_utc, period, len(bars))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = float(np.mean([b.range_size() for b in bars[-period:]]))
        self._cache.set(key, result)
        return result

    def atr(self, series: BarSeries, period: int) -> float:
        """Wilder's Average True Range."""
        bars = series.as_list()
        if len(bars) < period + 1:
            return 0.0
        key = ("atr", bars[-1].time_utc, period, len(bars))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        trs = [bars[1].true_range(bars[0].close)]
        for i in range(2, len(bars)):
            trs.append(bars[i].true_range(bars[i - 1].close))
        tr_arr = np.array(trs)
        if len(tr_arr) < period:
            return float(np.mean(tr_arr))
        wilder = float(np.mean(tr_arr[:period]))
        for tr in tr_arr[period:]:
            wilder = (wilder * (period - 1) + tr) / period
        self._cache.set(key, wilder)
        return wilder

    def ema(self, series: BarSeries, period: int) -> float:
        """Exponential moving average of close prices."""
        bars = series.as_list()
        if len(bars) < period:
            return 0.0
        key = ("ema", bars[-1].time_utc, period, len(bars))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        closes = np.array([b.close for b in bars])
        k = 2.0 / (period + 1)
        val = float(np.mean(closes[:period]))
        for c in closes[period:]:
            val = c * k + val * (1 - k)
        self._cache.set(key, val)
        return val

    def vwap(self, series: BarSeries) -> float:
        """Volume-weighted average price using tick_volume proxy."""
        bars = series.as_list()
        if not bars:
            return 0.0
        key = ("vwap", bars[-1].time_utc, len(bars))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        typical = np.array([(b.high + b.low + b.close) / 3 for b in bars])
        vols = np.array([max(b.tick_volume, 1) for b in bars], dtype=float)
        result = float(np.sum(typical * vols) / np.sum(vols))
        self._cache.set(key, result)
        return result

    def swing_highs(self, series: BarSeries, lookback: int = 2) -> list[tuple[int, float]]:
        """Return list of (index, price) for confirmed swing highs.

        A swing high at index i is confirmed when i + lookback bars have closed.
        Returns indices relative to the series (0 = oldest, -1 = latest).
        """
        bars = series.as_list()
        result: list[tuple[int, float]] = []
        for i in range(lookback, len(bars) - lookback):
            high = bars[i].high
            if all(bars[i - j].high < high for j in range(1, lookback + 1)) and \
               all(bars[i + j].high < high for j in range(1, lookback + 1)):
                result.append((i, high))
        return result

    def swing_lows(self, series: BarSeries, lookback: int = 2) -> list[tuple[int, float]]:
        """Return list of (index, price) for confirmed swing lows."""
        bars = series.as_list()
        result: list[tuple[int, float]] = []
        for i in range(lookback, len(bars) - lookback):
            low = bars[i].low
            if all(bars[i - j].low > low for j in range(1, lookback + 1)) and \
               all(bars[i + j].low > low for j in range(1, lookback + 1)):
                result.append((i, low))
        return result

    def adr(self, daily_highs: list[float], daily_lows: list[float], period: int = 14) -> float:
        """Average daily range over `period` trading days, in price units."""
        if len(daily_highs) < period or len(daily_lows) < period:
            return 0.0
        ranges = [h - l for h, l in zip(daily_highs[-period:], daily_lows[-period:])]
        return float(np.mean(ranges))

    def sma(self, values: list[float], period: int) -> float:
        if len(values) < period:
            return 0.0
        return float(np.mean(values[-period:]))
