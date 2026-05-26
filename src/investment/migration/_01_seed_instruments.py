"""Migration 01: Seed instruments table from CSV files.

Sources:
  config/holdings.csv     -> STOCK instruments (tranche C)
  config/core_etf.csv     -> ETF instruments (tranche B)
  config/cash_positions.csv -> CASH/BOND/RSU instruments (tranche A/D)
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from investment.core.db import transaction
from investment.core.settings import CONFIG_DIR
from investment.migration.utils import log_failure, log_ok

HOLDINGS_CSV = CONFIG_DIR / "holdings.csv"
ETF_CSV = CONFIG_DIR / "core_etf.csv"
CASH_CSV = CONFIG_DIR / "cash_positions.csv"

# Map cash type codes to asset_class + tranche
CASH_TYPE_MAP = {
    "CASH":  ("CASH",  "A"),
    "ZGYQ":  ("BOND",  "A"),
    "MTRSU": ("RSU",   "D"),
}

# Industry/theme for ETFs
ETF_META = {
    "513010": ("港股科技", "广义科技"),
    "563360": ("A股宽基", "新质生产力"),
    "515180": ("红利/价值", "广义金融"),
    "159915": ("创业板", "广义科技"),
    "159941": ("美股科技", "广义科技"),
    "513500": ("美股宽基", None),
}


def run(db_path=None) -> int:
    """Insert instruments. Returns count of rows inserted."""
    inserted = 0
    failures = []

    with transaction(db_path) as conn:
        # ── C tranche: stocks ──────────────────────────────────────────────
        with open(HOLDINGS_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = row["code"].strip()
                market = row["market"].strip()
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO instruments
                           (code, market, name, asset_class, industry, theme, tranche)
                           VALUES (?,?,?,?,?,?,?)""",
                        (code, market, row["name"].strip(), "STOCK",
                         row.get("industry", "").strip() or None,
                         None,  # theme populated separately if needed
                         "C"),
                    )
                    inserted += conn.execute(
                        "SELECT changes()"
                    ).fetchone()[0]
                except Exception as e:
                    failures.append((HOLDINGS_CSV.name, code, str(e)))

        # ── B tranche: ETFs ────────────────────────────────────────────────
        with open(ETF_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = row["code"].strip()
                industry, theme = ETF_META.get(code, (None, None))
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO instruments
                           (code, market, name, asset_class, industry, theme, tranche)
                           VALUES (?,?,?,?,?,?,?)""",
                        (code, "A", row["name"].strip(), "ETF",
                         industry, theme, "B"),
                    )
                    inserted += conn.execute("SELECT changes()").fetchone()[0]
                except Exception as e:
                    failures.append((ETF_CSV.name, code, str(e)))

        # ── A/D tranche: cash/bond/RSU ─────────────────────────────────────
        with open(CASH_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = row["code"].strip()
                asset_class, tranche = CASH_TYPE_MAP.get(code, ("CASH", "A"))
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO instruments
                           (code, market, name, asset_class, tranche)
                           VALUES (?,?,?,?,?)""",
                        (code, "OTC", row["name"].strip(), asset_class, tranche),
                    )
                    inserted += conn.execute("SELECT changes()").fetchone()[0]
                except Exception as e:
                    failures.append((CASH_CSV.name, code, str(e)))

    for src, code, err in failures:
        log_failure(src, code, err)
    if not failures:
        log_ok("01_seed_instruments", f"{inserted} instruments inserted")
    return inserted


if __name__ == "__main__":
    run()
