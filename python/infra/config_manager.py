from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from python.models.config import (
    DataConfig, ExitConfig, IPCConfig, IndicatorConfig,
    LearningConfig, MMConfig, NewsConfig, SessionConfig,
    StrategyConfig, SystemConfig,
)


class ConfigError(Exception):
    pass


class ConfigManager:
    """Sole writer of versioned, HMAC-signed configuration.

    Reads system.json on init, validates schema, exposes SystemConfig.
    When Learning Engine proposes a parameter update, this class signs
    and writes the new config to disk + to the IPC directory for the EA.
    """

    def __init__(
        self,
        config_path: str = "./config/system.json",
        ipc_dir: str = "./ipc",
        hmac_key: bytes | None = None,
    ) -> None:
        self._config_path = Path(config_path)
        self._ipc_dir = Path(ipc_dir)
        self._hmac_key = hmac_key or os.environ.get("HMAC_SECRET", "").encode()
        self._config: SystemConfig = self._load()

    def get(self) -> SystemConfig:
        return self._config

    def update(self, new_config: SystemConfig) -> None:
        new_config.version = self._config.version + 1
        self._write_to_disk(new_config, self._config_path)
        self._config = new_config
        self.write_ea_config()

    def write_ea_config(self) -> None:
        self._ipc_dir.mkdir(parents=True, exist_ok=True)
        self._write_to_disk(self._config, self._ipc_dir / "config.json")

    def rollback_to_version(self, version: int) -> None:
        history_path = self._config_path.parent / f"system_v{version}.json"
        if not history_path.exists():
            raise ConfigError(f"No history found for version {version}")
        self._config = self._load_from(history_path)
        self._write_to_disk(self._config, self._config_path)
        self.write_ea_config()

    def sign(self, payload: str) -> str:
        return hmac.new(self._hmac_key, payload.encode(), hashlib.sha256).hexdigest()

    def verify(self, payload: str, signature: str) -> bool:
        expected = self.sign(payload)
        return hmac.compare_digest(expected, signature)

    def _load(self) -> SystemConfig:
        if not self._config_path.exists():
            default = SystemConfig()
            self._write_to_disk(default, self._config_path)
            return default
        return self._load_from(self._config_path)

    def _load_from(self, path: Path) -> SystemConfig:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        sig = raw.pop("hmac_sig", "")
        if self._hmac_key and sig:
            payload = json.dumps(raw, sort_keys=True)
            if not self.verify(payload, sig):
                raise ConfigError(f"HMAC verification failed for {path}")
        return self._dict_to_config(raw)

    def _write_to_disk(self, config: SystemConfig, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        backup_version = config.version - 1
        backup = path.parent / f"system_v{backup_version}.json"
        if path.exists() and not backup.exists() and backup_version > 0:
            path.rename(backup)
        raw = self._config_to_dict(config)
        if self._hmac_key:
            payload = json.dumps(raw, sort_keys=True)
            raw["hmac_sig"] = self.sign(payload)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(raw, fh, indent=2)

    @staticmethod
    def _config_to_dict(config: SystemConfig) -> dict:
        return dataclasses.asdict(config)

    @staticmethod
    def _dict_to_config(raw: dict) -> SystemConfig:
        def _sub(cls, key: str) -> object:
            sub = raw.get(key, {})
            valid = {f.name for f in dataclasses.fields(cls)}
            return cls(**{k: v for k, v in sub.items() if k in valid})

        return SystemConfig(
            version=raw.get("version", 1),
            initial_equity=raw.get("initial_equity", 10000.0),
            pip_value_per_lot=raw.get("pip_value_per_lot", 10.0),
            circuit_breaker_locked=raw.get("circuit_breaker_locked", False),
            strategy=_sub(StrategyConfig, "strategy"),
            sessions=_sub(SessionConfig, "sessions"),
            risk=_sub(MMConfig, "risk"),
            indicators=_sub(IndicatorConfig, "indicators"),
            exit=_sub(ExitConfig, "exit"),
            news=_sub(NewsConfig, "news"),
            ipc=_sub(IPCConfig, "ipc"),
            learning=_sub(LearningConfig, "learning"),
            data=_sub(DataConfig, "data"),
        )
