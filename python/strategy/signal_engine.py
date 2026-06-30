from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

from python.core.bias_engine import BiasEngine
from python.core.indicator_engine import IndicatorEngine
from python.core.level_manager import LevelManager
from python.core.news_filter import NewsFilter
from python.core.session_filter import SessionFilter
from python.models.bar import BarSeries, OHLCVBar
from python.models.config import ExitConfig, IndicatorConfig, StrategyConfig
from python.models.level import PriceLevel, Zone, ZoneType
from python.models.signal import BiasState, SetupGrade, Signal

_PIP = 0.0001


class _State(enum.Enum):
    IDLE = "IDLE"
    SWEEP_DETECTED = "SWEEP_DETECTED"
    MSS_DETECTED = "MSS_DETECTED"
    ZONE_DEFINED = "ZONE_DEFINED"
    RETRACING = "RETRACING"


@dataclass
class _SweepContext:
    sweep_bar_idx: int
    sweep_extreme: float
    level: PriceLevel
    direction: str
    mss_swing_price: float = 0.0
    displacement_bar_idx: int = 0
    zone: Zone | None = None
    displacement_atr_ratio: float = 0.0


class SignalEngine:
    """Faithful, mechanical implementation of the 9-gate §4 entry checklist."""

    def __init__(
        self,
        indicator_engine: IndicatorEngine,
        bias_engine: BiasEngine,
        level_manager: LevelManager,
        session_filter: SessionFilter,
        news_filter: NewsFilter,
        strategy_cfg: StrategyConfig,
        indicator_cfg: IndicatorConfig,
        exit_cfg: ExitConfig,
    ) -> None:
        self._ie = indicator_engine
        self._be = bias_engine
        self._lm = level_manager
        self._sf = session_filter
        self._nf = news_filter
        self._sc = strategy_cfg
        self._ic = indicator_cfg
        self._ec = exit_cfg
        self._state = _State.IDLE
        self._ctx: _SweepContext | None = None

    def evaluate(
        self,
        m5_series: BarSeries,
        m15_series: BarSeries,
        h1_series: BarSeries,
        utc_now: datetime,
    ) -> tuple[Signal | None, str]:
        """Return (Signal, '') on success or (None, rejection_reason)."""

        # Gate 1: bias
        bias = self._be.compute(h1_series, m15_series)
        if bias == BiasState.NEUTRAL:
            self._state = _State.IDLE
            return None, "NEUTRAL_BIAS"

        direction = "BUY" if bias == BiasState.BULLISH else "SELL"

        # Gate 2: session
        in_session, session_name = self._sf.is_tradeable(utc_now)
        if not in_session:
            return None, f"OUTSIDE_SESSION:{session_name}"

        # Gate 2b: news blackout
        blacked_out, event_title = self._nf.is_blackout(utc_now)
        if blacked_out:
            self._state = _State.IDLE
            return None, f"NEWS_BLACKOUT:{event_title}"

        bars = m5_series.as_list()
        if len(bars) < 10:
            return None, "INSUFFICIENT_BARS"

        current_bar_idx = len(bars) - 1

        # State machine evaluation
        if self._state == _State.IDLE:
            return self._check_sweep(bars, current_bar_idx, direction, bias, session_name, utc_now, m5_series)

        if self._state == _State.SWEEP_DETECTED and self._ctx:
            bars_since_sweep = current_bar_idx - self._ctx.sweep_bar_idx
            if bars_since_sweep > self._sc.max_displacement_bars:
                self._state = _State.IDLE
                self._ctx = None
                return None, "DISPLACEMENT_TIMEOUT"
            return self._check_displacement(bars, current_bar_idx, bias, session_name, utc_now, m5_series)

        if self._state == _State.ZONE_DEFINED and self._ctx and self._ctx.zone:
            return self._check_retracement_and_confirmation(
                bars, current_bar_idx, bias, session_name, utc_now, m5_series, m15_series, h1_series
            )

        return None, "WAITING"

    def _check_sweep(
        self, bars: list[OHLCVBar], idx: int, direction: str,
        bias: BiasState, session: str, utc_now: datetime, m5_series: BarSeries,
    ) -> tuple[Signal | None, str]:
        current = bars[idx]
        sweep_pips = self._sc.min_sweep_pips * _PIP
        levels = self._lm.all_sweep_targets(direction)

        for level in levels:
            if direction == "BUY":
                pierced = current.low < level.price - sweep_pips
                rejected = current.close > level.price
            else:
                pierced = current.high > level.price + sweep_pips
                rejected = current.close < level.price

            if pierced and rejected:
                sweep_extreme = current.low if direction == "BUY" else current.high
                mss_swing = self._find_pre_sweep_opposing_swing(bars, idx, direction)
                self._ctx = _SweepContext(
                    sweep_bar_idx=idx,
                    sweep_extreme=sweep_extreme,
                    level=level,
                    direction=direction,
                    mss_swing_price=mss_swing,
                )
                self._state = _State.SWEEP_DETECTED
                return None, "SWEEP_FOUND_WAITING_DISPLACEMENT"

        return None, "NO_SWEEP"

    def _find_pre_sweep_opposing_swing(
        self, bars: list[OHLCVBar], sweep_idx: int, direction: str
    ) -> float:
        lookback = self._sc.swing_lookback
        pre_bars = bars[max(0, sweep_idx - 20): sweep_idx]
        if direction == "BUY":
            swings = [b.high for i, b in enumerate(pre_bars)
                      if i >= lookback and i < len(pre_bars) - lookback
                      and all(pre_bars[i - j].high < b.high for j in range(1, lookback + 1))
                      and all(pre_bars[i + j].high < b.high for j in range(1, lookback + 1))]
            return max(swings) if swings else (bars[sweep_idx].close + 10 * _PIP)
        else:
            swings = [b.low for i, b in enumerate(pre_bars)
                      if i >= lookback and i < len(pre_bars) - lookback
                      and all(pre_bars[i - j].low > b.low for j in range(1, lookback + 1))
                      and all(pre_bars[i + j].low > b.low for j in range(1, lookback + 1))]
            return min(swings) if swings else (bars[sweep_idx].close - 10 * _PIP)

    def _check_displacement(
        self, bars: list[OHLCVBar], idx: int, bias: BiasState,
        session: str, utc_now: datetime, m5_series: BarSeries,
    ) -> tuple[Signal | None, str]:
        assert self._ctx is not None
        current = bars[idx]
        fast_atr = self._ie.avg_range(m5_series, self._ic.atr_period_fast)
        if fast_atr == 0.0:
            return None, "ATR_ZERO"

        bar_range = current.range_size()
        atr_ratio = bar_range / fast_atr if fast_atr > 0 else 0.0

        is_displaced = atr_ratio >= self._sc.displacement_atr_multiplier
        if not is_displaced:
            return None, "WEAK_DISPLACEMENT"

        direction = self._ctx.direction
        if direction == "BUY" and not current.is_bullish():
            return None, "DISPLACEMENT_NOT_BULLISH"
        if direction == "SELL" and not current.is_bearish():
            return None, "DISPLACEMENT_NOT_BEARISH"

        mss_broken = (
            (direction == "BUY" and current.close > self._ctx.mss_swing_price) or
            (direction == "SELL" and current.close < self._ctx.mss_swing_price)
        )
        if not mss_broken:
            return None, "NO_MSS_YET"

        zone = self._build_zone(bars, idx, direction, utc_now)
        if zone is None:
            self._state = _State.IDLE
            self._ctx = None
            return None, "NO_VALID_ZONE"

        self._ctx.displacement_bar_idx = idx
        self._ctx.zone = zone
        self._ctx.displacement_atr_ratio = atr_ratio
        self._lm.add_zone(zone)
        self._state = _State.ZONE_DEFINED
        return None, "ZONE_DEFINED_WAITING_RETRACEMENT"

    def _build_zone(
        self, bars: list[OHLCVBar], disp_idx: int, direction: str, utc_now: datetime
    ) -> Zone | None:
        assert self._ctx is not None
        ob_zone = self._find_ob(bars, disp_idx, direction, utc_now)
        fvg_zone = self._find_fvg(bars, disp_idx, direction, utc_now)

        if ob_zone and fvg_zone:
            overlap_upper = min(ob_zone.upper, fvg_zone.upper)
            overlap_lower = max(ob_zone.lower, fvg_zone.lower)
            if overlap_upper > overlap_lower:
                return Zone(
                    zone_type=ZoneType.OB_FVG_OVERLAP,
                    upper=overlap_upper, lower=overlap_lower,
                    direction=direction,
                    formed_at=utc_now,
                    sweep_level_name=self._ctx.level.name,
                )
        if ob_zone:
            return ob_zone
        if fvg_zone:
            return fvg_zone
        return None

    def _find_ob(
        self, bars: list[OHLCVBar], disp_idx: int, direction: str, utc_now: datetime
    ) -> Zone | None:
        assert self._ctx is not None
        start = self._ctx.sweep_bar_idx
        if direction == "BUY":
            candidates = [b for b in bars[start:disp_idx] if b.is_bearish()]
            if not candidates:
                return None
            ob = candidates[-1]
            return Zone(ZoneType.ORDER_BLOCK, ob.open, ob.low, direction, utc_now, self._ctx.level.name)
        else:
            candidates = [b for b in bars[start:disp_idx] if b.is_bullish()]
            if not candidates:
                return None
            ob = candidates[-1]
            return Zone(ZoneType.ORDER_BLOCK, ob.high, ob.open, direction, utc_now, self._ctx.level.name)

    def _find_fvg(
        self, bars: list[OHLCVBar], disp_idx: int, direction: str, utc_now: datetime
    ) -> Zone | None:
        assert self._ctx is not None
        if disp_idx < 2:
            return None
        c1, c2, c3 = bars[disp_idx - 2], bars[disp_idx - 1], bars[disp_idx]
        if direction == "BUY" and c1.high < c3.low:
            return Zone(ZoneType.FAIR_VALUE_GAP, c3.low, c1.high, direction, utc_now, self._ctx.level.name)
        if direction == "SELL" and c1.low > c3.high:
            return Zone(ZoneType.FAIR_VALUE_GAP, c1.low, c3.high, direction, utc_now, self._ctx.level.name)
        return None

    def _check_retracement_and_confirmation(
        self, bars: list[OHLCVBar], idx: int, bias: BiasState, session: str,
        utc_now: datetime, m5_series: BarSeries, m15_series: BarSeries, h1_series: BarSeries,
    ) -> tuple[Signal | None, str]:
        assert self._ctx is not None and self._ctx.zone is not None
        current = bars[idx]
        zone = self._ctx.zone
        direction = self._ctx.direction

        if direction == "BUY" and current.close < self._ctx.sweep_extreme:
            self._state = _State.IDLE
            self._ctx = None
            return None, "SWEEP_EXTREME_BROKEN"
        if direction == "SELL" and current.close > self._ctx.sweep_extreme:
            self._state = _State.IDLE
            self._ctx = None
            return None, "SWEEP_EXTREME_BROKEN"

        price_in_zone = zone.contains(current.close) or zone.contains(current.low if direction == "BUY" else current.high)
        if not price_in_zone:
            return None, "NOT_IN_ZONE_YET"

        confirmed = (
            (direction == "BUY" and current.is_bullish() and current.close >= zone.midpoint()) or
            (direction == "SELL" and current.is_bearish() and current.close <= zone.midpoint())
        )
        if not confirmed:
            return None, "CONFIRMATION_NOT_MET"

        return self._build_signal(bars, idx, bias, direction, zone, session, utc_now, m5_series, h1_series)

    def _build_signal(
        self, bars: list[OHLCVBar], idx: int, bias: BiasState, direction: str,
        zone: Zone, session: str, utc_now: datetime, m5_series: BarSeries, h1_series: BarSeries,
    ) -> tuple[Signal | None, str]:
        assert self._ctx is not None
        current = bars[idx]
        slow_atr = self._ie.atr(m5_series, self._ic.atr_period_slow)
        buf_pips = max(self._ec.stop_buffer_pips_min, slow_atr / _PIP * self._ec.stop_buffer_atr_fraction)
        buf = buf_pips * _PIP

        if direction == "BUY":
            stop = self._ctx.sweep_extreme - buf
            entry = current.close
            stop_pips = (entry - stop) / _PIP
        else:
            stop = self._ctx.sweep_extreme + buf
            entry = current.close
            stop_pips = (stop - entry) / _PIP

        if stop_pips <= 0:
            self._state = _State.IDLE
            self._ctx = None
            return None, "INVALID_STOP"

        r = stop_pips * _PIP
        tp1 = entry + r if direction == "BUY" else entry - r

        tp2_target = self._find_tp2_target(direction, entry, r, h1_series)
        if tp2_target is None:
            self._state = _State.IDLE
            self._ctx = None
            return None, "NO_TP2_TARGET"

        planned_rr = abs(tp2_target - entry) / r
        if planned_rr < self._sc.min_rr:
            self._state = _State.IDLE
            self._ctx = None
            return None, f"POOR_RR:{planned_rr:.2f}"

        grade = SetupGrade.A_PLUS if zone.zone_type == ZoneType.OB_FVG_OVERLAP else SetupGrade.STANDARD

        signal = Signal(
            direction=direction, bias=bias, grade=grade,
            entry_price=entry, stop_price=stop,
            tp1_price=tp1, tp2_price=tp2_target,
            stop_pips=stop_pips, planned_rr=planned_rr,
            entry_zone=zone, sweep_level_name=self._ctx.level.name,
            sweep_extreme=self._ctx.sweep_extreme,
            signal_time_utc=utc_now, session=session,
            displacement_atr_ratio=self._ctx.displacement_atr_ratio,
        )
        self._state = _State.IDLE
        self._ctx = None
        return signal, ""

    def _find_tp2_target(
        self, direction: str, entry: float, r: float, h1_series: BarSeries
    ) -> float | None:
        max_rr = self._ec.tp2_rr_max
        hard_limit = entry + max_rr * r if direction == "BUY" else entry - max_rr * r

        candidates: list[float] = []
        if direction == "BUY":
            for pool in [self._lm.pdh, self._lm.pwh, self._lm.asian_high]:
                if pool > entry + r and pool <= hard_limit:
                    candidates.append(pool)
            for lvl in self._lm._equal_highs:
                if lvl.price > entry + r and lvl.price <= hard_limit:
                    candidates.append(lvl.price)
        else:
            for pool in [self._lm.pdl, self._lm.pwl, self._lm.asian_low]:
                if pool < entry - r and pool >= hard_limit:
                    candidates.append(pool)
            for lvl in self._lm._equal_lows:
                if lvl.price < entry - r and lvl.price >= hard_limit:
                    candidates.append(lvl.price)

        if candidates:
            return min(candidates, key=lambda p: abs(p - entry))
        return hard_limit
