from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime


class ZoneType(enum.Enum):
    ORDER_BLOCK = "ORDER_BLOCK"
    FAIR_VALUE_GAP = "FAIR_VALUE_GAP"
    OB_FVG_OVERLAP = "OB_FVG_OVERLAP"


@dataclass(frozen=True)
class Zone:
    zone_type: ZoneType
    upper: float
    lower: float
    direction: str          # 'BUY' or 'SELL'
    formed_at: datetime
    sweep_level_name: str
    is_mitigated: bool = False

    def proximal_edge(self) -> float:
        return self.upper if self.direction == "BUY" else self.lower

    def distal_edge(self) -> float:
        return self.lower if self.direction == "BUY" else self.upper

    def midpoint(self) -> float:
        return (self.upper + self.lower) / 2.0

    def contains(self, price: float) -> bool:
        return self.lower <= price <= self.upper

    def mitigated(self) -> Zone:
        """Return a new Zone with is_mitigated=True."""
        return Zone(
            zone_type=self.zone_type,
            upper=self.upper,
            lower=self.lower,
            direction=self.direction,
            formed_at=self.formed_at,
            sweep_level_name=self.sweep_level_name,
            is_mitigated=True,
        )


@dataclass(frozen=True)
class PriceLevel:
    name: str
    price: float
    level_type: str         # 'PDH','PDL','PWH','PWL','ASIAN_H','ASIAN_L','DAILY_OPEN', etc.
    formed_at: datetime
    direction_sweepable: str = "BOTH"   # 'UP', 'DOWN', or 'BOTH'
