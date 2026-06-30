from __future__ import annotations

from python.core.level_manager import LevelManager
from python.models.bar import BarSeries
from python.models.config import IndicatorConfig, StrategyConfig
from python.models.signal import SetupGrade, Signal

_PIP = 0.0001


class TradeValidationEngine:
    """Applies the §7 invalidation filters after a signal is generated."""

    def __init__(
        self,
        level_manager: LevelManager,
        strategy_cfg: StrategyConfig,
        indicator_cfg: IndicatorConfig,
        live_trade_count: int = 0,
    ) -> None:
        self._lm = level_manager
        self._sc = strategy_cfg
        self._ic = indicator_cfg
        self._live_trade_count = live_trade_count

    def update_live_trade_count(self, count: int) -> None:
        self._live_trade_count = count

    def validate(self, signal: Signal, m5_series: BarSeries) -> tuple[bool, str]:
        """Return (True, '') if valid, or (False, reason_code) if rejected."""

        check = self._zone_mitigated(signal)
        if not check:
            return False, "STALE_ZONE"

        check = self._stop_not_in_no_mans_land(signal, m5_series)
        if not check:
            return False, "STOP_OVEREXTENDED"

        check = not self._structure_choppy(m5_series)
        if not check:
            return False, "CHOPPY_STRUCTURE"

        check = self._grade_meets_bar(signal.grade)
        if not check:
            return False, f"GRADE_BELOW_BAR:{signal.grade.value}"

        check = self._rr_valid(signal)
        if not check:
            return False, f"POOR_RR:{signal.planned_rr:.2f}"

        return True, ""

    def _zone_mitigated(self, signal: Signal) -> bool:
        return not signal.entry_zone.is_mitigated

    def _stop_not_in_no_mans_land(self, signal: Signal, m5_series: BarSeries) -> bool:
        if m5_series is None or len(m5_series) < self._ic.adr_period:
            return True
        bars = m5_series.as_list()
        daily_ranges: list[float] = []
        by_date: dict = {}
        for b in bars:
            d = b.time_utc.date()
            if d not in by_date:
                by_date[d] = {"high": b.high, "low": b.low}
            else:
                by_date[d]["high"] = max(by_date[d]["high"], b.high)
                by_date[d]["low"] = min(by_date[d]["low"], b.low)
        for d in sorted(by_date)[-self._ic.adr_period:]:
            daily_ranges.append((by_date[d]["high"] - by_date[d]["low"]) / _PIP)
        adr = sum(daily_ranges) / len(daily_ranges) if daily_ranges else 0
        limit = adr * self._ic.stop_no_mans_land_adr_multiplier * _PIP
        return signal.stop_pips * _PIP <= limit

    def _structure_choppy(self, m5_series: BarSeries) -> bool:
        bars = m5_series.as_list()[-self._sc.choppy_lookback:]
        if len(bars) < 5:
            return False
        ratios = [b.wick_ratio() for b in bars if b.body_size() > 0]
        if not ratios:
            return False
        avg_ratio = sum(ratios) / len(ratios)
        return avg_ratio > self._sc.choppy_wick_body_ratio

    def _grade_meets_bar(self, grade: SetupGrade) -> bool:
        bar = self._sc.setup_grade_bar
        if bar == "A_PLUS":
            return grade == SetupGrade.A_PLUS
        return True

    def _rr_valid(self, signal: Signal) -> bool:
        return signal.planned_rr >= self._sc.min_rr
