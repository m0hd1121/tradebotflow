from __future__ import annotations

from datetime import datetime, timezone

from python.infra.database import TradeJournalDAO
from python.infra.logger import BotLogger


class PerformanceAnalyzer:
    """Computes the full §11 metrics suite from closed trade records."""

    def __init__(self, dao: TradeJournalDAO, log: BotLogger) -> None:
        self._dao = dao
        self._log = log

    def run(self, is_backtest: bool = False, backtest_id: str | None = None) -> dict:
        trades = self._dao.get_closed_trades(is_backtest=is_backtest, backtest_id=backtest_id)
        if not trades:
            return {}

        snapshot = {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "total_trades": len(trades),
            "is_backtest": is_backtest,
            **self._core_metrics(trades),
            **self._streak_metrics(trades),
            **self._bucket_metrics(trades),
            **self._cost_audit(trades),
        }
        self._dao.save_analytics_snapshot(snapshot)
        self._log.info("system", f"Analytics updated: {len(trades)} trades, "
                       f"expectancy={snapshot.get('expectancy_r', 0):.3f}R")
        return snapshot

    def _core_metrics(self, trades: list[dict]) -> dict:
        r_vals = [t.get("r_used", t.get("modelled_r", 0)) for t in trades]
        wins = [r for r in r_vals if r > 0]
        losses = [r for r in r_vals if r < 0]
        win_rate = len(wins) / len(r_vals) if r_vals else 0.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 1.0
        expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
        profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")

        cum_r = 0.0
        peak_r = 0.0
        max_dd = 0.0
        for r in r_vals:
            cum_r += r
            peak_r = max(peak_r, cum_r)
            max_dd = max(max_dd, peak_r - cum_r)

        return {
            "win_rate": round(win_rate, 4),
            "avg_win_r": round(avg_win, 4),
            "avg_loss_r": round(-avg_loss, 4),
            "expectancy_r": round(expectancy, 4),
            "profit_factor": round(profit_factor, 4),
            "total_r": round(sum(r_vals), 4),
            "max_drawdown_r": round(max_dd, 4),
        }

    def _streak_metrics(self, trades: list[dict]) -> dict:
        r_vals = [t.get("r_used", t.get("modelled_r", 0)) for t in trades]
        max_loss_streak = max_win_streak = cur_loss = cur_win = 0
        for r in r_vals:
            if r < 0:
                cur_loss += 1
                cur_win = 0
            else:
                cur_win += 1
                cur_loss = 0
            max_loss_streak = max(max_loss_streak, cur_loss)
            max_win_streak = max(max_win_streak, cur_win)
        return {
            "max_loss_streak": max_loss_streak,
            "max_win_streak": max_win_streak,
        }

    def _bucket_metrics(self, trades: list[dict]) -> dict:
        buckets: dict[str, list[float]] = {}
        for t in trades:
            r = t.get("r_used", t.get("modelled_r", 0))
            for key in [
                f"session:{t.get('session', 'unknown')}",
                f"grade:{t.get('setup_grade', 'unknown')}",
                f"day:{t.get('day_of_week', 'unknown')}",
                f"bias:{t.get('bias_aligned', 'unknown')}",
            ]:
                buckets.setdefault(key, []).append(r)

        bucket_stats: dict[str, dict] = {}
        for key, r_list in buckets.items():
            wins = [r for r in r_list if r > 0]
            losses = [r for r in r_list if r < 0]
            win_rate = len(wins) / len(r_list) if r_list else 0
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = abs(sum(losses) / len(losses)) if losses else 1
            expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
            bucket_stats[key] = {
                "trades": len(r_list),
                "win_rate": round(win_rate, 4),
                "expectancy_r": round(expectancy, 4),
            }
        return {"buckets": bucket_stats}

    def _cost_audit(self, trades: list[dict]) -> dict:
        costs = [t.get("costs_pips", 0) for t in trades if t.get("costs_pips")]
        avg_cost = sum(costs) / len(costs) if costs else 0.0
        return {"avg_cost_pips": round(avg_cost, 2)}

    def rolling_expectancy(self, trades: list[dict], window: int = 20) -> list[float]:
        r_vals = [t.get("r_used", t.get("modelled_r", 0)) for t in trades]
        result: list[float] = []
        for i in range(len(r_vals)):
            w = r_vals[max(0, i - window + 1): i + 1]
            wins = [r for r in w if r > 0]
            losses = [r for r in w if r < 0]
            win_rate = len(wins) / len(w) if w else 0
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = abs(sum(losses) / len(losses)) if losses else 1
            result.append(win_rate * avg_win - (1 - win_rate) * avg_loss)
        return result
