from __future__ import annotations

from datetime import date, datetime, time, timezone

from python.infra.database import TradeJournalDAO
from python.models.bar import BarSeries, OHLCVBar
from python.models.level import PriceLevel, Zone, ZoneType
from python.core.indicator_engine import IndicatorEngine


_PIP = 0.0001
_MAX_ZONES_PER_TYPE = 50


class LevelManager:
    """Maintains and persists all key price levels and OB/FVG zones."""

    def __init__(self, indicator_engine: IndicatorEngine, dao: TradeJournalDAO) -> None:
        self._ie = indicator_engine
        self._dao = dao
        self._pdh = self._pdl = 0.0
        self._pwh = self._pwl = 0.0
        self._asian_high = self._asian_low = 0.0
        self._daily_open = self._weekly_open = 0.0
        self._zones: list[Zone] = []
        self._equal_highs: list[PriceLevel] = []
        self._equal_lows: list[PriceLevel] = []
        self._current_day: date | None = None
        self._current_week: date | None = None

    def update(self, m5_series: BarSeries, utc_now: datetime) -> None:
        today = utc_now.date()
        week_start = today - __import__("datetime").timedelta(days=today.weekday())

        if self._current_day != today:
            self._roll_day(m5_series, utc_now)
            self._current_day = today

        if self._current_week != week_start:
            self._roll_week(m5_series, utc_now)
            self._current_week = week_start

        if utc_now.time() >= time(7, 0) and self._asian_high == 0.0:
            self._lock_asian_range(m5_series, utc_now)

        self._update_equal_levels(m5_series, utc_now)
        self._check_mitigation(m5_series)

    def add_zone(self, zone: Zone) -> None:
        zones_of_type = [z for z in self._zones if z.zone_type == zone.zone_type]
        if len(zones_of_type) >= _MAX_ZONES_PER_TYPE:
            oldest = min(zones_of_type, key=lambda z: z.formed_at)
            self._zones = [z for z in self._zones if z is not oldest]
        self._zones.append(zone)

    def active_zones(self, direction: str) -> list[Zone]:
        return [z for z in self._zones if not z.is_mitigated and z.direction == direction]

    def all_sweep_targets(self, direction: str) -> list[PriceLevel]:
        """Return marked levels that can be swept in the given direction."""
        levels: list[PriceLevel] = []
        now = datetime.now(timezone.utc)
        if self._pdh > 0:
            levels.append(PriceLevel("PDH", self._pdh, "PDH", now))
        if self._pdl > 0:
            levels.append(PriceLevel("PDL", self._pdl, "PDL", now))
        if self._pwh > 0:
            levels.append(PriceLevel("PWH", self._pwh, "PWH", now))
        if self._pwl > 0:
            levels.append(PriceLevel("PWL", self._pwl, "PWL", now))
        if self._asian_high > 0:
            levels.append(PriceLevel("Asian High", self._asian_high, "ASIAN_H", now))
        if self._asian_low > 0:
            levels.append(PriceLevel("Asian Low", self._asian_low, "ASIAN_L", now))
        levels.extend(self._equal_highs)
        levels.extend(self._equal_lows)
        return levels

    @property
    def pdh(self) -> float: return self._pdh
    @property
    def pdl(self) -> float: return self._pdl
    @property
    def pwh(self) -> float: return self._pwh
    @property
    def pwl(self) -> float: return self._pwl
    @property
    def asian_high(self) -> float: return self._asian_high
    @property
    def asian_low(self) -> float: return self._asian_low
    @property
    def daily_open(self) -> float: return self._daily_open
    @property
    def weekly_open(self) -> float: return self._weekly_open

    def _roll_day(self, series: BarSeries, utc_now: datetime) -> None:
        bars = series.as_list()
        yesterday_bars = [b for b in bars if b.time_utc.date() < utc_now.date()]
        if yesterday_bars:
            self._pdh = max(b.high for b in yesterday_bars)
            self._pdl = min(b.low for b in yesterday_bars)
        today_bars = [b for b in bars if b.time_utc.date() == utc_now.date()]
        if today_bars:
            self._daily_open = today_bars[0].open
        self._asian_high = self._asian_low = 0.0

    def _roll_week(self, series: BarSeries, utc_now: datetime) -> None:
        today = utc_now.date()
        week_start = today - __import__("datetime").timedelta(days=today.weekday())
        prev_week_start = week_start - __import__("datetime").timedelta(days=7)
        prev_bars = [
            b for b in series.as_list()
            if prev_week_start <= b.time_utc.date() < week_start
        ]
        if prev_bars:
            self._pwh = max(b.high for b in prev_bars)
            self._pwl = min(b.low for b in prev_bars)
        week_bars = [b for b in series.as_list() if b.time_utc.date() >= week_start]
        if week_bars:
            self._weekly_open = week_bars[0].open

    def _lock_asian_range(self, series: BarSeries, utc_now: datetime) -> None:
        today = utc_now.date()
        asian_bars = [
            b for b in series.as_list()
            if b.time_utc.date() == today and b.time_utc.time() < time(7, 0)
        ]
        if asian_bars:
            self._asian_high = max(b.high for b in asian_bars)
            self._asian_low = min(b.low for b in asian_bars)

    def _update_equal_levels(self, series: BarSeries, utc_now: datetime) -> None:
        tolerance = 2 * _PIP
        swing_highs = self._ie.swing_highs(series, lookback=2)
        swing_lows = self._ie.swing_lows(series, lookback=2)

        self._equal_highs = []
        for i, (idx_a, price_a) in enumerate(swing_highs):
            for idx_b, price_b in swing_highs[i + 1:]:
                if abs(price_a - price_b) <= tolerance:
                    level = PriceLevel(
                        f"Equal High ~{round(price_a, 5)}", price_a,
                        "EQUAL_H", utc_now
                    )
                    if not any(abs(e.price - price_a) <= tolerance for e in self._equal_highs):
                        self._equal_highs.append(level)

        self._equal_lows = []
        for i, (idx_a, price_a) in enumerate(swing_lows):
            for idx_b, price_b in swing_lows[i + 1:]:
                if abs(price_a - price_b) <= tolerance:
                    level = PriceLevel(
                        f"Equal Low ~{round(price_a, 5)}", price_a,
                        "EQUAL_L", utc_now
                    )
                    if not any(abs(e.price - price_a) <= tolerance for e in self._equal_lows):
                        self._equal_lows.append(level)

    def _check_mitigation(self, series: BarSeries) -> None:
        if not series or len(series) == 0:
            return
        latest = series.latest()
        updated: list[Zone] = []
        for zone in self._zones:
            if zone.is_mitigated:
                updated.append(zone)
                continue
            if zone.direction == "BUY" and latest.close < zone.lower:
                updated.append(zone.mitigated())
            elif zone.direction == "SELL" and latest.close > zone.upper:
                updated.append(zone.mitigated())
            else:
                updated.append(zone)
        self._zones = updated
