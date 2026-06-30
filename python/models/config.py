from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StrategyConfig:
    symbol: str = "EURUSD"
    pip_size: float = 0.0001
    min_sweep_pips: float = 2.0
    max_sweep_bars: int = 2
    max_displacement_bars: int = 3
    displacement_atr_multiplier: float = 1.5
    displacement_atr_period: int = 5
    swing_lookback: int = 2
    equal_hl_tolerance_pips: float = 2.0
    min_rr: float = 2.0
    confirmation_mode: str = "conservative"
    choppy_wick_body_ratio: float = 2.0
    choppy_lookback: int = 10
    setup_grade_bar: str = "A_PLUS"


@dataclass
class SessionConfig:
    london_start: str = "07:00"
    london_end: str = "10:00"
    overlap_start: str = "12:00"
    overlap_end: str = "15:00"
    no_entry_after: str = "15:00"
    hard_close: str = "20:00"
    friday_cutoff: str = "15:00"


@dataclass
class MMConfig:
    base_risk_pct: float = 0.005
    max_risk_pct: float = 0.010
    daily_loss_limit_pct: float = 0.02
    weekly_loss_limit_pct: float = 0.05
    monthly_loss_limit_pct: float = 0.06
    circuit_breaker_pct: float = 0.15
    max_consecutive_losses: int = 2
    max_trades_per_day: int = 3
    drawdown_reduction_threshold_1: float = 0.05
    drawdown_reduction_factor_1: float = 0.5
    drawdown_reduction_threshold_2: float = 0.10
    drawdown_reduction_factor_2: float = 0.25
    volatility_cooldown_adr_multiplier: float = 1.5
    volatility_cooldown_hours: int = 4
    volatility_adr_ma_period: int = 20


@dataclass
class IndicatorConfig:
    atr_period_fast: int = 5
    atr_period_slow: int = 14
    ema_period: int = 20
    adr_period: int = 14
    adr_min_pips: float = 60.0
    adr_max_pct_spent: float = 0.80
    stop_no_mans_land_adr_multiplier: float = 1.2


@dataclass
class ExitConfig:
    partial_close_pct: float = 0.5
    tp1_rr: float = 1.0
    tp2_rr_max: float = 3.0
    stop_buffer_pips_min: float = 2.0
    stop_buffer_atr_fraction: float = 0.5


@dataclass
class NewsConfig:
    calendar_url: str = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    blackout_minutes: int = 15
    major_blackout_minutes: int = 30
    major_keywords: list[str] = field(default_factory=lambda: [
        "Non-Farm Employment Change", "FOMC", "Federal Funds Rate",
        "ECB", "CPI", "GDP", "PCE"
    ])
    currencies: list[str] = field(default_factory=lambda: ["EUR", "USD"])


@dataclass
class IPCConfig:
    ipc_dir: str = "./ipc"
    signal_max_age_seconds: int = 90
    heartbeat_max_age_seconds: int = 60
    ea_config_poll_seconds: int = 60


@dataclass
class LearningConfig:
    enabled: bool = True
    min_trades_per_cycle: int = 100
    min_trades_per_bucket: int = 50
    min_improvement_r: float = 0.05
    out_of_sample_pct: float = 0.30
    review_window_hours: int = 24
    rollback_monitor_trades: int = 50


@dataclass
class DataConfig:
    m5_bars: int = 200
    m15_bars: int = 100
    h1_bars: int = 50
    cache_dir: str = "./data/cache"


@dataclass
class SystemConfig:
    version: int = 1
    initial_equity: float = 10000.0
    pip_value_per_lot: float = 10.0
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    sessions: SessionConfig = field(default_factory=SessionConfig)
    risk: MMConfig = field(default_factory=MMConfig)
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    exit: ExitConfig = field(default_factory=ExitConfig)
    news: NewsConfig = field(default_factory=NewsConfig)
    ipc: IPCConfig = field(default_factory=IPCConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    data: DataConfig = field(default_factory=DataConfig)
    circuit_breaker_locked: bool = False
