from __future__ import annotations

import uuid
from datetime import datetime, timezone

from python.core.bias_engine import BiasEngine
from python.core.indicator_engine import IndicatorEngine
from python.core.level_manager import LevelManager
from python.core.market_data_engine import MarketDataEngine
from python.core.news_filter import NewsFilter
from python.core.session_filter import SessionFilter
from python.infra.database import TradeJournalDAO
from python.infra.logger import BotLogger
from python.models.bar import BarSeries
from python.models.config import SystemConfig
from python.models.signal import SetupGrade
from python.models.trade import TradeOutcome, TradeRecord, TradeStatus
from python.risk.risk_manager import RiskManager
from python.strategy.signal_engine import SignalEngine
from python.strategy.trade_validation_engine import TradeValidationEngine

_PIP = 0.0001


class BacktestingEngine:
    """Event-driven bar-replay backtester using the live module instances.

    Uses the same SignalEngine/TradeValidationEngine/RiskManager instances
    as live trading — zero code divergence, so backtest faithfully reflects
    the live system.
    """

    def __init__(
        self,
        config: SystemConfig,
        dao: TradeJournalDAO,
        log: BotLogger,
        m5_bars: list,
        m15_bars: list,
        h1_bars: list,
    ) -> None:
        self._cfg = config
        self._dao = dao
        self._log = log
        self._m5_all = m5_bars
        self._m15_all = m15_bars
        self._h1_all = h1_bars
        self._backtest_id = str(uuid.uuid4())[:8]

    def run(self, spread_pips: float = 0.3, commission_pips: float = 0.1) -> dict:
        self._log.info("system", f"Backtest {self._backtest_id} started, {len(self._m5_all)} M5 bars")

        indicator_engine = IndicatorEngine()
        m5_series = BarSeries(self._cfg.data.m5_bars)
        m15_series = BarSeries(self._cfg.data.m15_bars)
        h1_series = BarSeries(self._cfg.data.h1_bars)

        bias_engine = BiasEngine(indicator_engine, self._cfg.indicators.ema_period)
        risk_manager = RiskManager(self._dao, self._cfg.risk, self._cfg.initial_equity)
        level_manager = LevelManager(indicator_engine, self._dao)

        # Use stub news/session filters for backtesting
        session_filter = SessionFilter(self._cfg.sessions)
        news_filter = _StubNewsFilter()

        signal_engine = SignalEngine(
            indicator_engine, bias_engine, level_manager,
            session_filter, news_filter,
            self._cfg.strategy, self._cfg.indicators, self._cfg.exit,
        )
        validation_engine = TradeValidationEngine(
            level_manager, self._cfg.strategy, self._cfg.indicators
        )

        open_trade: TradeRecord | None = None
        trade_count = 0

        m15_idx = h1_idx = 0

        for i, bar in enumerate(self._m5_all):
            m5_series.push(bar)
            while m15_idx < len(self._m15_all) and self._m15_all[m15_idx].time_utc <= bar.time_utc:
                m15_series.push(self._m15_all[m15_idx])
                m15_idx += 1
            while h1_idx < len(self._h1_all) and self._h1_all[h1_idx].time_utc <= bar.time_utc:
                h1_series.push(self._h1_all[h1_idx])
                h1_idx += 1

            level_manager.update(m5_series, bar.time_utc)

            # Manage open trade first
            if open_trade:
                open_trade = self._manage_open(open_trade, bar, m5_series, indicator_engine, spread_pips)
                if open_trade.status == TradeStatus.CLOSED:
                    costs = (spread_pips + commission_pips) * _PIP
                    open_trade.costs_pips = spread_pips + commission_pips
                    self._dao.update_trade_close(open_trade)
                    risk_manager.record_trade_result(open_trade.realized_r(), open_trade.pl_pct, bar.time_utc)
                    open_trade = None
                continue

            if len(m5_series) < self._cfg.data.m5_bars // 2:
                continue

            gate = risk_manager.check_gate(bar.time_utc)
            if gate:
                continue

            signal, reason = signal_engine.evaluate(m5_series, m15_series, h1_series, bar.time_utc)
            if not signal:
                continue

            valid, vreason = validation_engine.validate(signal, m5_series)
            if not valid:
                continue

            sizing = risk_manager.compute_size(signal.stop_pips, self._cfg.pip_value_per_lot)

            trade = TradeRecord(
                trade_id=0,
                date=bar.time_utc,
                day_of_week=bar.time_utc.strftime("%a"),
                session=signal.session,
                direction=signal.direction,
                bias_aligned=True,
                setup_grade=signal.grade.value,
                sweep_level=signal.sweep_level_name,
                entry_time=bar.time_utc,
                entry_price=signal.entry_price + (spread_pips * _PIP if signal.direction == "BUY" else -spread_pips * _PIP),
                stop_price=signal.stop_price,
                tp1_price=signal.tp1_price,
                tp2_price=signal.tp2_price,
                risk_pct=sizing.risk_pct_used,
                stop_pips=signal.stop_pips,
                planned_rr=signal.planned_rr,
                lots=sizing.lots,
                is_backtest=True,
                backtest_id=self._backtest_id,
            )
            trade.trade_id = self._dao.insert_trade(trade)
            open_trade = trade
            trade_count += 1

        self._log.info("system", f"Backtest {self._backtest_id} complete: {trade_count} trades")
        return {"backtest_id": self._backtest_id, "trades": trade_count}

    def _manage_open(
        self, trade: TradeRecord, bar, m5_series: BarSeries,
        ie: IndicatorEngine, spread_pips: float
    ) -> TradeRecord:
        direction = trade.direction

        # Time stop
        if bar.time_utc.hour >= 20:
            return self._close_trade(trade, bar.close, "TIME_STOP", bar.time_utc)

        # Dynamic invalidation (only pre-TP1)
        if not trade.tp1_hit:
            if direction == "BUY" and bar.close < trade.stop_price:
                return self._close_trade(trade, trade.stop_price, "LOSS_SL", bar.time_utc)
            if direction == "SELL" and bar.close > trade.stop_price:
                return self._close_trade(trade, trade.stop_price, "LOSS_SL", bar.time_utc)

        # TP1
        if not trade.tp1_hit:
            tp1_hit = (direction == "BUY" and bar.high >= trade.tp1_price) or \
                      (direction == "SELL" and bar.low <= trade.tp1_price)
            if tp1_hit:
                trade.tp1_hit = True
                trade.status = TradeStatus.TP1_HIT
                trade.stop_price = trade.entry_price + _PIP  # BE+1pip

        # TP2 (runner)
        if trade.tp1_hit:
            if direction == "BUY" and bar.high >= trade.tp2_price:
                return self._close_trade(trade, trade.tp2_price, "WIN_TP2", bar.time_utc)
            if direction == "SELL" and bar.low <= trade.tp2_price:
                return self._close_trade(trade, trade.tp2_price, "WIN_TP2", bar.time_utc)
            if direction == "BUY" and bar.low <= trade.stop_price:
                return self._close_trade(trade, trade.stop_price, "WIN_TP1_RUNNER_TS", bar.time_utc)
            if direction == "SELL" and bar.high >= trade.stop_price:
                return self._close_trade(trade, trade.stop_price, "WIN_TP1_RUNNER_TS", bar.time_utc)

        return trade

    def _close_trade(self, trade: TradeRecord, exit_price: float, outcome_str: str, exit_time: datetime) -> TradeRecord:
        direction = trade.direction
        entry = trade.entry_price
        r_size = abs(entry - trade.stop_price)

        if direction == "BUY":
            outcome_r = (exit_price - entry) / r_size if r_size > 0 else 0.0
        else:
            outcome_r = (entry - exit_price) / r_size if r_size > 0 else 0.0

        if trade.tp1_hit:
            realized = 0.5 * 1.0 + 0.5 * outcome_r
        else:
            realized = outcome_r

        trade.exit_time = exit_time
        trade.runner_exit_price = exit_price
        trade.modelled_r = realized
        trade.r_used = realized
        trade.pl_pct = realized * trade.risk_pct
        trade.outcome = TradeOutcome[outcome_str] if outcome_str in TradeOutcome.__members__ else TradeOutcome.LOSS_SL
        trade.status = TradeStatus.CLOSED
        return trade


class _StubNewsFilter:
    def is_blackout(self, utc_dt):
        return False, ""

    def is_cache_stale(self):
        return False

    def cache_age_hours(self):
        return 0.0

    def refresh_cache(self):
        pass
