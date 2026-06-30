from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


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


def _build_components(config_path: str):
    from python.infra.config_manager import ConfigManager
    from python.infra.database import TradeJournalDAO
    from python.infra.logger import BotLogger

    cm = ConfigManager(config_path)
    cfg = cm.get()
    log = BotLogger(cfg.log_dir)
    dao = TradeJournalDAO(cfg.db_path)
    dao.init_schema()
    return cfg, dao, log


def _load_bars(cfg):
    from python.core.market_data_engine import CSVMarketDataFeed, MarketDataEngine
    from python.models.bar import BarSeries, OHLCVBar

    data_dir = Path(cfg.data.csv_dir) if hasattr(cfg.data, "csv_dir") else Path("data")
    feed = CSVMarketDataFeed(cfg.data)
    mde = MarketDataEngine(cfg.data, feed)
    mde.warm_up()
    return mde.get_all_bars("M5"), mde.get_all_bars("M15"), mde.get_all_bars("H1")


def cmd_backtest(args) -> None:
    _load_env()
    cfg, dao, log = _build_components(args.config)

    m5, m15, h1 = _load_bars(cfg)
    log.info("system", f"Loaded bars: M5={len(m5)}, M15={len(m15)}, H1={len(h1)}")

    from python.learning.backtesting_engine import BacktestingEngine
    engine = BacktestingEngine(cfg, dao, log, m5, m15, h1)
    result = engine.run(
        spread_pips=args.spread,
        commission_pips=args.commission,
    )

    from python.learning.performance_analyzer import PerformanceAnalyzer
    analyzer = PerformanceAnalyzer(dao, log)
    snap = analyzer.run(is_backtest=True, backtest_id=result["backtest_id"])

    print(json.dumps({**result, **snap}, indent=2))
    log.info("system", f"Backtest complete: {result['trades']} trades, "
             f"expectancy={snap.get('expectancy_r', 'N/A')}")


def cmd_walkforward(args) -> None:
    _load_env()
    cfg, dao, log = _build_components(args.config)

    m5, m15, h1 = _load_bars(cfg)

    from python.learning.walk_forward_optimizer import WalkForwardOptimizer
    wfo = WalkForwardOptimizer(
        cfg, dao, log, m5, m15, h1,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    )
    result = wfo.run()

    print(json.dumps(result, indent=2))
    print(f"\nAverage WFE: {result.get('avg_wfe', 'N/A')}")


def cmd_montecarlo(args) -> None:
    _load_env()
    cfg, dao, log = _build_components(args.config)

    from python.learning.monte_carlo_analyzer import MonteCarloAnalyzer
    mc = MonteCarloAnalyzer(dao, log, n_simulations=args.simulations)
    result = mc.run(risk_pct=args.risk_pct, ruin_floor_pct=args.ruin_floor)

    print(json.dumps(result, indent=2))


def cmd_analyze(args) -> None:
    _load_env()
    cfg, dao, log = _build_components(args.config)

    from python.learning.performance_analyzer import PerformanceAnalyzer
    analyzer = PerformanceAnalyzer(dao, log)
    snap = analyzer.run(is_backtest=False)

    print(json.dumps(snap, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TradeBotFlow backtesting and analysis CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  backtest     Run a full backtest on historical CSV data
  walkforward  Walk-forward optimization (rolling train/test windows)
  montecarlo   Monte Carlo simulation on live or backtest trades
  analyze      Performance analysis on live trade history
""",
    )
    parser.add_argument("--config", default="config/system.json", help="Config file path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_bt = sub.add_parser("backtest", help="Run backtest on CSV data")
    p_bt.add_argument("--spread", type=float, default=0.3, help="Spread in pips")
    p_bt.add_argument("--commission", type=float, default=0.1, help="Commission in pips")

    p_wf = sub.add_parser("walkforward", help="Walk-forward optimization")
    p_wf.add_argument("--train-months", type=int, default=6)
    p_wf.add_argument("--test-months", type=int, default=2)
    p_wf.add_argument("--step-months", type=int, default=1)

    p_mc = sub.add_parser("montecarlo", help="Monte Carlo simulation")
    p_mc.add_argument("--simulations", type=int, default=10000)
    p_mc.add_argument("--risk-pct", type=float, default=0.005)
    p_mc.add_argument("--ruin-floor", type=float, default=0.50)

    sub.add_parser("analyze", help="Live trade performance analysis")

    args = parser.parse_args()

    dispatch = {
        "backtest": cmd_backtest,
        "walkforward": cmd_walkforward,
        "montecarlo": cmd_montecarlo,
        "analyze": cmd_analyze,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
