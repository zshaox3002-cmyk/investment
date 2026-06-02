"""Prioritizer — dedup + layer grouping + sort for task_calendar.

Dedup key: (due_date, source_module, source_ref) — same-day same-origin tasks
are never duplicated. Returns True from exists_task() when found.

Layer ordering:
  0 executable — has suggested_command, no blocking_reason
  1 confirm    — needs user confirmation
  2 monitor    — observe, no action required
  3 blocked    — blocking_reason is set
  4 info       — pure information

Within a layer: priority high > medium > low, then due_date asc.
"""
from __future__ import annotations

from typing import Optional

from investment.core.db import connect


LAYER_ORDER = ["executable", "confirm", "monitor", "blocked", "info"]
_LAYER_RANK = {layer: i for i, layer in enumerate(LAYER_ORDER)}
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def task_exists(
    source_module: str,
    source_ref: str,
    due_date: str,
    db_path=None,
) -> bool:
    """Return True if a pending/overdue task with this dedup key already exists."""
    conn = connect(db_path)
    row = conn.execute(
        """SELECT id FROM task_calendar
           WHERE source_module=? AND source_ref=? AND due_date=?
             AND status NOT IN ('done','skipped')
           LIMIT 1""",
        (source_module, source_ref, due_date),
    ).fetchone()
    conn.close()
    return row is not None


def prioritize_tasks(
    task_ids: list[int],
    db_path=None,
) -> dict[str, list[dict]]:
    """Load the given task IDs from task_calendar, group and sort by layer.

    Returns {layer: [task_dict, ...]} where layer ∈ LAYER_ORDER.
    Unknown layers are bucketed into 'monitor'.
    """
    if not task_ids:
        return {layer: [] for layer in LAYER_ORDER}

    placeholders = ",".join("?" * len(task_ids))
    conn = connect(db_path)
    rows = conn.execute(
        f"""SELECT id, title, category, due_date, priority, status,
                   related_code, source_module, source_ref, action_type,
                   decision_layer, evidence_json, blocking_reason,
                   suggested_command, confidence, notes
            FROM task_calendar
            WHERE id IN ({placeholders})""",
        task_ids,
    ).fetchall()
    conn.close()

    grouped: dict[str, list[dict]] = {layer: [] for layer in LAYER_ORDER}
    for r in rows:
        task = dict(r)
        layer = r["decision_layer"] or "monitor"
        if layer not in grouped:
            layer = "monitor"
        grouped[layer].append(task)

    for layer in LAYER_ORDER:
        grouped[layer].sort(
            key=lambda t: (
                _PRIORITY_RANK.get(t.get("priority") or "medium", 1),
                t.get("due_date") or "",
            )
        )

    return grouped


def prioritize_all_pending(db_path=None) -> dict[str, list[dict]]:
    """Load ALL pending/overdue tasks (not just a specific ID set) and group."""
    from datetime import date
    today = date.today().isoformat()
    conn = connect(db_path)
    rows = conn.execute(
        """SELECT id FROM task_calendar
           WHERE status NOT IN ('done','skipped')
             AND due_date <= date(?, '+7 days')""",
        (today,),
    ).fetchall()
    conn.close()
    ids = [r["id"] for r in rows]
    return prioritize_tasks(ids, db_path=db_path)
