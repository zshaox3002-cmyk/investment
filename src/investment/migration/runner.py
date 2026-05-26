"""Migration runner: executes all 8 steps in order."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from investment.core.db import init_db
from investment.migration import (
    m01, m02, m03, m04, m05, m06, m07, m08,
)


def run_all(db_path=None) -> dict[str, int]:
    """Run all migration steps. Returns step -> rows_inserted map."""
    print("Initializing DB schema...")
    init_db(db_path)

    steps = [
        ("01_seed_instruments",       m01.run),
        ("02_load_current_state",     m02.run),
        ("03_load_quotes_history",    m03.run),
        ("04_parse_theses",           m04.run),
        ("05_parse_trades_decisions", m05.run),
        ("06_load_alerts",            m06.run),
        ("07_load_executions",        m07.run),
        ("08_load_breaches",          m08.run),
    ]

    results = {}
    for name, fn in steps:
        print(f"\n── {name} ──")
        try:
            n = fn(db_path)
            results[name] = n
        except Exception as e:
            print(f"  [ERROR] {name}: {e}")
            results[name] = -1

    total = sum(v for v in results.values() if v >= 0)
    print(f"\n✓ Migration complete. Total rows inserted: {total}")
    return results


if __name__ == "__main__":
    run_all()
