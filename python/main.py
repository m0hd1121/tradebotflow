from __future__ import annotations

import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from python.core.bias_engine import BiasEngine
from python.core.indicator_engine import IndicatorEngine
from python.core.level_manager import LevelManager
from python.core.market_data_engine import MarketDataEngine, MT5MarketDataFeed
from python.core.news_filter import NewsFilter
from python.core.session_filter import SessionFilter
from python.infra.config_manager import ConfigManager
from python.infra.dashboard import Dashboard
from python.infra.database import TradeJournalDAO
from python.infra.logger import BotLogger
from python.infra.safety_monitor import SafetyMonitor
from python.ipc.event_reader import EventReader
from python.ipc.signal_dispatcher import SignalDispatcher
from python.learning.learning_engine import LearningEngine
from python.learning.performance_analyzer import PerformanceAnalyzer
from python.models.config import SystemConfig
from python.risk.risk_manager import RiskManager
from python.strategy.signal_engine import SignalEngine
from python.strategy.trade_validation_engine import TradeValidationEngine


def _load_env() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        import os
        os.environ.setdefault(key.strip(), val.strip())


class TradeBotFlow:
    """Live trading orchestrator — wires all modules and runs the M5 bar-close event loop."""

    def __init__(self, config_path: str = "config/system.json") -> None:
        _load_env()

        self._cm = ConfigManager(config_path)
        cfg: SystemConfig = self._cm.get()

        self._log = BotLogger(cfg.log_dir)
        self._dao = TradeJournalDAO(cfg.db_path)
        self._dao.init_schema()

        self._indicator_engine = IndicatorEngine()
        from python.models.bar import BarSeries
        self._m5_series = BarSeries(cfg.data.m5_bars)
        self._m15_series = BarSeries(cfg.data.m15_bars)
        self._h1_series = BarSeries(cfg.data.h1_bars)

        self._bias_engine = BiasEngine(self._indicator_engine, cfg.indicators.ema_period)
        self._level_manager = LevelManager(self._indicator_engine, self._dao)
        self._session_filter = SessionFilter(cfg.sessions)
        self._news_filter = NewsFilter(cfg.news, self._log)

        self._signal_engine = SignalEngine(
            self._indicator_engine, self._bias_engine, self._level_manager,
            self._session_filter, self._news_filter,
            cfg.strategy, cfg.indicators, cfg.exit,
        )
        self._validation_engine = TradeValidationEngine(
            self._level_manager, cfg.strategy, cfg.indicators
        )
        self._risk_manager = RiskManager(self._dao, cfg.risk, cfg.initial_equity)

        self._dispatcher = SignalDispatcher(cfg.ipc, self._log)
        self._event_reader = EventReader(cfg.ipc, self._log)
        self._event_reader.on_event = self._handle_exec_event

        feed = MT5MarketDataFeed(cfg.data)
        self._mde = MarketDataEngine(cfg.data, feed)
        self._mde.subscribe_bar_close(self._on_bar_close)

        analyzer = PerformanceAnalyzer(self._dao, self._log)
        self._learning_engine = LearningEngine(
            analyzer, self._cm, self._dao, self._log, cfg.learning
        )

        self._safety_monitor = SafetyMonitor(
            cfg.ipc, self._dao, self._news_filter, self._log, cfg
        )

        self._dashboard = Dashboard(self._dao, cfg)
        self._cfg = cfg
        self._running = False
        self._last_learning_check = 0.0

    def start(self) -> None:
        self._log.info("system", "TradeBotFlow starting up")

        self._news_filter.refresh_cache()
        self._log.info("system", "News cache refreshed")

        self._cm.verify()
        self._log.info("system", "Config HMAC verified")

        self._log.info("system", f"Warming up bar series (equity={self._cfg.initial_equity:,.2f})")
        self._mde.warm_up()

        self._event_reader.start()
        self._log.info("system", "IPC event reader started")

        self._safety_monitor.start()
        self._log.info("system", "Safety monitor started")

        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        self._running = True
        self._log.info("system", "Entering main event loop — waiting for M5 bar close")

        self._mde.run(block=True)

    def _on_bar_close(self, bar, timeframe: str) -> None:
        if timeframe != "M5":
            return

        cfg = self._cm.get()
        now = bar.time_utc

        self._m5_series.push(bar)
        self._level_manager.update(self._m5_series, now)

        bias, _ = self._bias_engine.compute(self._m15_series, self._h1_series)
        ea_age = self._safety_monitor.ea_heartbeat_age_s()
        open_trade = self._dao.get_open_trade()
        armed = open_trade is None

        if len(self._m5_series) % 72 == 0:
            self._dashboard.print_status(
                bias=bias,
                armed=armed,
                ea_heartbeat_age_s=ea_age,
                risk_manager=self._risk_manager,
            )

        gate_reason = self._risk_manager.check_gate(now)
        if gate_reason:
            self._log.info("risk", f"Gate blocked: {gate_reason}")
            return

        if open_trade:
            return

        signal_obj, reason = self._signal_engine.evaluate(
            self._m5_series, self._m15_series, self._h1_series, now
        )
        if not signal_obj:
            return

        valid, vreason = self._validation_engine.validate(signal_obj, self._m5_series)
        if not valid:
            self._log.info("signal", f"Signal invalid: {vreason}")
            return

        sizing = self._risk_manager.compute_size(signal_obj.stop_pips, cfg.pip_value_per_lot)
        if sizing.is_blocked:
            self._log.info("risk", f"Sizing blocked: {sizing.block_reason}")
            return

        self._dispatcher.dispatch(signal_obj, sizing)
        self._log.audit("SIGNAL_DISPATCHED", {
            "direction": signal_obj.direction,
            "grade": signal_obj.grade.value,
            "entry": signal_obj.entry_price,
            "stop": signal_obj.stop_price,
            "tp1": signal_obj.tp1_price,
            "tp2": signal_obj.tp2_price,
            "lots": sizing.lots,
            "risk_pct": sizing.risk_pct_used,
        })

        self._maybe_run_learning(cfg)

    def _handle_exec_event(self, event) -> None:
        self._log.info("trade", f"Exec event: {event.event_type} ticket={event.ticket}")

        if event.event_type in ("TRADE_CLOSED", "TRADE_PARTIAL"):
            closed = self._dao.get_trade_by_ticket(event.ticket)
            if closed:
                r_val = closed.get("r_used") or closed.get("modelled_r") or 0.0
                pl_pct = closed.get("pl_pct") or 0.0
                self._risk_manager.record_trade_result(r_val, pl_pct, datetime.now(timezone.utc))
                self._log.audit("TRADE_RESULT", {
                    "ticket": event.ticket,
                    "r": r_val,
                    "pl_pct": pl_pct,
                    "outcome": closed.get("outcome"),
                })

        if event.event_type == "HEARTBEAT":
            self._safety_monitor.record_heartbeat()

    def _maybe_run_learning(self, cfg) -> None:
        now_ts = time.monotonic()
        if now_ts - self._last_learning_check < 3600:
            return
        self._last_learning_check = now_ts

        closed_count = len(self._dao.get_closed_trades(is_backtest=False))
        if self._learning_engine.check_trigger(closed_count):
            result = self._learning_engine.run_cycle()
            self._log.info("learning", f"Learning cycle result: {result}")

        if self._learning_engine.apply_pending_if_ready():
            self._log.info("learning", "Pending proposal applied")
            self._cm.write_ea_config()

        if self._learning_engine.check_rollback():
            self._log.info("learning", "Rollback triggered — config reverted")
            self._cm.write_ea_config()

    def _shutdown(self, signum, frame) -> None:
        self._log.info("system", "Shutdown signal received — stopping cleanly")
        self._running = False
        self._safety_monitor.stop()
        self._event_reader.stop()
        self._mde.stop()
        self._log.info("system", "TradeBotFlow stopped")
        sys.exit(0)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="TradeBotFlow live trading engine")
    parser.add_argument("--config", default="config/system.json", help="Config file path")
    args = parser.parse_args()

    bot = TradeBotFlow(config_path=args.config)
    bot.start()


if __name__ == "__main__":
    main()
