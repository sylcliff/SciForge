#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Thin sqlite3 helpers for the literature skill.

No ORM — just context managers and convenience wrappers.
All DB calls go through this module.
"""

import sqlite3
import time
from pathlib import Path

DB: sqlite3.Connection | None = None
WAL_CHECKPOINT_INTERVAL = 60  # seconds between WAL checkpoints


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open (or reopen) the literature DB with WAL mode."""
    global DB
    db = sqlite3.connect(str(db_path), timeout=10)
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA foreign_keys = ON")
    db.row_factory = sqlite3.Row
    DB = db
    return db


def get() -> sqlite3.Connection:
    """Return the current connection, or raise if not connected."""
    if DB is None:
        raise RuntimeError("DB not connected — call connect() first")
    return DB


def close():
    """Checkpoint WAL and close."""
    global DB
    if DB is not None:
        try:
            DB.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass
        DB.close()
        DB = None


class Atomic:
    """Context manager for a transaction (BEGIN IMMEDIATE → commit/rollback)."""

    def __init__(self, db: sqlite3.Connection | None = None):
        self.db = db or get()

    def __enter__(self):
        self.db.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.db.commit()
        else:
            self.db.rollback()


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    """Execute a single SQL statement and return the cursor."""
    return get().execute(sql, params)


def fetchone(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    """Fetch one row, or None."""
    return get().execute(sql, params).fetchone()


def fetchall(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Fetch all rows."""
    return get().execute(sql, params).fetchall()


def insert(table: str, data: dict) -> int | None:
    """Insert a row and return rowid (or None on conflict / no-op)."""
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
    try:
        cur = get().execute(sql, tuple(data.values()))
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def upsert(table: str, data: dict, conflict_cols: list[str]) -> int | None:
    """Insert ON CONFLICT DO UPDATE, returning rowid."""
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    updates = ", ".join(f"{k} = excluded.{k}" for k in data if k not in conflict_cols)
    sql = (
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT({', '.join(conflict_cols)}) DO UPDATE SET {updates}"
    )
    cur = get().execute(sql, tuple(data.values()))
    return cur.lastrowid


def ensure_schema_version(db: sqlite3.Connection, expected: int) -> tuple[bool, int]:
    """Check schema version. Returns (match, current_version)."""
    try:
        row = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        current = int(row["value"]) if row else 0
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        current = 0
    return current == expected, current