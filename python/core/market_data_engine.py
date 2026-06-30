from __future__ import annotations

import abc
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from python.models.bar import BarSeries, OHLCVBar
from python.models.config import DataConfig


class AbstractMarketDataFeed(abc.ABC):
    @abc.abstractmethod
    def fetch_bars(self, timeframe: str, count: int) -> list[OHLCVBar]:
        """Return the most recent `count` closed bars for `timeframe`."""

    @abc.abstractmethod
    def is_connected(self) -> bool:
        pass


class CSVMarketDataFeed(AbstractMarketDataFeed):
    """Reads pre-downloaded M5/M15/H1 CSV files for backtesting."""

    def __init__(self, data_dir: str) -> None:
        self._dir = Path(data_dir)
        self._cache: dict[str, list[OHLCVBar]] = {}

    def fetch_bars(self, timeframe: str, count: int) -> list[OHLCVBar]:
        if timeframe not in self._cache:
            self._cache[timeframe] = self._load(timeframe)
        all_bars = self._cache[timeframe]
        return all_bars[-count:] if len(all_bars) >= count else all_bars

    def is_connected(self) -> bool:
        return True

    def _load(self, timeframe: str) -> list[OHLCVBar]:
        path = self._dir / f"EURUSD_{timeframe}.csv"
        if not path.exists():
            return []
        bars: list[OHLCVBar] = []
        with path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                bars.append(OHLCVBar(
                    time_utc=datetime.fromisoformat(row["time"]).replace(tzinfo=timezone.utc),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    tick_volume=int(row.get("tick_volume", 0)),
                ))
        return bars


class MT5MarketDataFeed(AbstractMarketDataFeed):
    """Live feed using the MetaTrader5 Python package."""

    def __init__(self, server: str = "", login: int = 0, password: str = "") -> None:
        self._connected = False
        try:
            import MetaTrader5 as mt5
            self._mt5 = mt5
            if login:
                self._connected = mt5.initialize(server=server, login=login, password=password)
            else:
                self._connected = mt5.initialize()
        except ImportError:
            self._mt5 = None

    _TF_MAP = {
        "M5": 5,     # mt5.TIMEFRAME_M5
        "M15": 15,   # mt5.TIMEFRAME_M15
        "H1": 16385, # mt5.TIMEFRAME_H1
    }

    def fetch_bars(self, timeframe: str, count: int) -> list[OHLCVBar]:
        if not self._mt5 or not self._connected:
            return []
        tf = self._TF_MAP.get(timeframe, 5)
        rates = self._mt5.copy_rates_from_pos("EURUSD", tf, 0, count)
        if rates is None:
            return []
        bars: list[OHLCVBar] = []
        for r in rates:
            bars.append(OHLCVBar(
                time_utc=datetime.fromtimestamp(r["time"], tz=timezone.utc),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                tick_volume=int(r["tick_volume"]),
            ))
        return bars

    def is_connected(self) -> bool:
        return self._connected

    def shutdown(self) -> None:
        if self._mt5:
            self._mt5.shutdown()


class MarketDataEngine:
    """Manages rolling BarSeries windows for each timeframe."""

    def __init__(self, feed: AbstractMarketDataFeed, cfg: DataConfig) -> None:
        self._feed = feed
        self._cfg = cfg
        self._series: dict[str, BarSeries] = {
            "M5": BarSeries(cfg.m5_bars),
            "M15": BarSeries(cfg.m15_bars),
            "H1": BarSeries(cfg.h1_bars),
        }
        self._bar_close_callbacks: list[Callable[[str, OHLCVBar], None]] = []

    def warm_up(self) -> None:
        for tf, series in self._series.items():
            bars = self._feed.fetch_bars(tf, series._data.maxlen)
            for b in bars:
                series.push(b)

    def on_new_bar(self, timeframe: str, bar: OHLCVBar) -> None:
        self._series[timeframe].push(bar)
        for cb in self._bar_close_callbacks:
            cb(timeframe, bar)

    def subscribe_bar_close(self, callback: Callable[[str, OHLCVBar], None]) -> None:
        self._bar_close_callbacks.append(callback)

    def get_series(self, timeframe: str) -> BarSeries:
        return self._series[timeframe]

    def latest_bar(self, timeframe: str) -> OHLCVBar | None:
        s = self._series.get(timeframe)
        return s.latest() if s and len(s) > 0 else None

    def refresh(self, timeframe: str) -> OHLCVBar | None:
        bars = self._feed.fetch_bars(timeframe, 2)
        if len(bars) < 2:
            return None
        new_bar = bars[-2]
        series = self._series[timeframe]
        if len(series) == 0 or series.latest().time_utc != new_bar.time_utc:
            self.on_new_bar(timeframe, new_bar)
            return new_bar
        return None

    def is_connected(self) -> bool:
        return self._feed.is_connected()
