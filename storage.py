"""SQLite-backed state: signal log, deduplication and live performance stats."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from models import Signal, TradeResult

log = logging.getLogger("ctb.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id             INTEGER PRIMARY KEY,
    symbol         TEXT NOT NULL,
    asset_class    TEXT NOT NULL,
    interval       TEXT NOT NULL,
    direction      TEXT NOT NULL,
    confidence     REAL NOT NULL,
    score          REAL NOT NULL,
    price          REAL NOT NULL,
    entry          REAL NOT NULL,
    stop_loss      REAL NOT NULL,
    take_profit    REAL NOT NULL,
    risk_reward    REAL NOT NULL,
    regime         TEXT NOT NULL,
    candle_time    INTEGER NOT NULL,
    generated_at   REAL NOT NULL,
    reasons        TEXT NOT NULL,
    UNIQUE(symbol, interval, candle_time, direction)
);
CREATE INDEX IF NOT EXISTS idx_signals_time
    ON signals(symbol, interval, candle_time);

CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY,
    symbol        TEXT NOT NULL,
    interval      TEXT NOT NULL,
    direction     TEXT NOT NULL,
    entry_time    INTEGER NOT NULL,
    exit_time     INTEGER NOT NULL,
    entry         REAL NOT NULL,
    exit_price    REAL NOT NULL,
    stop_loss     REAL NOT NULL,
    take_profit   REAL NOT NULL,
    outcome       TEXT NOT NULL,
    r_multiple    REAL NOT NULL,
    pnl           REAL NOT NULL,
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(symbol, interval, entry_time);
"""


class StateStore:
    """Thread-safe wrapper around a single SQLite database."""

    def __init__(self, path: str | Path = "ctb_state.db") -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Signals
    # ------------------------------------------------------------------ #
    def is_duplicate(self, signal: Signal, ttl_minutes: int = 0) -> bool:
        """True when the same symbol/interval/candle/direction already fired."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, generated_at FROM signals "
                "WHERE symbol=? AND interval=? AND candle_time=? AND direction=? "
                "LIMIT 1",
                (
                    signal.symbol,
                    signal.interval,
                    signal.candle_time,
                    signal.direction.value,
                ),
            ).fetchone()
        if row is None:
            return False
        if ttl_minutes <= 0:
            return True
        return (time.time() - row["generated_at"]) < ttl_minutes * 60

    def record_signal(self, signal: Signal) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO signals ("
                "symbol, asset_class, interval, direction, confidence, score, "
                "price, entry, stop_loss, take_profit, risk_reward, regime, "
                "candle_time, generated_at, reasons"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    signal.symbol,
                    signal.asset_class,
                    signal.interval,
                    signal.direction.value,
                    signal.confidence,
                    signal.score,
                    signal.price,
                    signal.entry,
                    signal.stop_loss,
                    signal.take_profit,
                    signal.risk_reward,
                    signal.regime,
                    signal.candle_time,
                    signal.generated_at,
                    json.dumps(signal.reasons),
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid or 0)

    def recent_signals(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM signals ORDER BY generated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ #
    # Trades
    # ------------------------------------------------------------------ #
    def record_trade(self, trade: TradeResult) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO trades ("
                "symbol, interval, direction, entry_time, exit_time, entry, "
                "exit_price, stop_loss, take_profit, outcome, r_multiple, pnl, "
                "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    trade.symbol,
                    trade.interval,
                    trade.direction.value,
                    trade.entry_time,
                    trade.exit_time,
                    trade.entry,
                    trade.exit,
                    trade.stop_loss,
                    trade.take_profit,
                    trade.outcome,
                    trade.r_multiple,
                    trade.pnl,
                    time.time(),
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid or 0)

    def performance(self, symbol: str | None = None) -> dict:
        query = (
            "SELECT COUNT(*) AS trades, "
            "SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins, "
            "SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) AS losses, "
            "AVG(r_multiple) AS avg_r, SUM(pnl) AS pnl "
            "FROM trades"
        )
        params: tuple = ()
        if symbol:
            query += " WHERE symbol=?"
            params = (symbol,)
        with self._lock:
            row = self._conn.execute(query, params).fetchone()
        trades = row["trades"] or 0
        wins = row["wins"] or 0
        return {
            "trades": trades,
            "wins": wins,
            "losses": row["losses"] or 0,
            "win_rate": round(100.0 * wins / trades, 1) if trades else 0.0,
            "avg_r": round(row["avg_r"] or 0.0, 3),
            "pnl": round(row["pnl"] or 0.0, 2),
        }
