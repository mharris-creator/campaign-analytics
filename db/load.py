"""SQLite connection + idempotent upsert helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import config


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(config.SCHEMA_PATH.read_text())
    conn.commit()


def upsert_many(conn: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    """Insert-or-replace rows. Each row is a dict of column->value.

    All rows must share the same keys. Returns the number of rows written.
    """
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(cols))
    collist = ", ".join(cols)
    sql = f"INSERT OR REPLACE INTO {table} ({collist}) VALUES ({placeholders})"
    conn.executemany(sql, [[r.get(c) for c in cols] for r in rows])
    conn.commit()
    return len(rows)


def insert_stage_events(conn: sqlite3.Connection, events: list[dict]) -> int:
    """Insert stage events keeping the EARLIEST entry per (contact, cycle, stage).

    contact_stage_events has PRIMARY KEY (contact_id, cycle, stage). Cycle numbers
    are stable across re-syncs because they are derived from append-only history,
    so keeping the smaller entered_at never overwrites a real first-touch.
    """
    if not events:
        return 0
    sql = (
        "INSERT INTO contact_stage_events (contact_id, cycle, cycle_type, stage, entered_at, source) "
        "VALUES (:contact_id, :cycle, :cycle_type, :stage, :entered_at, :source) "
        "ON CONFLICT(contact_id, cycle, stage) DO UPDATE SET "
        "  entered_at = MIN(entered_at, excluded.entered_at), "
        "  cycle_type = excluded.cycle_type, "
        "  source = excluded.source "
        "WHERE excluded.entered_at < entered_at"
    )
    conn.executemany(sql, events)
    conn.commit()
    return len(events)
