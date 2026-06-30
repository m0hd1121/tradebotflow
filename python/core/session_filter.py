from __future__ import annotations

from datetime import date, datetime, time, timezone

from python.models.config import SessionConfig


def _parse_hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


class SessionFilter:
    """Determines whether a UTC timestamp falls inside an approved entry window."""

    def __init__(self, cfg: SessionConfig, holidays: set[date] | None = None) -> None:
        self._london_start = _parse_hm(cfg.london_start)
        self._london_end = _parse_hm(cfg.london_end)
        self._overlap_start = _parse_hm(cfg.overlap_start)
        self._overlap_end = _parse_hm(cfg.overlap_end)
        self._no_entry_after = _parse_hm(cfg.no_entry_after)
        self._hard_close = _parse_hm(cfg.hard_close)
        self._friday_cutoff = _parse_hm(cfg.friday_cutoff)
        self._holidays: set[date] = holidays or set()

    def is_tradeable(self, utc_dt: datetime) -> tuple[bool, str]:
        """Return (tradeable, session_name) or (False, reason)."""
        t = utc_dt.time()
        d = utc_dt.date()

        if d in self._holidays:
            return False, "HOLIDAY"

        if utc_dt.weekday() == 4 and t >= self._friday_cutoff:
            return False, "FRIDAY_CUTOFF"

        if utc_dt.weekday() >= 5:
            return False, "WEEKEND"

        if t >= self._no_entry_after:
            return False, "NO_ENTRY_AFTER_1500"

        if self._london_start <= t < self._london_end:
            return True, "London"

        if self._overlap_start <= t < self._overlap_end:
            return True, "Overlap"

        return False, "OUTSIDE_SESSION"

    def is_hard_close(self, utc_dt: datetime) -> bool:
        return utc_dt.time() >= self._hard_close

    def session_name(self, utc_dt: datetime) -> str:
        _, name = self.is_tradeable(utc_dt)
        return name

    def minutes_to_hard_close(self, utc_dt: datetime) -> int:
        t = utc_dt.time()
        close_minutes = self._hard_close.hour * 60 + self._hard_close.minute
        current_minutes = t.hour * 60 + t.minute
        return max(0, close_minutes - current_minutes)

    def is_entry_allowed(self, utc_dt: datetime) -> bool:
        ok, _ = self.is_tradeable(utc_dt)
        return ok
