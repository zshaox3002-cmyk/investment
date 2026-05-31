"""Migration verification: reconcile DB against source files.

5 checks:
1. Holdings total market value (DB vs CSV × latest quote)
2. C-tranche position ratio (DB vs rules.yaml active_breaches)
3. Alert count (DB vs alerts/*.md file count)
4. Thesis scores (DB vs frontmatter in each thesis file)
5. Three known data conflicts (explicit display, not auto-fix)
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from investment.core.db import connect
from investment.core.settings import (
    ALERTS_DIR, CONFIG_DIR, DATA_DIR, RULES_PATH, THESES_DIR,
)
from investment.migration.utils import parse_frontmatter

REPORT_PATH = DATA_DIR / "migration_diff_report.md"


def _fmt_pct(v: float) -> str:
    return f"{v * 100:.2f}%"


def _fmt_cny(v: float) -> str:
    return f"¥{v:,.0f}"


def run(db_path=None) -> bool:
    conn = connect(db_path)
    lines = ["# Migration Diff Report\n"]
    all_ok = True

    # ── Check 1: Holdings total market value ──────────────────────────────
    lines.append("## Check 1: Holdings Total Market Value\n")
    db_total = conn.execute(
        "SELECT SUM(market_value) AS total FROM v_portfolio_snapshot "
        "WHERE tranche IN ('B','C')"
    ).fetchone()["total"] or 0.0

    # Compute from CSV × portfolio_ts.csv prices
    ts_prices: dict[str, float] = {}
    ts_csv = DATA_DIR / "portfolio_ts.csv"
    if ts_csv.exists():
        with open(ts_csv, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                p = row.get("price", "").strip()
                if p:
                    ts_prices[row["code"].strip()] = float(p)

    csv_total = 0.0
    # C tranche: holdings.csv
    with open(CONFIG_DIR / "holdings.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = row["code"].strip()
            shares = float(row["shares"])
            price = ts_prices.get(code, float(row.get("current_price", 0) or 0))
            csv_total += shares * price
    # B tranche: core_etf.csv
    with open(CONFIG_DIR / "core_etf.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = row["code"].strip()
            shares = float(row.get("shares", 0) or 0)
            price = ts_prices.get(code, float(row.get("cost_price", 0) or 0))
            csv_total += shares * price

    diff_pct = abs(db_total - csv_total) / max(csv_total, 1)
    status = "✅ OK" if diff_pct < 0.001 else "⚠️ DIFF"
    if diff_pct >= 0.001:
        all_ok = False
    lines.append(f"- DB total (B+C): {_fmt_cny(db_total)}")
    lines.append(f"- CSV total: {_fmt_cny(csv_total)}")
    lines.append(f"- Diff: {_fmt_pct(diff_pct)} → {status}\n")

    # ── Check 2: C-tranche position ratio ─────────────────────────────────
    lines.append("## Check 2: C-Tranche Position Ratio\n")
    with open(RULES_PATH, encoding="utf-8") as f:
        rules = yaml.safe_load(f) or {}

    rules_c_ratio = None
    for b in rules.get("active_breaches", []):
        if b.get("rule") == "active_position_total":
            rules_c_ratio = b.get("current_value")
            break

    db_c_mv = conn.execute(
        "SELECT SUM(market_value) AS v FROM v_portfolio_snapshot WHERE tranche='C'"
    ).fetchone()["v"] or 0.0
    db_bc_mv = conn.execute(
        "SELECT SUM(market_value) AS v FROM v_portfolio_snapshot WHERE tranche IN ('B','C')"
    ).fetchone()["v"] or 0.0
    # A-tranche: use cash_balances (no quotes)
    db_a_mv = conn.execute(
        """SELECT SUM(cb.balance) AS v FROM cash_balances cb
           JOIN instruments i ON i.id = cb.instrument_id
           WHERE i.tranche = 'A'
           AND cb.effective_date = (
               SELECT MAX(effective_date) FROM cash_balances cb2
               WHERE cb2.instrument_id = cb.instrument_id
           )"""
    ).fetchone()["v"] or 0.0
    db_total_excl_d = db_bc_mv + db_a_mv
    db_c_ratio = db_c_mv / db_total_excl_d if db_total_excl_d else 0.0

    lines.append(f"- rules.yaml active_breaches[active_position_total]: {_fmt_pct(rules_c_ratio or 0)}")
    lines.append(f"- DB computed (C / excl-D total): {_fmt_pct(db_c_ratio)}")
    lines.append("- Note: rules.yaml uses base ¥1,285,798; DB uses live quotes. Difference is expected.\n")

    # ── Check 3: Alert count ───────────────────────────────────────────────
    lines.append("## Check 3: Alert Count\n")
    file_count = len(list(ALERTS_DIR.glob("*.md")))
    # Count distinct body_paths from migration (each md file = one row)
    db_count = conn.execute(
        "SELECT COUNT(DISTINCT body_path) AS n FROM alerts WHERE body_path IS NOT NULL"
    ).fetchone()["n"]
    status = "✅ OK" if db_count == file_count else "⚠️ DIFF"
    if db_count != file_count:
        all_ok = False
    lines.append(f"- alerts/*.md files: {file_count}")
    lines.append(f"- DB distinct migrated alert paths: {db_count}")
    lines.append(f"- Status: {status}\n")

    # ── Check 4: Thesis scores ─────────────────────────────────────────────
    lines.append("## Check 4: Thesis Scores (frontmatter vs DB)\n")
    lines.append("| File | Frontmatter score | DB current_score | Match |")
    lines.append("|------|-------------------|-----------------|-------|")
    score_ok = True
    for path in sorted(THESES_DIR.glob("*.md")):
        if path.name == "_template.md":
            continue
        fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        fm_score = fm.get("score")
        code = fm.get("code", "").strip()
        row = conn.execute(
            """SELECT t.current_score FROM theses t
               JOIN instruments i ON i.id = t.instrument_id
               WHERE i.code = ?""",
            (code.split(".")[0],),
        ).fetchone()
        db_score = row["current_score"] if row else None
        match = "✅" if (fm_score is None and db_score is None) or (
            fm_score is not None and db_score is not None and abs(float(fm_score) - float(db_score)) < 0.01
        ) else "⚠️"
        if match == "⚠️":
            score_ok = False
            all_ok = False
        lines.append(f"| {path.name} | {fm_score} | {db_score} | {match} |")
    lines.append(f"\nThesis scores: {'✅ all match' if score_ok else '⚠️ mismatches found'}\n")

    # ── Check 5: Three known data conflicts (explicit display) ─────────────
    lines.append("## Check 5: Known Data Conflicts (informational)\n")
    lines.append("These conflicts existed in the source files and are preserved for review.\n")

    # 5a: 600219 current_price
    row_holdings = None
    with open(CONFIG_DIR / "holdings.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["code"].strip() == "600219":
                row_holdings = r
    csv_price = float(row_holdings["current_price"]) if row_holdings else None
    ts_price = ts_prices.get("600219")
    lines.append(f"### 5a: 600219 current_price")
    lines.append(f"- holdings.csv current_price: {csv_price}")
    lines.append(f"- portfolio_ts.csv price: {ts_price}")
    lines.append(f"- DB uses portfolio_ts.csv price (more recent). holdings.csv value is stale.\n")

    # 5b: 513010 target_value
    etf_target_val = None
    with open(CONFIG_DIR / "core_etf.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["code"].strip() == "513010":
                etf_target_val = r.get("target_value")
    lines.append(f"### 5b: 513010 target_value vs b_tranche plan")
    lines.append(f"- core_etf.csv target_value: {etf_target_val} (final target)")
    lines.append(f"- b_tranche_execution_plan.yaml phase1 amount: 80000 (phase 1 only)")
    lines.append(f"- These are different concepts: final target vs phase 1 amount. Not a bug.\n")

    # 5c: C-tranche ratio denominator
    lines.append(f"### 5c: C-tranche ratio denominator mismatch")
    lines.append(f"- rules.yaml uses base ¥1,285,798 (excl. RSU): C ratio = {_fmt_pct(rules_c_ratio or 0)}")
    lines.append(f"- daily reports use C_market_value / C_allocation: ratio = 188.9%")
    lines.append(f"- DB v_portfolio_snapshot uses live quotes / excl-D total: {_fmt_pct(db_c_ratio)}")
    lines.append(f"- Resolution: DB is the canonical source. rules.yaml base is a snapshot.\n")

    # ── Check 7: Phase 4 risk tables exist ────────────────────────────────
    lines.append("## Check 7: Phase 4 Risk Tables\n")
    risk_tables = ["risk_metrics", "correlation_matrix", "risk_contribution"]
    missing_risk = []
    for tbl in risk_tables:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
        ).fetchone()
        if not exists:
            missing_risk.append(tbl)
    if missing_risk:
        all_ok = False
        lines.append(f"- Missing tables: {', '.join(missing_risk)} → ⚠️ Run `inv migrate run`")
    else:
        lines.append(f"- Tables present: {', '.join(risk_tables)} → ✅ OK")
    lines.append("")

    # ── Check 6: Phase 2 onboarding tables exist ──────────────────────────
    lines.append("## Check 6: Phase 2 Onboarding Tables\n")
    required_tables = ["user_profile", "goals", "asset_inventory"]
    missing = []
    for tbl in required_tables:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
        ).fetchone()
        if not exists:
            missing.append(tbl)
    if missing:
        all_ok = False
        lines.append(f"- Missing tables: {', '.join(missing)} → ⚠️ Run `inv migrate run`")
    else:
        lines.append(f"- Tables present: {', '.join(required_tables)} → ✅ OK")
    lines.append("")

    # ── Check 8: Phase 5 attribution tables exist ──────────────────────────
    lines.append("## Check 8: Phase 5 Attribution Tables\n")
    attr_tables = ["performance_attribution", "benchmark_quotes"]
    missing_attr = []
    for tbl in attr_tables:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
        ).fetchone()
        if not exists:
            missing_attr.append(tbl)
    if missing_attr:
        all_ok = False
        lines.append(f"- Missing tables: {', '.join(missing_attr)} → ⚠️ Run `inv migrate run`")
    else:
        lines.append(f"- Tables present: {', '.join(attr_tables)} → ✅ OK")
    lines.append("")

    # ── Check 9: Phase 6 causal_ext columns exist ──────────────────────────
    lines.append("## Check 9: Phase 6 Causal Extension Columns\n")
    causal_ext_cols = ["validation_status", "revision_log", "scope_layer", "credibility_tier"]
    existing_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(chain_assessments)").fetchall()
    }
    missing_cols = [c for c in causal_ext_cols if c not in existing_cols]
    if missing_cols:
        all_ok = False
        lines.append(f"- Missing columns in chain_assessments: {', '.join(missing_cols)} → ⚠️ Run `inv migrate run`")
    else:
        lines.append(f"- Columns present: {', '.join(causal_ext_cols)} → ✅ OK")
    lines.append("")

    # ── Check 10: Phase 7 tables exist ────────────────────────────────────
    lines.append("## Check 10: Phase 7 Tables\n")
    phase7_tables = [
        "task_calendar", "task_log", "custom_strategies",
        "cost_model", "trade_cost_log",
        "behavior_flags", "decision_journal",
    ]
    missing_p7 = []
    for tbl in phase7_tables:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
        ).fetchone()
        if not exists:
            missing_p7.append(tbl)
    if missing_p7:
        all_ok = False
        lines.append(f"- Missing tables: {', '.join(missing_p7)} → ⚠️ Run `inv migrate run`")
    else:
        lines.append(f"- Tables present: {len(phase7_tables)} Phase 7 tables → ✅ OK")
    lines.append("")

    conn.close()

    # Write report
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport written to {REPORT_PATH}")
    return all_ok


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
