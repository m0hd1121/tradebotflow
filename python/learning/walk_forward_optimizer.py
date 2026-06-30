from __future__ import annotations

from datetime import datetime, timedelta

from python.infra.database import TradeJournalDAO
from python.infra.logger import BotLogger
from python.learning.backtesting_engine import BacktestingEngine
from python.learning.performance_analyzer import PerformanceAnalyzer
from python.models.config import SystemConfig


class WalkForwardOptimizer:
    """Rolls train/test windows over historical data and reports regime stability.

    Default: 6-month training window, 2-month test window, 1-month step.
    Generates a walk-forward efficiency report (OOS / IS performance ratio).
    """

    def __init__(
        self,
        config: SystemConfig,
        dao: TradeJournalDAO,
        log: BotLogger,
        m5_bars: list,
        m15_bars: list,
        h1_bars: list,
        train_months: int = 6,
        test_months: int = 2,
        step_months: int = 1,
    ) -> None:
        self._cfg = config
        self._dao = dao
        self._log = log
        self._m5 = m5_bars
        self._m15 = m15_bars
        self._h1 = h1_bars
        self._train_months = train_months
        self._test_months = test_months
        self._step_months = step_months

    def run(self) -> dict:
        if not self._m5:
            return {"error": "No M5 data"}

        start = self._m5[0].time_utc
        end = self._m5[-1].time_utc
        self._log.info("system", f"Walk-forward run: {start.date()} to {end.date()}")

        windows = self._build_windows(start, end)
        results: list[dict] = []

        for i, (train_start, train_end, test_start, test_end) in enumerate(windows):
            train_m5 = [b for b in self._m5 if train_start <= b.time_utc < train_end]
            test_m5 = [b for b in self._m5 if test_start <= b.time_utc < test_end]
            train_m15 = [b for b in self._m15 if train_start <= b.time_utc < train_end]
            test_m15 = [b for b in self._m15 if test_start <= b.time_utc < test_end]
            train_h1 = [b for b in self._h1 if train_start <= b.time_utc < train_end]
            test_h1 = [b for b in self._h1 if test_start <= b.time_utc < test_end]

            if len(train_m5) < 500 or len(test_m5) < 100:
                continue

            train_bt = BacktestingEngine(self._cfg, self._dao, self._log, train_m5, train_m15, train_h1)
            train_result = train_bt.run()

            test_bt = BacktestingEngine(self._cfg, self._dao, self._log, test_m5, test_m15, test_h1)
            test_result = test_bt.run()

            analyzer = PerformanceAnalyzer(self._dao, self._log)
            train_snap = analyzer.run(is_backtest=True, backtest_id=train_result.get("backtest_id"))
            test_snap = analyzer.run(is_backtest=True, backtest_id=test_result.get("backtest_id"))

            is_exp = train_snap.get("expectancy_r", 0.0)
            oos_exp = test_snap.get("expectancy_r", 0.0)
            efficiency = oos_exp / is_exp if is_exp != 0 else 0.0

            results.append({
                "window": i + 1,
                "train_start": train_start.isoformat(),
                "train_end": train_end.isoformat(),
                "test_start": test_start.isoformat(),
                "test_end": test_end.isoformat(),
                "train_trades": train_result.get("trades", 0),
                "test_trades": test_result.get("trades", 0),
                "is_expectancy": round(is_exp, 4),
                "oos_expectancy": round(oos_exp, 4),
                "wfe": round(efficiency, 4),
            })

            self._log.info("system", f"WF window {i+1}: IS={is_exp:.3f}R OOS={oos_exp:.3f}R WFE={efficiency:.2f}")

        avg_wfe = sum(r["wfe"] for r in results) / len(results) if results else 0.0
        self._log.info("system", f"Walk-forward complete: {len(results)} windows, avg WFE={avg_wfe:.2f}")
        return {"windows": results, "avg_wfe": round(avg_wfe, 4)}

    def _build_windows(self, start: datetime, end: datetime) -> list[tuple]:
        windows: list[tuple] = []
        train_delta = timedelta(days=30 * self._train_months)
        test_delta = timedelta(days=30 * self._test_months)
        step_delta = timedelta(days=30 * self._step_months)

        cursor = start
        while cursor + train_delta + test_delta <= end:
            train_end = cursor + train_delta
            test_end = train_end + test_delta
            windows.append((cursor, train_end, train_end, test_end))
            cursor += step_delta

        return windows
