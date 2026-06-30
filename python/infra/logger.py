from __future__ import annotations

import json
import logging
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path


AUDIT = 45
logging.addLevelName(AUDIT, "AUDIT")

CHANNELS = ("signal", "trade", "risk", "learning", "system", "audit")


class _AsyncFileHandler(logging.Handler):
    """Non-blocking handler — writes log records from a background thread."""

    def __init__(self, path: Path, max_queue: int = 1000) -> None:
        super().__init__()
        self._path = path
        self._queue: queue.Queue[logging.LogRecord | None] = queue.Queue(maxsize=max_queue)
        self._drops = 0
        self._thread = threading.Thread(target=self._writer, daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self._drops += 1

    def _writer(self) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            while True:
                record = self._queue.get()
                if record is None:
                    break
                try:
                    fh.write(self.format(record) + "\n")
                    fh.flush()
                except Exception:
                    pass

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=3)
        super().close()


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "data"):
            payload["data"] = record.data
        return json.dumps(payload)


class BotLogger:
    """Central logger. Audit channel never rotates."""

    def __init__(self, log_dir: str = "./logs") -> None:
        self._log_dir = Path(log_dir)
        self._loggers: dict[str, logging.Logger] = {}
        self._setup()

    def _setup(self) -> None:
        formatter = StructuredFormatter()
        for channel in CHANNELS:
            lg = logging.getLogger(f"tradebotflow.{channel}")
            lg.setLevel(logging.DEBUG)
            lg.propagate = False
            path = self._log_dir / channel / f"{channel}.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = _AsyncFileHandler(path)
            handler.setFormatter(formatter)
            lg.addHandler(handler)
            self._loggers[channel] = lg

    def _log(self, channel: str, level: int, msg: str, data: dict | None = None) -> None:
        lg = self._loggers.get(channel, self._loggers["system"])
        record = lg.makeRecord(lg.name, level, "", 0, msg, (), None)
        if data:
            record.data = data
        lg.handle(record)

    def info(self, channel: str, msg: str, data: dict | None = None) -> None:
        self._log(channel, logging.INFO, msg, data)

    def warn(self, channel: str, msg: str, data: dict | None = None) -> None:
        self._log(channel, logging.WARNING, msg, data)

    def error(self, channel: str, msg: str, data: dict | None = None) -> None:
        self._log(channel, logging.ERROR, msg, data)

    def audit(self, msg: str, data: dict | None = None) -> None:
        self._log("audit", AUDIT, msg, data)

    def debug(self, channel: str, msg: str, data: dict | None = None) -> None:
        self._log(channel, logging.DEBUG, msg, data)
