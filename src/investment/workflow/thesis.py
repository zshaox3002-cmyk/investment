"""Thesis management workflow.

inv thesis sync   — sync frontmatter from theses/*.md into DB
inv thesis list   — list all theses with scores
inv thesis score  — record a dimension score
inv thesis stale  — list theses needing update
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from investment.core.db import connect, transaction
from investment.core.settings import THESES_DIR
from investment.migration.utils import instrument_id_by_code, parse_frontmatter


def sync(db_path=None) -> int:
    """Sync frontmatter from all theses/*.md into DB. Returns rows upserted."""
    upserted = 0
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    thesis_files = [p for p in THESES_DIR.glob("*.md") if p.name != "_template.md"]

    with transaction(db_path) as conn:
        for path in thesis_files:
            text = path.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            if not fm or not fm.get("code"):
                continue
            raw_code = fm["code"].strip()
            iid = instrument_id_by_code(conn, raw_code)
            if iid is None:
                print(f"  [skip] {path.name}: instrument not found for {raw_code}")
                continue

            score = fm.get("score")
            try:
                score = float(score) if score is not None else None
            except (TypeError, ValueError):
                score = None

            updated = str(fm.get("updated", "")).strip() or now[:10]
            rel_path = str(path.relative_to(path.parents[2]))

            conn.execute(
                """INSERT OR REPLACE INTO theses
                   (instrument_id, version, current_score, rating, action,
                    alert_context, body_path, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (iid, fm.get("version", "v1.0"), score,
                 fm.get("rating", "").strip() or None,
                 fm.get("action", "").strip() or None,
                 fm.get("alert_context", "").strip() or None,
                 rel_path, updated),
            )
            upserted += conn.execute("SELECT changes()").fetchone()[0]

            # Upsert composite score into thesis_scores
            if score is not None:
                conn.execute(
                    """INSERT OR REPLACE INTO thesis_scores
                       (instrument_id, snapshot_date, dimension, score, source)
                       VALUES (?,?,?,?,?)""",
                    (iid, updated, "综合", score, "thesis_md"),
                )

    return upserted


def list_theses(db_path=None) -> list[dict]:
    """Return all theses with current scores."""
    conn = connect(db_path)
    rows = conn.execute(
        """SELECT i.code, i.name, i.tranche,
                  t.current_score, t.rating, t.action, t.updated_at,
                  t.next_review_date
           FROM theses t JOIN instruments i ON i.id=t.instrument_id
           ORDER BY t.current_score DESC NULLS LAST"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def record_score(
    code: str,
    dimension: str,
    score: float,
    rationale: str = "",
    snapshot_date: Optional[str] = None,
    db_path=None,
) -> bool:
    """Record a dimension score for a thesis. Returns True if inserted."""
    today = snapshot_date or date.today().isoformat()
    with transaction(db_path) as conn:
        iid = instrument_id_by_code(conn, code)
        if iid is None:
            return False
        conn.execute(
            """INSERT OR REPLACE INTO thesis_scores
               (instrument_id, snapshot_date, dimension, score, rationale, source)
               VALUES (?,?,?,?,?,?)""",
            (iid, today, dimension, score, rationale or None, "manual"),
        )
        # Update composite score if dimension is "综合"
        if dimension == "综合":
            conn.execute(
                "UPDATE theses SET current_score=?, updated_at=? WHERE instrument_id=?",
                (score, today, iid),
            )
    return True


def stale_theses(days_threshold: int = 90, db_path=None) -> list[dict]:
    """Return theses not updated within days_threshold days."""
    cutoff = (date.today() - timedelta(days=days_threshold)).isoformat()
    conn = connect(db_path)
    rows = conn.execute(
        """SELECT i.code, i.name, t.current_score, t.rating, t.updated_at,
                  julianday('now') - julianday(t.updated_at) AS days_since
           FROM theses t JOIN instruments i ON i.id=t.instrument_id
           WHERE t.updated_at < ?
           ORDER BY t.updated_at ASC""",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
