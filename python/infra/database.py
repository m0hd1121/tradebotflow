from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from python.models.trade import TradeOutcome, TradeRecord, TradeStatus


_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA page_size=4096;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS trades (
    trade_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    day_of_week     TEXT,
    session         TEXT,
    direction       TEXT,
    bias_aligned    INTEGER,
    setup_grade     TEXT,
    sweep_level     TEXT,
    entry_time      TEXT,
    entry_price     REAL,
    stop_price      REAL,
    tp1_price       REAL,
    tp2_price       REAL,
    risk_pct        REAL,
    stop_pips       REAL,
    planned_rr      REAL,
    lots            REAL,
    status          TEXT DEFAULT 'OPEN',
    tp1_hit         INTEGER DEFAULT 0,
    runner_exit_price REAL,
    exit_time       TEXT,
    outcome         TEXT DEFAULT 'OPEN',
    modelled_r      REAL DEFAULT 0,
    r_used          REAL DEFAULT 0,
    pl_pct          REAL DEFAULT 0,
    cum_r           REAL DEFAULT 0,
    costs_pips      REAL DEFAULT 0,
    rule_violation  INTEGER DEFAULT 0,
    screenshot_path TEXT DEFAULT '',
    note            TEXT DEFAULT '',
    is_backtest     INTEGER DEFAULT 0,
    backtest_id     TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_time     TEXT NOT NULL,
    direction       TEXT,
    grade           TEXT,
    entry_price     REAL,
    stop_price      REAL,
    planned_rr      REAL,
    sweep_level     TEXT,
    session         TEXT,
    passed          INTEGER,
    rejection_reason TEXT,
    trade_id        INTEGER
);

CREATE TABLE IF NOT EXISTS config_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    version         INTEGER NOT NULL,
    timestamp       TEXT NOT NULL,
    config_json     TEXT NOT NULL,
    changed_by      TEXT DEFAULT 'system'
);

CREATE TABLE IF NOT EXISTS pending_params (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    proposed_at     TEXT NOT NULL,
    proposal_type   TEXT NOT NULL,
    description     TEXT,
    before_expectancy REAL,
    after_expectancy REAL,
    sample_size     INTEGER,
    details_json    TEXT,
    status          TEXT DEFAULT 'PENDING'
);

CREATE TABLE IF NOT EXISTS active_params (
    param_key       TEXT PRIMARY KEY,
    param_value     TEXT NOT NULL,
    applied_at      TEXT NOT NULL,
    config_version  INTEGER
);

