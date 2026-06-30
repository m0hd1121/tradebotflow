from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from python.models.bar import BarSeries, OHLCVBar
from python.models.signal import BiasState, SetupGrade


def _bar(close: float, high: float | None = None, low: float | None = None,
         open_: float | None = None, idx: int = 0) -> OHLCVBar:
    return OHLCVBar(
        time_utc=datetime(2024, 1, 2, 8, idx % 60, tzinfo=timezone.utc),
        open=open_ if open_ is not None else close - 0.0005,
        high=high if high is not None else close + 0.0010,
        low=low if low is not None else close - 0.0010,
        close=close,
        tick_volume=100,
    )


def _make_engine():
    from python.strategy.signal_engine import SignalEngine
    from python.core.indicator_engine import IndicatorEngine
    from python.core.bias_engine import BiasEngine
    from python.core.level_manager import LevelManager
    from python.core.session_filter import SessionFilter
    from python.core.news_filter import NewsFilter

    ie = IndicatorEngine()

    bias = MagicMock(spec=BiasEngine)
    bias.compute.return_value = ("BULLISH", BiasState.BULLISH)

    lm = MagicMock(spec=LevelManager)
    lm.all_sweep_targets.return_value = [
        MagicMock(name="PDL", price=1.0950, direction="BUY", label="PDL")
    ]
    lm.get_active_zones.return_value = []

    sf = MagicMock(spec=SessionFilter)
    sf.is_tradeable.return_value = (True, "LONDON")

    nf = MagicMock()
    nf.is_blackout.return_value = (False, "")

    strategy_cfg = MagicMock()
    strategy_cfg.sweep_min_pips = 2.0
    strategy_cfg.sweep_close_bars = 2
    strategy_cfg.displacement_atr_mult = 1.5
    strategy_cfg.zone_tolerance_pips = 2.0
    strategy_cfg.a_plus_only_until_trades = 100
    strategy_cfg.disabled_sessions = []
    strategy_cfg.disabled_grades = []

    ind_cfg = MagicMock()
    ind_cfg.ema_period = 50
    ind_cfg.atr_period = 14
    ind_cfg.atr_simple_period = 5
    ind_cfg.swing_lookback = 2

    exit_cfg = MagicMock()
    exit_cfg.tp2_min_r = 2.0
    exit_cfg.tp2_max_r = 3.0

    return SignalEngine(ie, bias, lm, sf, nf, strategy_cfg, ind_cfg, exit_cfg)


class TestSignalEngineState:
    def test_no_signal_empty_series(self):
        engine = _make_engine()
        m5 = BarSeries(200)
        m15 = BarSeries(100)
        h1 = BarSeries(50)
        now = datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc)
        sig, reason = engine.evaluate(m5, m15, h1, now)
        assert sig is None

    def test_evaluate_returns_tuple(self):
        engine = _make_engine()
        m5 = BarSeries(200)
        for i in range(10):
            m5.push(_bar(1.1000 + i * 0.0001, idx=i))
        m15 = BarSeries(100)
        h1 = BarSeries(50)
        now = datetime(2024, 1, 2, 8, 30, tzinfo=timezone.utc)
        result = engine.evaluate(m5, m15, h1, now)
        assert isinstance(result, tuple)
        assert len(result) == 2
