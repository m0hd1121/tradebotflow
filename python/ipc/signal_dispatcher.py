from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from python.infra.config_manager import ConfigManager
from python.infra.logger import BotLogger
from python.models.risk import SizingResult
from python.models.signal import Signal


class SignalDispatcher:
    """Writes HMAC-signed signal.json to the IPC directory for the EA to consume."""

    def __init__(self, config_manager: ConfigManager, log: BotLogger) -> None:
        self._cm = config_manager
        self._log = log
        self._ipc_path = Path(config_manager.get().ipc.ipc_dir)

    def dispatch(self, signal: Signal, sizing: SizingResult) -> bool:
        payload = {
            "ts": signal.signal_time_utc.isoformat(),
            "direction": signal.direction,
            "grade": signal.grade.value,
            "bias": signal.bias.value,
            "session": signal.session,
            "entry_price": round(signal.entry_price, 5),
            "stop_price": round(signal.stop_price, 5),
            "tp1_price": round(signal.tp1_price, 5),
            "tp2_price": round(signal.tp2_price, 5),
            "stop_pips": round(signal.stop_pips, 1),
            "planned_rr": round(signal.planned_rr, 2),
            "sweep_extreme": round(signal.sweep_extreme, 5),
            "sweep_level": signal.sweep_level_name,
            "lots": round(sizing.lots, 2),
            "risk_pct": round(sizing.risk_pct_used, 4),
            "zone_upper": round(signal.entry_zone.upper, 5),
            "zone_lower": round(signal.entry_zone.lower, 5),
            "zone_type": signal.entry_zone.zone_type.value,
        }
        payload_str = json.dumps(payload, sort_keys=True)
        payload["hmac_sig"] = self._cm.sign(payload_str)

        try:
            self._ipc_path.mkdir(parents=True, exist_ok=True)
            signal_file = self._ipc_path / "signal.json"
            signal_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self._log.info("signal", "Signal dispatched to EA", {
                "direction": signal.direction, "grade": signal.grade.value,
                "entry": signal.entry_price, "lots": sizing.lots, "rr": signal.planned_rr,
            })
            return True
        except OSError as exc:
            self._log.error("signal", f"Failed to write signal.json: {exc}")
            return False

    def clear(self) -> None:
        signal_file = self._ipc_path / "signal.json"
        try:
            if signal_file.exists():
                signal_file.write_text("{}", encoding="utf-8")
        except OSError:
            pass
