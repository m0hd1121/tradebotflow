from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from python.infra.logger import BotLogger
from python.models.events import ExecEvent


class EventReader:
    """Background thread watching ipc/exec_events/ for EA execution events."""

    def __init__(
        self,
        ipc_dir: str,
        on_event: Callable[[ExecEvent], None],
        log: BotLogger,
        poll_interval: float = 1.0,
    ) -> None:
        self._events_dir = Path(ipc_dir) / "exec_events"
        self._on_event = on_event
        self._log = log
        self._poll_interval = poll_interval
        self._seen: set[str] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True, name="EventReader")

    def start(self) -> None:
        self._events_dir.mkdir(parents=True, exist_ok=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _watch(self) -> None:
        while not self._stop.is_set():
            try:
                for path in sorted(self._events_dir.glob("*.json")):
                    if path.name in self._seen:
                        continue
                    self._process(path)
                    self._seen.add(path.name)
            except Exception as exc:
                self._log.error("system", f"EventReader error: {exc}")
            time.sleep(self._poll_interval)

    def _process(self, path: Path) -> None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            event = ExecEvent(
                event_type=raw.get("event_type", "UNKNOWN"),
                trade_id=int(raw.get("trade_id", 0)),
                timestamp_utc=raw.get("timestamp_utc", ""),
                direction=raw.get("direction", ""),
                lots=float(raw.get("lots", 0)),
                price=float(raw.get("price", 0)),
                stop_price=float(raw.get("stop_price", 0)),
                tp1_price=float(raw.get("tp1_price", 0)),
                tp2_price=float(raw.get("tp2_price", 0)),
                outcome_r=float(raw.get("outcome_r", 0)),
                costs_pips=float(raw.get("costs_pips", 0)),
                note=raw.get("note", ""),
            )
            self._on_event(event)
            self._log.info("trade", f"EA event processed: {event.event_type}", {
                "trade_id": event.trade_id, "price": event.price, "r": event.outcome_r,
            })
        except Exception as exc:
            self._log.error("trade", f"Failed to parse exec_event {path.name}: {exc}")
