"""Migration utilities: logging helpers and shared parsers."""
from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

FAILURES_CSV = Path(__file__).resolve().parents[3] / "data" / "migration_failures.csv"
_failures_written = False


def log_ok(step: str, msg: str) -> None:
    print(f"  [ok] {step}: {msg}")


def log_failure(source: str, key: str, error: str) -> None:
    global _failures_written
    FAILURES_CSV.parent.mkdir(parents=True, exist_ok=True)
    mode = "a"
    write_header = not FAILURES_CSV.exists() or not _failures_written
    with open(FAILURES_CSV, mode, newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["timestamp", "source", "key", "error"])
        w.writerow([datetime.utcnow().isoformat(timespec="seconds"), source, key, error])
    _failures_written = True
    print(f"  [FAIL] {source} / {key}: {error}")


def parse_frontmatter(text: str) -> dict:
    """Extract YAML-like frontmatter between --- delimiters."""
    import yaml
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except Exception:
        return {}


def instrument_id_by_code(conn, code: str, market: str | None = None) -> int | None:
    """Look up instrument id by code (and optionally market)."""
    # Strip exchange suffix like .SH / .SZ / .HK
    bare = re.sub(r"\.(SH|SZ|HK|US)$", "", code.strip(), flags=re.IGNORECASE)
    if market:
        row = conn.execute(
            "SELECT id FROM instruments WHERE code=? AND market=?", (bare, market)
        ).fetchone()
        if row:
            return row["id"]
    # Fallback: match by code only
    row = conn.execute(
        "SELECT id FROM instruments WHERE code=? LIMIT 1", (bare,)
    ).fetchone()
    return row["id"] if row else None
