"""SQLite connection layer.

Single-process app, so we lean on `sqlite3` directly with WAL + foreign keys
enabled. `connect()` returns a connection with `row_factory=Row`. Use
`init_db()` once per environment to create tables and bump schema_version.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from .settings import DB_PATH, SCHEMA_PATH, SCHEMA_VERSION
from .exceptions import DBError


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    return conn


@contextmanager
def transaction(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if not row:
        return 0
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] or 0


def init_db(db_path: Optional[Path] = None, force: bool = False) -> int:
    """Create tables and views. Idempotent.

    Returns the schema_version after init.
    """
    if not SCHEMA_PATH.exists():
        raise DBError(f"schema.sql missing at {SCHEMA_PATH}")
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    with transaction(db_path) as conn:
        current = _current_schema_version(conn)
        if current >= SCHEMA_VERSION and not force:
            return current
        conn.executescript(schema_sql)
        conn.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at, description) "
            "VALUES (?, ?, ?)",
            (SCHEMA_VERSION, datetime.utcnow().isoformat(timespec="seconds") + "Z",
             "Initial schema (18 tables + 3 views)"),
        )
    return SCHEMA_VERSION


def list_tables(db_path: Optional[Path] = None) -> list[str]:
    with transaction(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
        ).fetchall()
    return [r["name"] for r in rows]
