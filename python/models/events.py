from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ExecEvent:
    """Written by MQL5 EA to ipc/exec_events/, read by EventReader."""
    event_type: str         # 'OPENED','TP1_HIT','BE_SET','TRAILING','CLOSED','EXEC_FAILED'
    trade_id: int
    timestamp_utc: str      # ISO-8601 string (MQL5 doesn't natively write datetime)
    direction: str
    lots: float
    price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    outcome_r: float = 0.0
    costs_pips: float = 0.0
    note: str = ""

    def event_time(self) -> datetime:
        from datetime import timezone
        return datetime.fromisoformat(self.timestamp_utc).replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class LearningEvent:
    """Audit-grade log entry for every learning engine decision."""
    timestamp_utc: datetime
    event_type: str         # 'PROPOSAL','APPLIED','ROLLBACK','NO_ACTION','MONITORING'
    description: str
    before_expectancy: float
    after_expectancy: float
    sample_size: int
    config_version: int
    details: str = ""       # JSON string with full rationale


@dataclass(frozen=True)
class NewsEvent:
    timestamp_utc: datetime
    currency: str
    title: str
    impact: str             # 'High','Medium','Low'
    is_major: bool          # NFP, FOMC, ECB, CPI — 30-min blackout instead of 15
