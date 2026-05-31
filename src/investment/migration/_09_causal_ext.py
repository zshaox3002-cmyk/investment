"""Phase 6: Extend chain_assessments with validation/scope/credibility columns.

Idempotent: checks pragma table_info before each ALTER TABLE.
"""
from __future__ import annotations

from investment.core.db import transaction


_NEW_COLUMNS = [
    (
        "validation_status",
        "TEXT NOT NULL DEFAULT 'open' CHECK(validation_status IN ('open','confirmed','refuted'))",
    ),
    (
        "revision_log",
        "TEXT NOT NULL DEFAULT '[]'",
    ),
    (
        "scope_layer",
        "TEXT CHECK(scope_layer IS NULL OR scope_layer IN ('L1_macro','L2_sector','L3_holding'))",
    ),
    (
        "credibility_tier",
        "TEXT NOT NULL DEFAULT 'C' CHECK(credibility_tier IN ('A','B','C','D'))",
    ),
]


def run(db_path=None) -> int:
    """Add Phase 6 columns to chain_assessments. Returns number of columns added."""
    added = 0
    with transaction(db_path) as conn:
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(chain_assessments)").fetchall()
        }
        for col_name, col_def in _NEW_COLUMNS:
            if col_name not in existing:
                conn.execute(
                    f"ALTER TABLE chain_assessments ADD COLUMN {col_name} {col_def}"
                )
                added += 1
                print(f"  [ok] Added column chain_assessments.{col_name}")
            else:
                print(f"  [skip] chain_assessments.{col_name} already exists")

        # Create index (idempotent)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ca_credibility "
            "ON chain_assessments(date DESC, credibility_tier, validation_status)"
        )
    return added
