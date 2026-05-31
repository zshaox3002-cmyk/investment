"""SQL migration runner.

Scans ``migrations/`` for ``.sql`` files, executes them in lexical order,
and tracks applied files via the ``sql_schema_migrations`` table (SHA256 checksum).

Usage::

    from investment.core.sql_migrator import run_sql_migrations
    run_sql_migrations()         # apply pending migrations
    run_sql_migrations(force=True)  # re-apply all
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .db import connect, transaction
from .settings import MIGRATIONS_DIR


_BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS sql_schema_migrations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT NOT NULL UNIQUE,
    checksum    TEXT NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status      TEXT NOT NULL DEFAULT 'applied'
                CHECK(status IN ('applied','failed','rolled_back'))
);
"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bootstrap(conn) -> None:
    conn.executescript(_BOOTSTRAP_SQL)


def _applied_files(conn) -> dict[str, str]:
    rows = conn.execute(
        "SELECT filename, checksum FROM sql_schema_migrations WHERE status='applied'"
    ).fetchall()
    return {r["filename"]: r["checksum"] for r in rows}


def run_sql_migrations(db_path: Path | None = None, force: bool = False) -> dict[str, str]:
    """Execute pending SQL migrations. Returns {filename: status}.

    Status values: ``applied``, ``skipped``, ``force_reapplied``, ``failed``.
    """
    if not MIGRATIONS_DIR.exists():
        return {}

    sql_files = sorted(
        [p for p in MIGRATIONS_DIR.glob("*.sql") if p.name[0].isdigit()]
    )
    if not sql_files:
        return {}

    results: dict[str, str] = {}

    with transaction(db_path) as conn:
        _bootstrap(conn)
        applied = _applied_files(conn)

        for fpath in sql_files:
            fname = fpath.name
            checksum = _sha256(fpath)

            if not force and fname in applied and applied[fname] == checksum:
                results[fname] = "skipped"
                continue

            try:
                sql = fpath.read_text(encoding="utf-8")
                conn.executescript(sql)

                # Check DB (not in-memory dict) for existing tracking record
                was_applied = fname in applied
                conn.execute(
                    "INSERT OR REPLACE INTO sql_schema_migrations (filename, checksum, status, applied_at) "
                    "VALUES (?, ?, 'applied', CURRENT_TIMESTAMP)",
                    (fname, checksum),
                )
                results[fname] = "force_reapplied" if (force and was_applied) else "applied"
            except Exception as exc:
                conn.execute(
                    "INSERT OR REPLACE INTO sql_schema_migrations (filename, checksum, status) "
                    "VALUES (?, ?, 'failed')",
                    (fname, checksum),
                )
                results[fname] = f"failed: {exc}"
                raise

    return results
