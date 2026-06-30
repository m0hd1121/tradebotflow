from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from python.models.config import MMConfig
from python.models.risk import KillSwitchReason


def _make_rm(equity: float = 10000.0, risk_pct: float = 0.005):
    from python.risk.risk_manager import RiskManager

    dao = MagicMock()
    dao.get_daily_pnl.return_value = 0.0
    dao.get_weekly_pnl.return_value = 0.0
    dao.get_monthly_pnl.return_value = 0.0
    dao.get_trades_count_today.return_value = 0
    dao.get_consecutive_losses.return_value = 0
    dao.get_open_trade.return_value = None

    cfg = MagicMock()
    cfg.risk_pct = risk_pct
    cfg.daily_loss_limit_pct = 0.02
    cfg.weekly_loss_limit_pct = 0.04
    cfg.monthly_loss_limit_pct = 0.08
    cfg.max_drawdown_pct = 0.10
    cfg.max_trades_per_day = 3
    cfg.max_consecutive_losses = 3
    cfg.drawdown_reduce_threshold_pct = 0.05
    cfg.drawdown_reduce_factor = 0.50
    cfg.volatility_cooldown_atr_mult = 2.5
    cfg.volatility_cooldown_bars = 12

    rm = RiskManager(dao, cfg, equity)
    return rm, dao


class TestCheckGate:
    def test_no_gate_clean_state(self):
        rm, _ = _make_rm()
        now = datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc)
        assert rm.check_gate(now) is None

    def test_daily_loss_limit_blocks(self):
        rm, dao = _make_rm()
        dao.get_daily_pnl.return_value = -0.025   # exceeds 2% limit
        now = datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc)
        reason = rm.check_gate(now)
        assert reason is not None
        assert "DAILY" in str(reason).upper() or "daily" in str(reason).lower()

    def test_max_trades_blocks(self):
        rm, dao = _make_rm()
        dao.get_trades_count_today.return_value = 3   # at limit
        now = datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc)
        reason = rm.check_gate(now)
        assert reason is not None

    def test_consecutive_losses_blocks(self):
        rm, dao = _make_rm()
        dao.get_consecutive_losses.return_value = 3
        now = datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc)
        reason = rm.check_gate(now)
        assert reason is not None

    def test_weekend_blocks(self):
        rm, _ = _make_rm()
        saturday = datetime(2024, 1, 6, 10, 0, tzinfo=timezone.utc)
        reason = rm.check_gate(saturday)
        assert reason is not None


class TestComputeSize:
    def test_normal_sizing(self):
        rm, _ = _make_rm(equity=10000.0, risk_pct=0.005)
        result = rm.compute_size(stop_pips=10.0, pip_value_per_lot=10.0)
        # Expected: 10000 * 0.005 / (10 * 10) = 0.05 lots
        assert not result.is_blocked
        assert abs(result.lots - 0.05) < 0.01

    def test_zero_stop_returns_blocked(self):
        rm, _ = _make_rm()
        result = rm.compute_size(stop_pips=0.0, pip_value_per_lot=10.0)
        assert result.is_blocked

    def test_drawdown_reduces_risk(self):
        rm, _ = _make_rm(equity=10000.0)
        # Simulate 6% drawdown (above 5% threshold → halve risk)
        rm._drawdown_pct = 0.06
        result = rm.compute_size(stop_pips=10.0, pip_value_per_lot=10.0)
        # Risk should be reduced by factor 0.5
        assert not result.is_blocked
        assert result.risk_pct_used <= 0.005 * 0.5 + 1e-9


class TestRecordTradeResult:
    def test_equity_updates(self):
        rm, _ = _make_rm(equity=10000.0, risk_pct=0.005)
        initial_equity = rm.equity
        now = datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc)
        rm.record_trade_result(r_val=1.0, pl_pct=0.005, trade_time=now)
        assert rm.equity > initial_equity

    def test_consecutive_loss_tracked(self):
        rm, dao = _make_rm()
        now = datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc)
        rm.record_trade_result(r_val=-1.0, pl_pct=-0.005, trade_time=now)
        assert rm._consecutive_losses >= 1

    def test_win_resets_consecutive_losses(self):
        rm, dao = _make_rm()
        now = datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc)
        rm._consecutive_losses = 2
        rm.record_trade_result(r_val=1.0, pl_pct=0.005, trade_time=now)
        assert rm._consecutive_losses == 0
