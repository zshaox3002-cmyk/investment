"""Migration _10: extend task_calendar for v3 agent orchestrator.

SQLite has no ADD COLUMN IF NOT EXISTS, so we check existing columns first.
New columns added:
  source_module     — which orchestrator module generated this task
  source_ref        — unique ref within the module (for dedup)
  action_type       — action category (rebalance / risk / cooldown / etc.)
  decision_layer    — executable | confirm | monitor | blocked | info
  evidence_json     — structured evidence dict (JSON)
  blocking_reason   — why this task is blocked (if decision_layer='blocked')
  suggested_command — inv CLI command to execute this task
  confidence        — 0.0-1.0 confidence in the task's relevance
"""
from __future__ import annotations

from investment.core.db import transaction


# Columns to add: (name, type_and_default)
_NEW_COLUMNS: list[tuple[str, str]] = [
    ("source_module",     "TEXT"),
    ("source_ref",        "TEXT"),
    ("action_type",       "TEXT"),
    ("decision_layer",    "TEXT DEFAULT 'monitor'"),
    ("evidence_json",     "TEXT DEFAULT '{}'"),
    ("blocking_reason",   "TEXT"),
    ("suggested_command", "TEXT"),
    ("confidence",        "REAL DEFAULT 1.0"),
]


def run(db_path=None) -> int:
    """Add v3 columns to task_calendar. Returns number of columns actually added."""
    added = 0
    with transaction(db_path) as conn:
        existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(task_calendar)").fetchall()
        }
        for col_name, col_def in _NEW_COLUMNS:
            if col_name not in existing:
                conn.execute(
                    f"ALTER TABLE task_calendar ADD COLUMN {col_name} {col_def}"
                )
                added += 1

        # Add dedup index (source_module, source_ref, due_date) if not present
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_calendar_source "
            "ON task_calendar(due_date, source_module, source_ref)"
        )

    return added
