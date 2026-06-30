from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from python.infra.database import TradeJournalDAO
from python.infra.logger import BotLogger
from python.models.bar import OHLCVBar


def _make_bars(n: int, base_price: float = 1.1000,
               step: float = 0.0001) -> list[OHLCVBar]:
    bars = []
    price = base_price
    t = datetime(2024, 1, 2, 7, 0, tzinfo=timezone.utc)
    for i in range(n):
        direction = 1 if (i % 20 < 10) else -1
        price += direction * step
        high = price + 0.0015
        low  = price - 0.0015
        bars.append(OHLCVBar(
            time_utc=t,
            open=price - direction * step * 0.3,
            high=high,
            low=low,
            close=price,
            tick_volume=500,
        ))
        t += timedelta(minutes=5)
    return bars


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    dao = TradeJournalDAO(db_path)
    dao.init_schema()
    return dao


@pytest.fixture
def log():
    return MagicMock(spec=BotLogger)


def _make_config():
    cfg = MagicMock()
    cfg.initial_equity = 10000.0
    cfg.pip_value_per_lot = 10.0
    cfg.data.m5_bars = 200
    cfg.data.m15_bars = 100
    cfg.data.h1_bars = 50

    cfg.indicators.ema_period = 50
    cfg.indicators.atr_period = 14
    cfg.indicators.atr_simple_period = 5
    cfg.indicators.swing_lookback = 2
    cfg.indicators.adr_period = 10

    cfg.strategy.sweep_min_pips = 2.0
    cfg.strategy.sweep_close_bars = 2
    cfg.strategy.displacement_atr_mult = 1.5
    cfg.strategy.zone_tolerance_pips = 2.0
    cfg.strategy.a_plus_only_until_trades = 100
    cfg.strategy.disabled_sessions = []
    cfg.strategy.disabled_grades = []
    cfg.strategy.min_planned_rr = 2.0
    cfg.strategy.be_offset_pips = 1.0

    cfg.exit.time_stop_hour_utc = 20
    cfg.exit.tp1_partial_pct = 0.50
    cfg.exit.tp2_min_r = 2.0
    cfg.exit.tp2_max_r = 3.0

    cfg.sessions.london = MagicMock(start_utc="07:00", end_utc="10:00")
    cfg.sessions.new_york = MagicMock(start_utc="13:00", end_utc="16:00")
    cfg.sessions.overlap = MagicMock(start_utc="13:00", end_utc="15:00")
    cfg.sessions.friday_cutoff_utc = "20:00"
    cfg.sessions.holidays = []

    cfg.news.url = ""
    cfg.news.blackout_before_min = 30
    cfg.news.blackout_after_min = 15
    cfg.news.cache_ttl_hours = 24
    cfg.news.currencies = ["EUR", "USD"]
    cfg.news.impact_levels = ["High"]

    cfg.risk.risk_pct = 0.005
    cfg.risk.daily_loss_limit_pct = 0.02
    cfg.risk.weekly_loss_limit_pct = 0.04
    cfg.risk.monthly_loss_limit_pct = 0.08
    cfg.risk.max_drawdown_pct = 0.10
    cfg.risk.max_trades_per_day = 3
    cfg.risk.max_consecutive_losses = 3
    cfg.risk.drawdown_reduce_threshold_pct = 0.05
    cfg.risk.drawdown_reduce_factor = 0.50
    cfg.risk.volatility_cooldown_atr_mult = 2.5
    cfg.risk.volatility_cooldown_bars = 12
    return cfg


class TestBacktestPipeline:
    def test_backtest_runs_without_error(self, tmp_db, log):
        from python.learning.backtesting_engine import BacktestingEngine

        cfg = _make_config()
        m5 = _make_bars(500)
        m15 = _make_bars(150, step=0.0003)
        h1 = _make_bars(60, step=0.0008)

        engine = BacktestingEngine(cfg, tmp_db, log, m5, m15, h1)
        result = engine.run()

        assert "backtest_id" in result
        assert "trades" in result
        assert isinstance(result["trades"], int)

    def test_backtest_result_in_db(self, tmp_db, log):
        from python.learning.backtesting_engine import BacktestingEngine

        cfg = _make_config()
        m5 = _make_bars(500)
        m15 = _make_bars(150, step=0.0003)
        h1 = _make_bars(60, step=0.0008)

        engine = BacktestingEngine(cfg, tmp_db, log, m5, m15, h1)
        result = engine.run()

        trades = tmp_db.get_closed_trades(is_backtest=True,
                                           backtest_id=result["backtest_id"])
        assert len(trades) == result["trades"]


class TestPerformanceAnalyzerIntegration:
    def test_run_on_backtest_trades(self, tmp_db, log):
        from python.learning.backtesting_engine import BacktestingEngine
        from python.learning.performance_analyzer import PerformanceAnalyzer

        cfg = _make_config()
        m5 = _make_bars(600)
        m15 = _make_bars(200, step=0.0003)
        h1 = _make_bars(80, step=0.0008)

        engine = BacktestingEngine(cfg, tmp_db, log, m5, m15, h1)
        bt_result = engine.run()

        analyzer = PerformanceAnalyzer(tmp_db, log)
        snap = analyzer.run(is_backtest=True, backtest_id=bt_result["backtest_id"])

        assert "total_trades" in snap
        if snap.get("total_trades", 0) > 0:
            assert "win_rate" in snap
            assert "expectancy_r" in snap
