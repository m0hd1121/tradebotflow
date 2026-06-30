from __future__ import annotations

import enum
from dataclasses import dataclass


class KillSwitchReason(enum.Enum):
    DAILY_LIMIT = "DAILY_LIMIT"
    WEEKLY_LIMIT = "WEEKLY_LIMIT"
    MONTHLY_LIMIT = "MONTHLY_LIMIT"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    CONSEC_LOSS = "CONSEC_LOSS"
    TRADE_COUNT = "TRADE_COUNT"
    SESSION_CLOSED = "SESSION_CLOSED"
    VOLATILITY_COOLDOWN = "VOLATILITY_COOLDOWN"
    CIRCUIT_BREAKER_LOCKED = "CIRCUIT_BREAKER_LOCKED"


@dataclass(frozen=True)
class SizingResult:
    lots: float
    risk_pct_used: float
    adj_risk_pct: float
    stop_pips: float
    drawdown_reduction_applied: str
    is_blocked: bool
    block_reason: KillSwitchReason | None = None

    @classmethod
    def blocked(cls, reason: KillSwitchReason) -> SizingResult:
        return cls(
            lots=0.0,
            risk_pct_used=0.0,
            adj_risk_pct=0.0,
            stop_pips=0.0,
            drawdown_reduction_applied="NONE",
            is_blocked=True,
            block_reason=reason,
        )
