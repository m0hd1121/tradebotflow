from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from python.infra.database import TradeJournalDAO
from python.infra.logger import BotLogger


class MonteCarloAnalyzer:
    """Re-samples historical R-multiples to estimate risk metrics under uncertainty.

    10,000 bootstrap simulations produce confidence-interval estimates of:
    - Maximum drawdown (R and %)
    - Longest losing streak
    - Probability of ruin (equity < floor)
    """

    def __init__(self, dao: TradeJournalDAO, log: BotLogger, n_simulations: int = 10_000) -> None:
        self._dao = dao
        self._log = log
        self._n = n_simulations

    def run(
        self,
        r_multiples: list[float] | None = None,
        risk_pct: float = 0.005,
        ruin_floor_pct: float = 0.50,
        is_backtest: bool = False,
        backtest_id: str | None = None,
    ) -> dict:
        if r_multiples is None:
            trades = self._dao.get_closed_trades(is_backtest=is_backtest, backtest_id=backtest_id)
            r_multiples = [t.get("r_used", t.get("modelled_r", 0)) for t in trades]

        if len(r_multiples) < 30:
            return {"error": "Insufficient sample (< 30 trades)"}

        arr = np.array(r_multiples)
        n_trades = len(arr)

        self._log.info("system", f"Monte Carlo: {self._n} sims, {n_trades} source trades")

        # Bootstrap: resample with replacement
        samples = np.random.choice(arr, size=(self._n, n_trades), replace=True)

        # Equity curves (starting at 1.0)
        equity = np.ones((self._n, 1))
        for i in range(n_trades):
            r_col = samples[:, i]
            equity = np.hstack([equity, equity[:, -1:] * (1 + r_col * risk_pct)])

        max_dds = self._max_drawdowns(equity)
        loss_streaks = self._longest_loss_streaks(samples)
        ruin_probs = np.mean(np.min(equity, axis=1) < ruin_floor_pct)

        results = {
            "n_simulations": self._n,
            "n_source_trades": n_trades,
            "max_drawdown_median_pct": round(float(np.median(max_dds)) * 100, 2),
            "max_drawdown_p95_pct": round(float(np.percentile(max_dds, 95)) * 100, 2),
            "max_drawdown_p99_pct": round(float(np.percentile(max_dds, 99)) * 100, 2),
            "loss_streak_median": round(float(np.median(loss_streaks)), 1),
            "loss_streak_p95": int(np.percentile(loss_streaks, 95)),
            "loss_streak_p99": int(np.percentile(loss_streaks, 99)),
            "ruin_probability_pct": round(float(ruin_probs) * 100, 2),
            "final_equity_median": round(float(np.median(equity[:, -1])), 4),
            "final_equity_p5": round(float(np.percentile(equity[:, -1], 5)), 4),
        }

        self._dao.save_analytics_snapshot({"type": "monte_carlo", **results})
        self._log.info("system", f"MC done: p95 DD={results['max_drawdown_p95_pct']:.1f}% "
                       f"p95 streak={results['loss_streak_p95']} "
                       f"ruin={results['ruin_probability_pct']:.2f}%")
        return results

    @staticmethod
    def _max_drawdowns(equity: np.ndarray) -> np.ndarray:
        peaks = np.maximum.accumulate(equity, axis=1)
        drawdowns = (peaks - equity) / peaks
        return np.max(drawdowns, axis=1)

    @staticmethod
    def _longest_loss_streaks(samples: np.ndarray) -> np.ndarray:
        streaks = np.zeros(samples.shape[0])
        current = np.zeros(samples.shape[0])
        for i in range(samples.shape[1]):
            is_loss = samples[:, i] < 0
            current = np.where(is_loss, current + 1, 0)
            streaks = np.maximum(streaks, current)
        return streaks