CREATE TABLE IF NOT EXISTS analytics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    computed_at     TEXT NOT NULL,
    snapshot_json   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mc_analysis (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at          TEXT NOT NULL,
    n_simulations   INTEGER,
    results_json    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS levels (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    level_type      TEXT NOT NULL,
    price           REAL NOT NULL,
    formed_at       TEXT NOT NULL,
    is_zone         INTEGER DEFAULT 0,
    zone_upper      REAL,
    zone_lower      REAL,
    zone_type       TEXT,
    zone_direction  TEXT,
    zone_sweep_level TEXT,
    is_mitigated    INTEGER DEFAULT 0
);
"""


class TradeJournalDAO:
    """SQLite data-access layer. Thread-safe via per-thread connections."""

    def __init__(self, db_path: str = "./tradebotflow.db") -> None:
        self._db_path = Path(db_path)
        self._local = threading.local()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self._db_path), detect_types=sqlite3.PARSE_DECLTYPES
            )
            self._local.conn.row_factory = sqlite3.Row
        yield self._local.conn

    def insert_trade(self, trade: TradeRecord) -> int:
        sql = """INSERT INTO trades
            (date, day_of_week, session, direction, bias_aligned, setup_grade,
             sweep_level, entry_time, entry_price, stop_price, tp1_price, tp2_price,
             risk_pct, stop_pips, planned_rr, lots, is_backtest, backtest_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        with self._conn() as conn:
            cur = conn.execute(sql, (
                trade.date.isoformat(), trade.day_of_week, trade.session,
                trade.direction, int(trade.bias_aligned), trade.setup_grade,
                trade.sweep_level, trade.entry_time.isoformat(),
                trade.entry_price, trade.stop_price, trade.tp1_price, trade.tp2_price,
                trade.risk_pct, trade.stop_pips, trade.planned_rr, trade.lots,
                int(trade.is_backtest), trade.backtest_id,
            ))
            conn.commit()
            return cur.lastrowid

    def update_trade_close(self, trade: TradeRecord) -> None:
        sql = """UPDATE trades SET
            status=?, tp1_hit=?, runner_exit_price=?, exit_time=?, outcome=?,
            modelled_r=?, r_used=?, pl_pct=?, cum_r=?, costs_pips=?,
            rule_violation=?, note=?
            WHERE trade_id=?"""
        with self._conn() as conn:
            conn.execute(sql, (
                trade.status.value, int(trade.tp1_hit), trade.runner_exit_price,
                trade.exit_time.isoformat() if trade.exit_time else None,
                trade.outcome.value, trade.modelled_r, trade.r_used,
                trade.pl_pct, trade.cum_r, trade.costs_pips,
                int(trade.rule_violation), trade.note, trade.trade_id,
            ))
            conn.commit()

    def log_signal(
        self, signal_time: datetime, direction: str, grade: str,
        entry: float, stop: float, rr: float, sweep_level: str,
        session: str, passed: bool, rejection_reason: str = "",
        trade_id: int | None = None,
    ) -> None:
        sql = """INSERT INTO signals
            (signal_time, direction, grade, entry_price, stop_price,
             planned_rr, sweep_level, session, passed, rejection_reason, trade_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
        with self._conn() as conn:
            conn.execute(sql, (
                signal_time.isoformat(), direction, grade, entry, stop,
                rr, sweep_level, session, int(passed), rejection_reason, trade_id,
            ))
            conn.commit()

    def save_config_version(self, version: int, config_json: str, changed_by: str = "system") -> None:
        sql = "INSERT INTO config_history (version, timestamp, config_json, changed_by) VALUES (?,?,?,?)"
        with self._conn() as conn:
            conn.execute(sql, (version, datetime.now(timezone.utc).isoformat(), config_json, changed_by))
            conn.commit()

    def save_analytics_snapshot(self, snapshot: dict) -> None:
        sql = "INSERT INTO analytics (computed_at, snapshot_json) VALUES (?,?)"
        with self._conn() as conn:
            conn.execute(sql, (datetime.now(timezone.utc).isoformat(), json.dumps(snapshot)))
            conn.commit()

    def get_closed_trades(self, is_backtest: bool = False, backtest_id: str | None = None) -> list[dict]:
        with self._conn() as conn:
            if backtest_id:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE backtest_id=? AND status='CLOSED'",
                    (backtest_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE is_backtest=? AND status='CLOSED'",
                    (int(is_backtest),)
                ).fetchall()
        return [dict(r) for r in rows]

    def get_open_trade(self) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE status IN ('OPEN','TP1_HIT') AND is_backtest=0"
            ).fetchone()
        return dict(row) if row else None

    def get_daily_pnl(self, date_str: str) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(pl_pct),0) FROM trades WHERE date LIKE ? AND is_backtest=0",
                (f"{date_str}%",)
            ).fetchone()
        return row[0] if row else 0.0

    def get_trades_count_today(self, date_str: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE date LIKE ? AND is_backtest=0",
                (f"{date_str}%",)
            ).fetchone()
        return row[0] if row else 0

    def get_recent_outcomes(self, n: int, is_backtest: bool = False) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT outcome FROM trades WHERE is_backtest=? AND status='CLOSED' ORDER BY trade_id DESC LIMIT ?",
                (int(is_backtest), n)
            ).fetchall()
        return [r[0] for r in rows]

    def get_param(self, key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT param_value FROM active_params WHERE param_key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_param(self, key: str, value: str, config_version: int = 0) -> None:
        sql = """INSERT INTO active_params (param_key, param_value, applied_at, config_version)
                 VALUES (?,?,?,?) ON CONFLICT(param_key) DO UPDATE SET
                 param_value=excluded.param_value, applied_at=excluded.applied_at,
                 config_version=excluded.config_version"""
        with self._conn() as conn:
            conn.execute(sql, (key, value, datetime.now(timezone.utc).isoformat(), config_version))
            conn.commit()
