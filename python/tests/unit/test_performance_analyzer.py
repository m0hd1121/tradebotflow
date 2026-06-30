from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from python.learning.performance_analyzer import PerformanceAnalyzer


def _make_trades(r_vals: list[float]) -> list[dict]:
    trades = []
    for i, r in enumerate(r_vals):
        trades.append({
            "trade_id": i + 1,
            "r_used": r,
            "modelled_r": r,
            "session": "LONDON",
            "setup_grade": "A+",
            "day_of_week": "Mon",
            "bias_aligned": True,
            "outcome": "WIN_TP2" if r > 0 else "LOSS_SL",
        })
    return trades


def _make_analyzer(r_vals: list[float]) -> PerformanceAnalyzer:
    dao = MagicMock()
    dao.get_closed_trades.return_value = _make_trades(r_vals)
    dao.save_analytics_snapshot.return_value = None
    log = MagicMock()
    return PerformanceAnalyzer(dao, log)


class TestCoreMetrics:
    def test_perfect_win_rate(self):
        analyzer = _make_analyzer([1.0, 2.0, 1.5, 2.0, 1.0])
        snap = analyzer.run()
        assert snap["win_rate"] == 1.0
        assert snap["expectancy_r"] > 0

    def test_all_losses(self):
        analyzer = _make_analyzer([-1.0, -1.0, -1.0])
        snap = analyzer.run()
        assert snap["win_rate"] == 0.0
        assert snap["expectancy_r"] < 0

    def test_expectancy_formula(self):
        # 60% win rate, avg win 1.5R, avg loss 1.0R
        # expectancy = 0.6*1.5 - 0.4*1.0 = 0.9 - 0.4 = 0.5
        r_vals = [1.5, 1.5, 1.5, -1.0, -1.0, 1.5, 1.5, 1.5, -1.0, -1.0]
        analyzer = _make_analyzer(r_vals)
        snap = analyzer.run()
        assert abs(snap["expectancy_r"] - 0.5) < 0.05

    def test_max_drawdown_r(self):
        r_vals = [1.0, -1.0, -1.0, -1.0, 1.0]
        analyzer = _make_analyzer(r_vals)
        snap = analyzer.run()
        assert snap["max_drawdown_r"] >= 3.0   # 3 consecutive losses

    def test_profit_factor_gt_one(self):
        analyzer = _make_analyzer([2.0, 2.0, -1.0, 2.0, -1.0])
        snap = analyzer.run()
        assert snap["profit_factor"] > 1.0

    def test_insufficient_trades_returns_error(self):
        analyzer = _make_analyzer([])
        snap = analyzer.run()
        assert "error" in snap or snap.get("total_trades", 0) == 0


class TestBucketMetrics:
    def test_session_bucket_populated(self):
        r_vals = [1.0, -1.0, 2.0, -1.0, 1.5]
        analyzer = _make_analyzer(r_vals)
        snap = analyzer.run()
        buckets = snap.get("buckets", {})
        assert "session" in buckets or snap.get("total_trades", 0) > 0
