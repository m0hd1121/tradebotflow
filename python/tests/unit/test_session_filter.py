from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from python.core.session_filter import SessionFilter


def _make_sf():
    cfg = MagicMock()
    cfg.london = MagicMock(start_utc="07:00", end_utc="10:00")
    cfg.new_york = MagicMock(start_utc="13:00", end_utc="16:00")
    cfg.overlap = MagicMock(start_utc="13:00", end_utc="15:00")
    cfg.friday_cutoff_utc = "20:00"
    cfg.holidays = []
    return SessionFilter(cfg)


class TestSessionFilter:
    def test_london_session_tradeable(self):
        sf = _make_sf()
        dt = datetime(2024, 1, 2, 8, 30, tzinfo=timezone.utc)  # Tuesday 08:30
        ok, name = sf.is_tradeable(dt)
        assert ok
        assert "LONDON" in name.upper()

    def test_new_york_session_tradeable(self):
        sf = _make_sf()
        dt = datetime(2024, 1, 2, 14, 0, tzinfo=timezone.utc)  # Tuesday 14:00
        ok, name = sf.is_tradeable(dt)
        assert ok

    def test_dead_zone_not_tradeable(self):
        sf = _make_sf()
        dt = datetime(2024, 1, 2, 11, 0, tzinfo=timezone.utc)  # Between sessions
        ok, _ = sf.is_tradeable(dt)
        assert not ok

    def test_weekend_not_tradeable(self):
        sf = _make_sf()
        saturday = datetime(2024, 1, 6, 10, 0, tzinfo=timezone.utc)
        ok, _ = sf.is_tradeable(saturday)
        assert not ok

    def test_friday_after_cutoff_not_tradeable(self):
        sf = _make_sf()
        friday_late = datetime(2024, 1, 5, 20, 30, tzinfo=timezone.utc)
        ok, _ = sf.is_tradeable(friday_late)
        assert not ok

    def test_holiday_not_tradeable(self):
        cfg = MagicMock()
        cfg.london = MagicMock(start_utc="07:00", end_utc="10:00")
        cfg.new_york = MagicMock(start_utc="13:00", end_utc="16:00")
        cfg.overlap = MagicMock(start_utc="13:00", end_utc="15:00")
        cfg.friday_cutoff_utc = "20:00"
        cfg.holidays = ["2024-01-01"]
        sf = SessionFilter(cfg)
        holiday = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
        ok, _ = sf.is_tradeable(holiday)
        assert not ok
