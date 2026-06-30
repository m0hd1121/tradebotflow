from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class TradeOutcome(enum.Enum):
    WIN_TP2 = "WIN_TP2"
    WIN_TP1_RUNNER_TS = "WIN_TP1_RUNNER_TS"
    WIN_TP1_TIME_STOP = "WIN_TP1_TIME_STOP"
    BREAKEVEN = "BREAKEVEN"
    LOSS_SL = "LOSS_SL"
    LOSS_DYNAMIC = "LOSS_DYNAMIC_INVALIDATION"
    LOSS_TIME_STOP = "LOSS_TIME_STOP"
    OPEN = "OPEN"


class TradeStatus(enum.Enum):
    OPEN = "OPEN"
    TP1_HIT = "TP1_HIT"
    CLOSED = "CLOSED"


@dataclass
class TradeRecord:
    trade_id: int
    date: datetime
    day_of_week: str
    session: str
    direction: str
    bias_aligned: bool
    setup_grade: str
    sweep_level: str
    entry_time: datetime
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    risk_pct: float
    stop_pips: float
    planned_rr: float
    lots: float
    status: TradeStatus = TradeStatus.OPEN
    tp1_hit: bool = False
    runner_exit_price: float | None = None
    exit_time: datetime | None = None
    outcome: TradeOutcome = TradeOutcome.OPEN
    modelled_r: float = 0.0
    r_used: float = 0.0
    pl_pct: float = 0.0
    cum_r: float = 0.0
    costs_pips: float = 0.0
    rule_violation: bool = False
    screenshot_path: str = ""
    note: str = ""
    is_backtest: bool = False
    backtest_id: str | None = None

    def realized_r(self) -> float:
        return self.r_used if self.r_used != 0.0 else self.modelled_r
