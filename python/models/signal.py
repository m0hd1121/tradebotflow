from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

from python.models.level import Zone


class BiasState(enum.Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class SetupGrade(enum.Enum):
    A_PLUS = "A+"
    STANDARD = "STANDARD"


@dataclass(frozen=True)
class Signal:
    direction: str                  # 'BUY' or 'SELL'
    bias: BiasState
    grade: SetupGrade
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    stop_pips: float
    planned_rr: float
    entry_zone: Zone
    sweep_level_name: str
    sweep_extreme: float            # Exact low (BUY) or high (SELL) of the sweep candle
    signal_time_utc: datetime
    displacement_atr_ratio: float
    session: str                    # 'London', 'Overlap', 'NY'
