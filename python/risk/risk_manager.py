from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from python.infra.database import TradeJournalDAO
from python.models.config import MMConfig
from python.models.risk import KillSwitchReason, SizingResult

_PIP = 0.0001


class RiskManager:
    """Enforces all capital-protection rules (§6 + additional MM layers)."""

    def __init__(self, dao: TradeJournalDAO, cfg: MMConfig, initial_equity: float = 10000.0) -> None:
        self._dao = dao
        self._cfg = cfg
        self._equity = initial_equity
        self._equity_peak = initial_equity
        self._daily_pnl = 0.0
        self._weekly_pnl = 0.0
        self._monthly_pnl = 0.0
        self._consecutive_losses = 0
        self._trades_today = 0
        self._circuit_breaker_locked = False
        self._volatility_cooldown_until: datetime | None = None
        self._last_daily_reset: datetime | None = None
        self._last_weekly_reset: datetime | None = None
        self._last_monthly_reset: datetime | None = None

    def sync_from_db(self, utc_now: datetime) -> None:
        """Reload state from DB — call on startup and after each trade."""
        date_str = utc_now.strftime("%Y-%m-%d")
        self._daily_pnl = self._dao.get_daily_pnl(date_str)
        self._trades_today = self._dao.get_trades_count_today(date_str)

        recent = self._dao.get_recent_outcomes(10)
        consec = 0
        for outcome in recent:
            if "LOSS" in outcome or "BREAKEVEN" in outcome:
                consec += 1
            else:
                break
        self._consecutive_losses = consec

        closed = self._dao.get_closed_trades(is_backtest=False)
        if closed:
            cum_pl = sum(t.get("pl_pct", 0) for t in closed)
            self._equity = self._cfg.__class__.__module__ and 1  # placeholder, use initial + cum
            week_start = utc_now - timedelta(days=utc_now.weekday())
            month_start = utc_now.replace(day=1, hour=0, minute=0, second=0)
            self._weekly_pnl = sum(
                t.get("pl_pct", 0) for t in closed
                if t.get("exit_time") and t["exit_time"] >= week_start.isoformat()
            )
            self._monthly_pnl = sum(
                t.get("pl_pct", 0) for t in closed
                if t.get("exit_time") and t["exit_time"] >= month_start.isoformat()
            )

    def check_gate(self, utc_now: datetime) -> SizingResult | None:
        """Return SizingResult(blocked=True, reason) if any kill-switch is active, else None."""
        if self._circuit_breaker_locked:
            return SizingResult.blocked(KillSwitchReason.CIRCUIT_BREAKER_LOCKED)

        drawdown = (self._equity_peak - self._equity) / self._equity_peak if self._equity_peak > 0 else 0.0
        if drawdown >= self._cfg.circuit_breaker_pct:
            self._circuit_breaker_locked = True
            return SizingResult.blocked(KillSwitchReason.CIRCUIT_BREAKER)

        if self._daily_pnl <= -self._cfg.daily_loss_limit_pct:
            return SizingResult.blocked(KillSwitchReason.DAILY_LIMIT)

        if self._weekly_pnl <= -self._cfg.weekly_loss_limit_pct:
            return SizingResult.blocked(KillSwitchReason.WEEKLY_LIMIT)

        if self._monthly_pnl <= -self._cfg.monthly_loss_limit_pct:
            return SizingResult.blocked(KillSwitchReason.MONTHLY_LIMIT)

        if self._consecutive_losses >= self._cfg.max_consecutive_losses:
            return SizingResult.blocked(KillSwitchReason.CONSEC_LOSS)

        if self._trades_today >= self._cfg.max_trades_per_day:
            return SizingResult.blocked(KillSwitchReason.TRADE_COUNT)

        if self._volatility_cooldown_until and utc_now < self._volatility_cooldown_until:
            return SizingResult.blocked(KillSwitchReason.VOLATILITY_COOLDOWN)

        return None

    def compute_size(
        self, stop_pips: float, pip_value_per_lot: float = 10.0
    ) -> SizingResult:
        """Compute lot size with drawdown-based risk reduction applied."""
        drawdown = (self._equity_peak - self._equity) / self._equity_peak if self._equity_peak > 0 else 0.0
        adj_risk, reduction_label = self._adjusted_risk(drawdown)

        if stop_pips <= 0 or pip_value_per_lot <= 0:
            return SizingResult(
                lots=0.0, risk_pct_used=adj_risk, adj_risk_pct=adj_risk,
                stop_pips=stop_pips, drawdown_reduction_applied=reduction_label,
                is_blocked=False,
            )

        risk_amount = self._equity * adj_risk
        lots_raw = risk_amount / (stop_pips * pip_value_per_lot)
        lots = self._round_down(lots_raw, 0.01)
        lots = min(lots, 100.0)

        return SizingResult(
            lots=lots, risk_pct_used=adj_risk, adj_risk_pct=adj_risk,
            stop_pips=stop_pips, drawdown_reduction_applied=reduction_label,
            is_blocked=False,
        )

    def record_trade_result(self, outcome_r: float, pl_pct: float, utc_now: datetime) -> None:
        """Update state after a trade closes."""
        self._daily_pnl += pl_pct
        self._weekly_pnl += pl_pct
        self._monthly_pnl += pl_pct
        self._equity += self._equity * pl_pct
        self._equity_peak = max(self._equity_peak, self._equity)
        self._trades_today += 1

        is_loss = outcome_r < 0
        if is_loss:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

    def trigger_volatility_cooldown(self, utc_now: datetime) -> None:
        self._volatility_cooldown_until = utc_now + timedelta(hours=self._cfg.volatility_cooldown_hours)

    def check_volatility_spike(self, current_adr: float, adr_ma: float) -> bool:
        """Return True if ADR spike detected; caller should trigger cooldown."""
        if adr_ma <= 0:
            return False
        return current_adr > adr_ma * self._cfg.volatility_cooldown_adr_multiplier

    def reset_circuit_breaker(self) -> None:
        self._circuit_breaker_locked = False

    def reset_daily(self) -> None:
        self._daily_pnl = 0.0
        self._trades_today = 0

    def reset_weekly(self) -> None:
        self._weekly_pnl = 0.0

    def reset_monthly(self) -> None:
        self._monthly_pnl = 0.0

    @property
    def equity(self) -> float:
        return self._equity

    @property
    def equity_peak(self) -> float:
        return self._equity_peak

    @property
    def drawdown_pct(self) -> float:
        if self._equity_peak <= 0:
            return 0.0
        return (self._equity_peak - self._equity) / self._equity_peak

    def _adjusted_risk(self, drawdown: float) -> tuple[float, str]:
        base = min(self._cfg.base_risk_pct, self._cfg.max_risk_pct)
        if drawdown >= self._cfg.drawdown_reduction_threshold_2:
            factor = self._cfg.drawdown_reduction_factor_2
            label = f"DD_REDUCTION_2:{drawdown:.1%}"
        elif drawdown >= self._cfg.drawdown_reduction_threshold_1:
            factor = self._cfg.drawdown_reduction_factor_1
            label = f"DD_REDUCTION_1:{drawdown:.1%}"
        else:
            factor = 1.0
            label = "NONE"
        return base * factor, label

    @staticmethod
    def _round_down(value: float, step: float) -> float:
        return math.floor(value / step) * step
