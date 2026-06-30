from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from python.models.events import NewsEvent
from python.models.config import NewsConfig
from python.infra.logger import BotLogger

try:
    import requests as _requests
except ImportError:
    _requests = None  # type: ignore[assignment]


class NewsFilter:
    """Fetches and caches the economic calendar; exposes is_blackout()."""

    def __init__(self, cfg: NewsConfig, log: BotLogger) -> None:
        self._cfg = cfg
        self._log = log
        self._events: list[NewsEvent] = []
        self._last_fetch: datetime | None = None
        self._cache_stale_hours = 25

    def refresh_cache(self) -> bool:
        if _requests is None:
            self._log.warn("system", "requests library not available — news filter disabled")
            return False
        try:
            resp = _requests.get(self._cfg.calendar_url, timeout=10)
            resp.raise_for_status()
            raw = resp.json()
            self._events = self._parse(raw)
            self._last_fetch = datetime.now(timezone.utc)
            self._log.info("system", f"News cache refreshed: {len(self._events)} events loaded")
            return True
        except Exception as exc:
            self._log.warn("system", f"News calendar fetch failed: {exc}; using stale cache")
            return False

    def is_blackout(self, utc_dt: datetime) -> tuple[bool, str]:
        """Return (is_blocked, event_title) if within blackout window."""
        self._maybe_refresh(utc_dt)
        for event in self._events:
            if event.currency not in self._cfg.currencies:
                continue
            blackout = self._cfg.major_blackout_minutes if event.is_major else self._cfg.blackout_minutes
            delta = abs((utc_dt - event.timestamp_utc).total_seconds() / 60)
            if delta <= blackout:
                return True, event.title
        return False, ""

    def next_event(self, utc_dt: datetime) -> NewsEvent | None:
        future = [e for e in self._events if e.timestamp_utc > utc_dt and e.currency in self._cfg.currencies]
        return min(future, key=lambda e: e.timestamp_utc, default=None)

    def cache_age_hours(self) -> float:
        if self._last_fetch is None:
            return float("inf")
        return (datetime.now(timezone.utc) - self._last_fetch).total_seconds() / 3600

    def is_cache_stale(self) -> bool:
        return self.cache_age_hours() > self._cache_stale_hours

    def _maybe_refresh(self, utc_dt: datetime) -> None:
        if self._last_fetch is None:
            self.refresh_cache()
            return
        today = utc_dt.date()
        last = self._last_fetch.date()
        if today != last:
            self.refresh_cache()

    def _parse(self, raw: list[dict]) -> list[NewsEvent]:
        events: list[NewsEvent] = []
        currencies = set(self._cfg.currencies)
        for item in raw:
            currency = item.get("country", item.get("currency", "")).upper()
            if currency not in currencies:
                continue
            impact = item.get("impact", "").lower()
            if impact not in ("high", "3"):
                continue
            title = item.get("title", item.get("name", ""))
            is_major = any(kw.lower() in title.lower() for kw in self._cfg.major_keywords)
            try:
                ts_str = item.get("date", item.get("datetime", ""))
                if ts_str:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    events.append(NewsEvent(
                        timestamp_utc=ts,
                        currency=currency,
                        title=title,
                        impact="High",
                        is_major=is_major,
                    ))
            except (ValueError, KeyError):
                continue
        return events
