"""Migration 08: Load rules.yaml active_breaches into rule_breaches table."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from investment.core.db import transaction
from investment.core.settings import RULES_PATH
from investment.migration.utils import instrument_id_by_code, log_failure, log_ok

NOW = datetime.utcnow().isoformat(timespec="seconds") + "Z"

STATUS_MAP = {
    "整改中": "remediating",
    "观察中": "active",
    "已解决": "resolved",
    "升级": "escalated",
}


def run(db_path=None) -> int:
    inserted = 0
    failures = []

    with open(RULES_PATH, encoding="utf-8") as f:
        rules = yaml.safe_load(f) or {}

    breaches = rules.get("active_breaches", [])
    if not breaches:
        log_ok("08_load_breaches", "no active_breaches found")
        return 0

    with transaction(db_path) as conn:
        for breach in breaches:
            if not isinstance(breach, dict):
                continue
            rule_path = breach.get("rule", "unknown")
            # Instrument: may be a single stock or None (portfolio-level)
            stock_code = breach.get("stock", "").strip()
            iid = None
            if stock_code:
                iid = instrument_id_by_code(conn, stock_code)

            status_raw = breach.get("status", "active")
            status = STATUS_MAP.get(status_raw, "active")

            notes_parts = []
            if breach.get("notes"):
                notes_parts.append(breach["notes"].strip())
            if breach.get("remediation_type"):
                notes_parts.append("整改类型: " + "; ".join(breach["remediation_type"]))
            if breach.get("action_plan"):
                notes_parts.append("行动计划: " + breach["action_plan"])
            notes = "\n".join(notes_parts) or None

            try:
                conn.execute(
                    """INSERT OR IGNORE INTO rule_breaches
                       (rule_path, instrument_id, current_value, threshold,
                        breach_amount, detected_at, grace_period_expires,
                        status, notes)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (rule_path, iid,
                     float(breach.get("current_value", 0)),
                     float(breach.get("threshold", 0)),
                     float(breach.get("breach_amount", 0)),
                     breach.get("detected_at", NOW[:10]),
                     breach.get("grace_period_expires"),
                     status, notes),
                )
                inserted += conn.execute("SELECT changes()").fetchone()[0]
            except Exception as e:
                failures.append(("rules.yaml", rule_path, str(e)))

    for src, key, err in failures:
        log_failure(src, key, err)
    log_ok("08_load_breaches", f"{inserted} rule_breaches inserted")
    return inserted


if __name__ == "__main__":
    run()
