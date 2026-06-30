#!/usr/bin/env python3
"""VPS setup helper — creates directory structure and validates environment."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


REQUIRED_DIRS = [
    "logs", "data", "ipc", "ipc/exec_events", "config",
]

REQUIRED_ENV_VARS = [
    "HMAC_SECRET", "DB_PATH", "IPC_PATH",
]


def check_python_version() -> None:
    if sys.version_info < (3, 11):
        sys.exit(f"Python 3.11+ required (got {sys.version})")
    print(f"Python {sys.version.split()[0]} OK")


def create_dirs(base: Path) -> None:
    for d in REQUIRED_DIRS:
        target = base / d
        target.mkdir(parents=True, exist_ok=True)
        print(f"  dir: {target}")


def check_env(base: Path) -> None:
    env_file = base / ".env"
    if not env_file.exists():
        template = base / "config" / ".env.example"
        if template.exists():
            shutil.copy(template, env_file)
            print(f".env created from template — EDIT IT NOW and set HMAC_SECRET")
        else:
            print("WARNING: .env not found and no template available")
        return

    content = env_file.read_text()
    missing = [v for v in REQUIRED_ENV_VARS if v not in content]
    if missing:
        print(f"WARNING: .env missing variables: {missing}")
    else:
        print(".env OK")


def install_requirements(base: Path) -> None:
    req = base / "requirements.txt"
    if not req.exists():
        print("requirements.txt not found — skipping pip install")
        return
    print("Installing Python requirements...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req)], check=True)
    print("Requirements installed")


def verify_ipc_dir(base: Path) -> None:
    ipc = base / "ipc"
    exec_ev = ipc / "exec_events"
    for p in [ipc, exec_ev]:
        if not p.exists():
            p.mkdir(parents=True)
            print(f"Created {p}")
    # Write a test file to verify write permissions
    test = ipc / ".write_test"
    test.write_text("ok")
    test.unlink()
    print("IPC directory write permissions OK")


def main() -> None:
    base = Path(__file__).resolve().parent.parent
    print(f"Setting up TradeBotFlow at {base}\n")

    check_python_version()
    print("\nCreating directories:")
    create_dirs(base)
    print("\nChecking environment:")
    check_env(base)
    print("\nInstalling requirements:")
    install_requirements(base)
    print("\nVerifying IPC directory:")
    verify_ipc_dir(base)

    print("\nSetup complete.")
    print("Next steps:")
    print("  1. Edit .env and set HMAC_SECRET to a long random string")
    print("  2. Place EURUSD_M5.csv, EURUSD_M15.csv, EURUSD_H1.csv in data/")
    print("  3. Run: python -m python.backtest_runner backtest")
    print("  4. Copy mql5/ to MetaTrader 5 data folder and compile TradeBotFlow.mq5")
    print("  5. Run: python -m python.main")


if __name__ == "__main__":
    main()
