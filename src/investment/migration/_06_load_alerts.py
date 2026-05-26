"""Migration 06: Load alerts/*.md into alerts table.

Filename pattern: YYYY-MM-DD_CODE_TYPE.md
Body is kept on disk; body_path stored as reference.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from investment.core.db import transaction
from investment.core.settings import ALERTS_DIR
from investment.migration.utils import instrument_id_by_code, log_failure, log_ok

SEVERITY_MAP = {
    "WARNING": "warning",
    "CRITICAL": "critical",
    "INFO": "info",
    "l1": "info",
    "l2": "warning",
    "l3": "critical",
}

FILENAME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})_([^_]+)_(.+)\.md$"
)


def _severity_from_type(alert_type: str) -> str:
    lower = alert_type.lower()
    if "l3" in lower or "critical" in lower or "drawdown_l3" in lower:
        return "critical"
    if "l2" in lower or "warning" in lower or "drawdown_l2" in lower:
        return "warning"
    return "info"


def _extract_message(text: str) -> str:
    """Get first non-empty non-header line as message."""
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("|") and not line.startswith("-"):
            # Strip markdown bold/italic
            line = re.sub(r"[*_`]", "", line)
            if len(line) > 5:
                return line[:200]
    return "(no message)"


def run(db_path=None) -> int:
    inserted = 0
    failures = []

    alert_files = sorted(ALERTS_DIR.glob("*.md"))

    with transaction(db_path) as conn:
        for path in alert_files:
            m = FILENAME_RE.match(path.name)
            if not m:
                failures.append((path.name, path.name, "filename does not match pattern"))
                continue

            alert_date, code_raw, alert_type = m.group(1), m.group(2), m.group(3)
            severity = _severity_from_type(alert_type)

            # Try to find instrument
            iid = instrument_id_by_code(conn, code_raw)

            text = path.read_text(encoding="utf-8")
            message = _extract_message(text)
            rel_path = str(path.relative_to(path.parents[1]))

            try:
                conn.execute(
                    """INSERT OR IGNORE INTO alerts
                       (alert_date, alert_type, severity, instrument_id,
                        message, body_path)
                       VALUES (?,?,?,?,?,?)""",
                    (alert_date, alert_type, severity, iid, message, rel_path),
                )
                inserted += conn.execute("SELECT changes()").fetchone()[0]
            except Exception as e:
                failures.append((path.name, code_raw, str(e)))

    for src, key, err in failures:
        log_failure(src, key, err)
    log_ok("06_load_alerts", f"{inserted} alerts inserted, {len(failures)} failures")
    return inserted


if __name__ == "__main__":
    run()
