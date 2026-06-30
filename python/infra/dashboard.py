from __future__ import annotations

import json
from datetime import datetime, timezone

from python.infra.database import TradeJournalDAO
from python.models.config import SystemConfig


class Dashboard:
    """Read-only CLI status display. Prints formatted status report to stdout."""

    def __init__(self, dao: TradeJournalDAO, config: SystemConfig) -> None:
        self._dao = dao
        self._cfg = config

    def print_status(
        self,
        bias: str = "UNKNOWN",
        armed: bool = False,
        ea_heartbeat_age_s: float = 0.0,
        risk_manager=None,
    ) -> None:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        trades_today = self._dao.get_trades_count_today(date_str)
        daily_pnl = self._dao.get_daily_pnl(date_str)
        open_trade = self._dao.get_open_trade()

        snap = self._latest_analytics()
        expectancy = snap.get("expectancy_r", "N/A")
        total_trades = snap.get("total_trades", 0)
        win_rate = snap.get("win_rate", "N/A")

        dd_pct = 0.0
        equity = self._cfg.initial_equity
        if risk_manager:
            dd_pct = risk_manager.drawdown_pct * 100
            equity = risk_manager.equity

        print("\n" + "=" * 58)
        print(f"  TRADEBOTFLOW STATUS  {now.strftime('%Y-%m-%d %H:%M UTC')}")
        print("=" * 58)
        print(f"  Bias         : {bias}")
        print(f"  Armed        : {'YES' if armed else 'NO'}")
        print(f"  Equity       : {equity:,.2f}")
        print(f"  Drawdown     : {dd_pct:.1f}%")
        print(f"  Circuit Brk  : {'LOCKED' if self._cfg.circuit_breaker_locked else 'OK'}")
        print("-" * 58)
        print(f"  Trades today : {trades_today} / {self._cfg.risk.max_trades_per_day}")
        print(f"  Daily P/L    : {daily_pnl:.2%}")
        print(f"  Daily limit  : {self._cfg.risk.daily_loss_limit_pct:.2%}")
        print(f"  Open trade   : {'YES - ' + open_trade['direction'] if open_trade else 'None'}")
        print("-" * 58)
        print(f"  EA heartbeat : {ea_heartbeat_age_s:.0f}s ago")
        print("-" * 58)
        print(f"  Total trades : {total_trades}")
        print(f"  Win rate     : {win_rate if isinstance(win_rate, str) else f'{win_rate:.1%}'}")
        print(f"  Expectancy   : {expectancy if isinstance(expectancy, str) else f'{expectancy:.3f}R'}")
        print("=" * 58 + "\n")

    def _latest_analytics(self) -> dict:
        try:
            with self._dao._conn() as conn:
                row = conn.execute(
                    "SELECT snapshot_json FROM analytics ORDER BY id DESC LIMIT 1"
                ).fetchone()
            return json.loads(row[0]) if row else {}
        except Exception:
            return {}
