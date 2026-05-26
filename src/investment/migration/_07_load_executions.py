"""Migration 07: Load execution plans into executions table.

Sources:
  config/execution_tracker.yaml  -> c_tranche_restructure + b_tranche_build sections
  config/b_tranche_execution_plan.yaml -> detailed B-tranche phases
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from investment.core.db import transaction
from investment.core.settings import CONFIG_DIR
from investment.migration.utils import instrument_id_by_code, log_failure, log_ok

TRACKER_YAML = CONFIG_DIR / "execution_tracker.yaml"
B_PLAN_YAML = CONFIG_DIR / "b_tranche_execution_plan.yaml"

NOW = datetime.utcnow().isoformat(timespec="seconds") + "Z"

STATUS_MAP = {
    "done": "done",
    "in_progress": "in_progress",
    "pending": "pending",
    "skipped": "skipped",
    "blocked": "blocked",
}

SIDE_MAP = {
    "卖出": "SELL", "减仓": "SELL", "清仓": "SELL", "sell": "SELL",
    "买入": "BUY", "建仓": "BUY", "补仓": "BUY", "buy": "BUY",
}


def _infer_side(action: str) -> str:
    for kw, side in SIDE_MAP.items():
        if kw in action:
            return side
    return "BUY"


def _parse_tracker_items(items: list, plan_name: str, conn) -> list[dict]:
    rows = []
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        code = str(item.get("stock", "")).strip()
        iid = instrument_id_by_code(conn, code) if code else None
        if iid is None:
            # Try to extract from action text
            m = re.search(r"(\d{5,6}|0\d{4})", item.get("action", ""))
            if m:
                iid = instrument_id_by_code(conn, m.group(1))
        if iid is None:
            continue  # skip non-instrument tasks (compliance, thesis)

        action = item.get("action", "")
        rows.append(dict(
            plan_name=plan_name,
            instrument_id=iid,
            phase=item.get("id", ""),
            batch=1,
            side=_infer_side(action),
            planned_date=str(item.get("planned_date", "")).strip() or None,
            planned_end=str(item.get("planned_end", "")).strip() or None,
            planned_shares=None,
            planned_amount=float(item["amount"]) if item.get("amount") else None,
            trigger_type=None,
            trigger_spec=None,
            status=STATUS_MAP.get(str(item.get("status", "pending")), "pending"),
            result_summary=str(item.get("result", "")).strip() or None,
            log_ref=str(item.get("log_ref", "")).strip() or None,
            notes=str(item.get("notes", "")).strip() or None,
            source_doc="config/execution_tracker.yaml",
        ))
    return rows


def _parse_b_plan(data: dict, conn) -> list[dict]:
    """Parse b_tranche_execution_plan.yaml phases into execution rows."""
    rows = []

    # nasdaq_exit tranches
    nasdaq_exit = data.get("nasdaq_exit", {})
    etf_code = str(nasdaq_exit.get("etf_code", "159941"))
    iid = instrument_id_by_code(conn, etf_code)
    for t in nasdaq_exit.get("tranches", []):
        if not isinstance(t, dict):
            continue
        rows.append(dict(
            plan_name="b_tranche_v2",
            instrument_id=iid,
            phase="nasdaq_exit",
            batch=t.get("batch", 1),
            side="SELL",
            planned_date=None,
            planned_end=None,
            planned_shares=None,
            planned_amount=float(t["approx_amount"]) if t.get("approx_amount") else None,
            trigger_type="time",
            trigger_spec=json.dumps({"week": t.get("week"), "condition": t.get("condition")}),
            status="pending",
            result_summary=None,
            log_ref=None,
            notes=str(t.get("condition", "")),
            source_doc="config/b_tranche_execution_plan.yaml",
        ))

    # phase1_immediate, phase2_dca, phase3_conditional
    for phase_key in ("phase1_immediate", "phase2_dca", "phase3_conditional"):
        phase_data = data.get(phase_key)
        if not phase_data:
            continue
        for etf_code_key, etf_info in phase_data.items() if isinstance(phase_data, dict) else []:
            if not isinstance(etf_info, dict):
                continue
            code = str(etf_info.get("etf_code", etf_code_key)).strip()
            iid = instrument_id_by_code(conn, code)
            if iid is None:
                continue
            for batch in etf_info.get("batches", []):
                if not isinstance(batch, dict):
                    continue
                rows.append(dict(
                    plan_name="b_tranche_v2",
                    instrument_id=iid,
                    phase=phase_key,
                    batch=batch.get("batch", 1),
                    side="BUY",
                    planned_date=str(batch.get("date", "")).strip() or None,
                    planned_end=None,
                    planned_shares=None,
                    planned_amount=float(batch["amount"]) if batch.get("amount") else None,
                    trigger_type=batch.get("trigger_type"),
                    trigger_spec=json.dumps(batch.get("trigger")) if batch.get("trigger") else None,
                    status="pending",
                    result_summary=None,
                    log_ref=None,
                    notes=str(batch.get("notes", "")).strip() or None,
                    source_doc="config/b_tranche_execution_plan.yaml",
                ))
    return rows


def run(db_path=None) -> int:
    inserted = 0
    failures = []

    with open(TRACKER_YAML, encoding="utf-8") as f:
        tracker = yaml.safe_load(f) or {}

    b_plan = {}
    if B_PLAN_YAML.exists():
        with open(B_PLAN_YAML, encoding="utf-8") as f:
            b_plan = yaml.safe_load(f) or {}

    with transaction(db_path) as conn:
        # From execution_tracker.yaml
        for section, plan_name in [
            ("c_tranche_restructure", "c_restructure"),
            ("b_tranche_build", "b_tranche_v2"),
        ]:
            items = tracker.get(section, [])
            if not isinstance(items, list):
                continue
            rows = _parse_tracker_items(items, plan_name, conn)
            for row in rows:
                if row["instrument_id"] is None:
                    continue
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO executions
                           (plan_name, instrument_id, phase, batch, side,
                            planned_date, planned_end, planned_shares, planned_amount,
                            trigger_type, trigger_spec, status, result_summary,
                            log_ref, notes, source_doc, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (row["plan_name"], row["instrument_id"], row["phase"],
                         row["batch"], row["side"], row["planned_date"],
                         row["planned_end"], row["planned_shares"], row["planned_amount"],
                         row["trigger_type"], row["trigger_spec"], row["status"],
                         row["result_summary"], row["log_ref"], row["notes"],
                         row["source_doc"], NOW, NOW),
                    )
                    inserted += conn.execute("SELECT changes()").fetchone()[0]
                except Exception as e:
                    failures.append(("execution_tracker", row["phase"], str(e)))

        # From b_tranche_execution_plan.yaml
        b_rows = _parse_b_plan(b_plan, conn)
        for row in b_rows:
            if row["instrument_id"] is None:
                continue
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO executions
                       (plan_name, instrument_id, phase, batch, side,
                        planned_date, planned_end, planned_shares, planned_amount,
                        trigger_type, trigger_spec, status, result_summary,
                        log_ref, notes, source_doc, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (row["plan_name"], row["instrument_id"], row["phase"],
                     row["batch"], row["side"], row["planned_date"],
                     row["planned_end"], row["planned_shares"], row["planned_amount"],
                     row["trigger_type"], row["trigger_spec"], row["status"],
                     row["result_summary"], row["log_ref"], row["notes"],
                     row["source_doc"], NOW, NOW),
                )
                inserted += conn.execute("SELECT changes()").fetchone()[0]
            except Exception as e:
                failures.append(("b_tranche_plan", row["phase"], str(e)))

    for src, key, err in failures:
        log_failure(src, key, err)
    log_ok("07_load_executions", f"{inserted} rows inserted, {len(failures)} failures")
    return inserted


if __name__ == "__main__":
    run()
