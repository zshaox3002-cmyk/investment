"""Migration 02: Load current holdings and cash balances.

Sources:
  config/holdings.csv     -> holdings table (effective_date = file mtime or today)
  config/cash_positions.csv -> cash_balances table
"""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from investment.core.db import transaction
from investment.core.settings import CONFIG_DIR
from investment.migration.utils import instrument_id_by_code, log_failure, log_ok

HOLDINGS_CSV = CONFIG_DIR / "holdings.csv"
CASH_CSV = CONFIG_DIR / "cash_positions.csv"

MARKET_MAP = {"A": "A", "HK": "HK", "US": "US"}


def _effective_date(path: Path) -> str:
    """Use file mtime as effective_date, fallback to today."""
    try:
        ts = path.stat().st_mtime
        from datetime import datetime
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return date.today().isoformat()


def run(db_path=None) -> int:
    inserted = 0
    failures = []
    eff_date = _effective_date(HOLDINGS_CSV)

    with transaction(db_path) as conn:
        # ── Stock holdings ─────────────────────────────────────────────────
        with open(HOLDINGS_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = row["code"].strip()
                market = row["market"].strip()
                iid = instrument_id_by_code(conn, code, market)
                if iid is None:
                    failures.append((HOLDINGS_CSV.name, code, "instrument not found"))
                    continue
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO holdings
                           (instrument_id, effective_date, shares, cost_price,
                            added_date, reason, source)
                           VALUES (?,?,?,?,?,?,?)""",
                        (iid, eff_date,
                         float(row["shares"]),
                         float(row["cost_price"]),
                         row.get("added_date", "").strip() or None,
                         row.get("reason", "").strip() or None,
                         "migration"),
                    )
                    inserted += conn.execute("SELECT changes()").fetchone()[0]
                except Exception as e:
                    failures.append((HOLDINGS_CSV.name, code, str(e)))

        # ── Cash / bond / RSU balances ─────────────────────────────────────
        eff_date_cash = _effective_date(CASH_CSV)
        with open(CASH_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = row["code"].strip()
                iid = instrument_id_by_code(conn, code)
                if iid is None:
                    failures.append((CASH_CSV.name, code, "instrument not found"))
                    continue
                # annual_rate: strip % and convert
                rate_str = row.get("annual_rate", "0").strip().rstrip("%")
                try:
                    annual_rate = float(rate_str) / 100 if rate_str else None
                except ValueError:
                    annual_rate = None
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO cash_balances
                           (instrument_id, effective_date, balance, annual_rate,
                            status, notes)
                           VALUES (?,?,?,?,?,?)""",
                        (iid, eff_date_cash,
                         float(row["balance"]),
                         annual_rate,
                         row.get("status", "").strip() or None,
                         row.get("notes", "").strip() or None),
                    )
                    inserted += conn.execute("SELECT changes()").fetchone()[0]
                except Exception as e:
                    failures.append((CASH_CSV.name, code, str(e)))

    for src, code, err in failures:
        log_failure(src, code, err)
    if not failures:
        log_ok("02_load_current_state", f"{inserted} rows inserted")
    return inserted


if __name__ == "__main__":
    run()
