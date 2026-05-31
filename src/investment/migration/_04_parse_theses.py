"""Migration 04: Parse theses/*.md frontmatter into theses + thesis_scores tables.

Each thesis file has YAML frontmatter with: code, name, version, updated,
score, rating, action, alert_context.

The markdown body is kept on disk; body_path is stored as reference.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from investment.core.db import transaction
from investment.core.settings import THESES_DIR
from investment.migration.utils import (
    instrument_id_by_code,
    log_failure,
    log_ok,
    parse_frontmatter,
)


def run(db_path=None) -> int:
    inserted = 0
    failures = []
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    thesis_files = [
        p for p in THESES_DIR.glob("*.md") if p.name != "_template.md"
    ]

    with transaction(db_path) as conn:
        for path in thesis_files:
            text = path.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            if not fm:
                failures.append((path.name, path.name, "no frontmatter"))
                continue

            raw_code = fm.get("code", "").strip()
            if not raw_code:
                failures.append((path.name, path.name, "missing code in frontmatter"))
                continue

            iid = instrument_id_by_code(conn, raw_code)
            if iid is None:
                failures.append((path.name, raw_code, "instrument not found"))
                continue

            updated = fm.get("updated", "").strip() or now[:10]
            score = fm.get("score")
            try:
                score = float(score) if score is not None else None
            except (TypeError, ValueError):
                score = None

            # Relative path from project root
            rel_path = str(path.relative_to(path.parents[2]))

            try:
                conn.execute(
                    """INSERT OR REPLACE INTO theses
                       (instrument_id, version, current_score, rating, action,
                        alert_context, body_path, updated_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (iid,
                     fm.get("version", "v1.0"),
                     score,
                     fm.get("rating", "").strip() or None,
                     fm.get("action", "").strip() or None,
                     fm.get("alert_context", "").strip() or None,
                     rel_path,
                     updated),
                )
                inserted += conn.execute("SELECT changes()").fetchone()[0]
            except Exception as e:
                failures.append((path.name, raw_code, str(e)))
                continue

            # Insert composite score as a thesis_scores row
            if score is not None:
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO thesis_scores
                           (instrument_id, snapshot_date, dimension, score, source)
                           VALUES (?,?,?,?,?)""",
                        (iid, updated, "综合", score, "thesis_md"),
                    )
                    inserted += conn.execute("SELECT changes()").fetchone()[0]
                except Exception as e:
                    failures.append((path.name, raw_code + "/score", str(e)))

    for src, key, err in failures:
        log_failure(src, key, err)
    if not failures:
        log_ok("04_parse_theses", f"{inserted} rows inserted")
    return inserted


if __name__ == "__main__":
    run()
