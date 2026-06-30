from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from python.infra.config_manager import ConfigManager
from python.infra.database import TradeJournalDAO
from python.infra.logger import BotLogger


class SafetyMonitor:
    """30-second background watchdog. Checks EA heartbeat, limits, news cache age."""

    def __init__(
        self,
        config_manager: ConfigManager,
        dao: TradeJournalDAO,
        log: BotLogger,
        news_filter=None,
        on_circuit_breaker: object | None = None,
        interval_s: int = 30,
    ) -> None:
        self._cm = config_manager
        self._dao = dao
        self._log = log
        self._nf = news_filter
        self._on_circuit_breaker = on_circuit_breaker
        self._interval = interval_s
        self._ipc_dir = Path(config_manager.get().ipc.ipc_dir)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="SafetyMonitor")

    def start(self) -> None:
        self._thread.start()
        self._log.info("system", "SafetyMonitor started")

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def write_kill_flag(self, reason: str = "") -> None:
        path = self._ipc_dir / "kill_flag.txt"
        try:
            path.write_text(reason, encoding="utf-8")
            self._log.audit(f"KILL_FLAG written: {reason}")
        except OSError as exc:
            self._log.error("system", f"Failed to write kill_flag: {exc}")

    def clear_kill_flag(self) -> None:
        path = self._ipc_dir / "kill_flag.txt"
        if path.exists():
            path.unlink()
            self._log.info("system", "Kill flag cleared")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._check_heartbeat()
                self._check_news_cache()
            except Exception as exc:
                self._log.error("system", f"SafetyMonitor cycle error: {exc}")
            time.sleep(self._interval)

    def _check_heartbeat(self) -> None:
        cfg = self._cm.get()
        hb_path = self._ipc_dir / "heartbeat.txt"
        if not hb_path.exists():
            return
        try:
            mtime = hb_path.stat().st_mtime
            age = time.time() - mtime
            max_age = cfg.ipc.heartbeat_max_age_seconds
            if age > max_age:
                self._log.warn("system", f"EA heartbeat stale: {age:.0f}s > {max_age}s")
                self._alert(f"EA heartbeat stale ({age:.0f}s)")
        except OSError:
            pass

    def _check_news_cache(self) -> None:
        if self._nf is None:
            return
        if self._nf.is_cache_stale():
            self._log.warn("system", f"News cache stale: {self._nf.cache_age_hours():.1f}h")
            self._alert("News calendar cache is stale (> 25 hours). Attempting refresh.")
            self._nf.refresh_cache()

    def _alert(self, message: str) -> None:
        self._log.warn("system", f"ALERT: {message}")
        # Extension point: add Telegram/email here
