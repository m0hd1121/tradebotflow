from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from python.learning.monte_carlo_analyzer import MonteCarloAnalyzer


def _make_mc(r_vals: list[float]) -> MonteCarloAnalyzer:
    dao = MagicMock()
    dao.get_closed_trades.return_value = [{"r_used": r} for r in r_vals]
    dao.save_analytics_snapshot.return_value = None
    log = MagicMock()
    return MonteCarloAnalyzer(dao, log, n_simulations=500)   # small for test speed


class TestMonteCarloAnalyzer:
    def test_insufficient_sample_returns_error(self):
        mc = _make_mc([1.0, -1.0, 1.0])
        result = mc.run(r_multiples=[1.0, -1.0, 1.0])
        assert "error" in result

    def test_returns_required_keys(self):
        r_vals = ([1.5] * 20 + [-1.0] * 10)   # 30 trades
        mc = _make_mc(r_vals)
        result = mc.run(r_multiples=r_vals)
        required = [
            "max_drawdown_median_pct", "max_drawdown_p95_pct",
            "loss_streak_median", "loss_streak_p95",
            "ruin_probability_pct", "final_equity_median",
        ]
        for key in required:
            assert key in result, f"Missing key: {key}"

    def test_positive_edge_low_ruin(self):
        r_vals = [2.0] * 60   # perfect system
        mc = _make_mc(r_vals)
        result = mc.run(r_multiples=r_vals)
        assert result["ruin_probability_pct"] == 0.0

    def test_negative_edge_high_ruin(self):
        r_vals = [-1.0] * 60  # always losing
        mc = _make_mc(r_vals)
        result = mc.run(r_multiples=r_vals)
        assert result["ruin_probability_pct"] > 50.0

    def test_p95_drawdown_gte_median(self):
        r_vals = [1.0, -1.0] * 20
        mc = _make_mc(r_vals)
        result = mc.run(r_multiples=r_vals)
        assert result["max_drawdown_p95_pct"] >= result["max_drawdown_median_pct"]
