"""Migration 03: Load portfolio time-series into quotes table.

Source: data/portfolio_ts.csv
Columns: date, code, type, name, shares, price, market_value, pnl_pct
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from investment.core.db import transaction
from investment.core.settings import DATA_DIR
from investment.migration.utils import instrument_id_by_code, log_failure, log_ok

TS_CSV = DATA_DIR / "portfolio_ts.csv"

# Map tranche letter from csv 'type' column to market hint
TRANCHE_MARKET = {"B": "A", "C": "A", "A": "OTC", "D": "OTC"}
# HK stocks have codes starting with 0 and 5 digits
HK_PATTERN = {"02015"}


def _market_for(code: str, tranche: str) -> str | None:
    if code in HK_PATTERN:
        return "HK"
    return TRANCHE_MARKET.get(tranche)


def run(db_path=None) -> int:
    if not TS_CSV.exists():
        log_ok("03_load_quotes_history", "portfolio_ts.csv not found, skipping")
        return 0

    inserted = 0
    failures = []
    fetched_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    with transaction(db_path) as conn:
        with open(TS_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = row["code"].strip()
                tranche = row.get("type", "").strip()
                market = _market_for(code, tranche)
                iid = instrument_id_by_code(conn, code, market)
                if iid is None:
                    failures.append((TS_CSV.name, code, "instrument not found"))
                    continue
                price_str = row.get("price", "").strip()
                if not price_str:
                    continue  # no price, skip (cash rows)
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO quotes
                           (instrument_id, quote_date, close, fetched_at, source)
                           VALUES (?,?,?,?,?)""",
                        (iid, row["date"].strip(), float(price_str),
                         fetched_at, "portfolio_ts_csv"),
                    )
                    inserted += conn.execute("SELECT changes()").fetchone()[0]
                except Exception as e:
                    failures.append((TS_CSV.name, code, str(e)))

    for src, code, err in failures:
        log_failure(src, code, err)
    if not failures:
        log_ok("03_load_quotes_history", f"{inserted} quote rows inserted")
    return inserted


if __name__ == "__main__":
    run()
