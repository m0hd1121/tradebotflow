from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

import numpy as np


@dataclass(frozen=True)
class OHLCVBar:
    time_utc: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int

    def is_bullish(self) -> bool:
        return self.close > self.open

    def is_bearish(self) -> bool:
        return self.close < self.open

    def body_size(self) -> float:
        return abs(self.close - self.open)

    def range_size(self) -> float:
        return self.high - self.low

    def wick_ratio(self) -> float:
        body = self.body_size()
        if body == 0.0:
            return float("inf")
        return (self.range_size() - body) / body

    def true_range(self, prev_close: float | None = None) -> float:
        if prev_close is None:
            return self.range_size()
        return max(
            self.high - self.low,
            abs(self.high - prev_close),
            abs(self.low - prev_close),
        )


class BarSeries:
    """Fixed-size rolling window of OHLCVBar objects."""

    def __init__(self, maxlen: int) -> None:
        self._data: deque[OHLCVBar] = deque(maxlen=maxlen)

    def push(self, bar: OHLCVBar) -> None:
        self._data.append(bar)

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, index: int) -> OHLCVBar:
        return list(self._data)[index]

    def __iter__(self) -> Iterator[OHLCVBar]:
        return iter(self._data)

    def latest(self) -> OHLCVBar:
        return self._data[-1]

    def is_full(self) -> bool:
        return len(self._data) == self._data.maxlen

    def highs(self) -> np.ndarray:
        return np.array([b.high for b in self._data])

    def lows(self) -> np.ndarray:
        return np.array([b.low for b in self._data])

    def closes(self) -> np.ndarray:
        return np.array([b.close for b in self._data])

    def ranges(self) -> np.ndarray:
        return np.array([b.range_size() for b in self._data])

    def true_ranges(self) -> np.ndarray:
        bars = list(self._data)
        trs = [bars[0].range_size()]
        for i in range(1, len(bars)):
            trs.append(bars[i].true_range(bars[i - 1].close))
        return np.array(trs)

    def as_list(self) -> list[OHLCVBar]:
        return list(self._data)
