"""Migration 05: Parse trades/decision_*.md and trades/log_*.md.

Decisions -> decisions table
Stop rules from decision_002 -> stop_rules table
Trade logs -> trades table
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from investment.core.db import transaction
from investment.core.settings import TRADES_DIR
from investment.migration.utils import (
    instrument_id_by_code,
    log_failure,
    log_ok,
)

# decision_002 stop rules (manually extracted from §5 and §7)
# These are the 4+3+1 rules identified in the plan
DECISION_002_STOP_RULES = [
    # §5.2 Absolute price GRID_SELL (4 batches)
    dict(rule_type="GRID_SELL", trigger_kind="PRICE_ABS",
         trigger_value=5.81, action="sell_6150_shares", shares=6150, priority=10),
    dict(rule_type="GRID_SELL", trigger_kind="PRICE_ABS",
         trigger_value=6.34, action="sell_6150_shares", shares=6150, priority=20),
    dict(rule_type="GRID_SELL", trigger_kind="PRICE_ABS",
         trigger_value=6.86, action="sell_6150_shares", shares=6150, priority=30),
    dict(rule_type="GRID_SELL", trigger_kind="PRICE_ABS",
         trigger_value=7.39, action="sell_6150_shares", shares=6150, priority=40),
    # §7 GRID_BUY averaging-down ladder (3 levels)
    dict(rule_type="GRID_BUY", trigger_kind="PRICE_ABS",
         trigger_value=4.80, action="buy_averaging_down_l1", shares=None, priority=50),
    dict(rule_type="GRID_BUY", trigger_kind="PRICE_ABS",
         trigger_value=4.40, action="buy_averaging_down_l2", shares=None, priority=60),
    dict(rule_type="GRID_BUY", trigger_kind="PRICE_ABS",
         trigger_value=4.00, action="buy_averaging_down_l3", shares=None, priority=70),
    # §3 Q3 -35% hard drawdown trigger
    dict(rule_type="HARD_DD", trigger_kind="PNL_PCT",
         trigger_value=-0.35, action="exit_all_review", shares=None, priority=1),
]


def _parse_decision_header(text: str, filename_stem: str) -> dict:
    """Extract decision metadata from markdown header block."""
    meta = {}
    for label, key in [
        (r"决策编号[：:]?\s*\*{0,2}(decision_\S+?)\*{0,2}\s*$", "decision_no"),
        (r"决策日期[：:]?\s*\*{0,2}(\d{4}-\d{2}-\d{2})\*{0,2}", "decision_date"),
        (r"决策类型[：:]?\s*\*{0,2}(.+?)\*{0,2}\s*$", "decision_type_raw"),
    ]:
        m = re.search(label, text, re.MULTILINE)
        if m:
            meta[key] = m.group(1).strip()
    # Fallback: use filename as decision_no
    if not meta.get("decision_no"):
        meta["decision_no"] = filename_stem
    return meta


def _map_decision_type(raw: str) -> str:
    raw = raw.lower()
    if "新建" in raw or "new" in raw:
        return "NEW"
    if "加仓" in raw or "add" in raw:
        return "ADD"
    if "减仓" in raw or "减持" in raw or "reduce" in raw:
        return "REDUCE"
    if "清仓" in raw or "exit" in raw:
        return "EXIT"
    if "再平衡" in raw or "rebalance" in raw:
        return "REBALANCE"
    return "REDUCE"  # default for decision_002


def _parse_trade_log(text: str, decision_id: int | None, conn) -> list[dict]:
    """Extract individual trade rows from a log file."""
    trades = []
    # Match table rows like: | 1 | 10:03:54 | ¥5.34 | 2,100 | ¥11,214 |
    pattern = re.compile(
        r"\|\s*\d+\s*\|\s*[\d:]+\s*\|\s*[¥￥]?([\d,.]+)\s*\|\s*([\d,]+)\s*\|\s*[¥￥]?([\d,.]+)\s*\|"
    )
    date_m = re.search(r"执行日期[：:]\s*(\d{4}-\d{2}-\d{2})", text)
    trade_date = date_m.group(1) if date_m else datetime.utcnow().strftime("%Y-%m-%d")

    # Determine instrument from context
    code_m = re.search(r"（(\d{5,6}|0\d{4})）", text)
    code = code_m.group(1) if code_m else None

    for m in pattern.finditer(text):
        price_str = m.group(1).replace(",", "")
        shares_str = m.group(2).replace(",", "")
        amount_str = m.group(3).replace(",", "")
        try:
            price = float(price_str)
            shares = float(shares_str)
            amount = float(amount_str)
        except ValueError:
            continue
        if shares <= 0 or price <= 0:
            continue
        trades.append(dict(
            code=code,
            trade_date=trade_date,
            side="SELL",  # log_002_001 is a sell log; default SELL
            shares=shares,
            price=price,
            amount=amount,
            decision_id=decision_id,
        ))
    return trades


def run(db_path=None) -> int:
    inserted = 0
    failures = []
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    decision_files = sorted(TRADES_DIR.glob("decision_*.md"))
    log_files = sorted(TRADES_DIR.glob("log_*.md"))

    with transaction(db_path) as conn:
        decision_id_map: dict[str, int] = {}

        # ── Parse decisions ────────────────────────────────────────────────
        for path in decision_files:
            text = path.read_text(encoding="utf-8")
            meta = _parse_decision_header(text, path.stem)
            decision_no = meta.get("decision_no") or path.stem
            decision_date = meta.get("decision_date") or now[:10]
            dtype = _map_decision_type(meta.get("decision_type_raw", ""))

            # Primary instrument: look for code in header
            code_m = re.search(r"（(\d{5,6}|0\d{4})\.(?:SH|SZ|HK)\)", text)
            if not code_m:
                code_m = re.search(r"(\d{5,6}|0\d{4})\.(?:SH|SZ|HK)", text)
            primary_iid = None
            if code_m:
                primary_iid = instrument_id_by_code(conn, code_m.group(1))

            rel_path = str(path.relative_to(path.parents[2]))
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO decisions
                       (decision_no, decision_date, decision_type,
                        primary_instrument_id, body_path, status)
                       VALUES (?,?,?,?,?,?)""",
                    (decision_no, decision_date, dtype,
                     primary_iid, rel_path, "active"),
                )
                ch = conn.execute("SELECT changes()").fetchone()[0]
                inserted += ch
                if ch:
                    did = conn.execute(
                        "SELECT id FROM decisions WHERE decision_no=?", (decision_no,)
                    ).fetchone()["id"]
                    decision_id_map[decision_no] = did
            except Exception as e:
                failures.append((path.name, decision_no, str(e)))

        # ── Stop rules for decision_002 ────────────────────────────────────
        d002_id = decision_id_map.get("decision_002")
        if d002_id is None:
            # Try to fetch existing
            row = conn.execute(
                "SELECT id FROM decisions WHERE decision_no='decision_002'"
            ).fetchone()
            if row:
                d002_id = row["id"]

        if d002_id:
            iid_600219 = instrument_id_by_code(conn, "600219")
            if iid_600219:
                for rule in DECISION_002_STOP_RULES:
                    try:
                        existing = conn.execute(
                            """SELECT id FROM stop_rules
                               WHERE instrument_id=? AND rule_type=?
                                 AND trigger_kind=? AND trigger_value=?""",
                            (iid_600219, rule["rule_type"],
                             rule["trigger_kind"], rule["trigger_value"]),
                        ).fetchone()
                        if existing:
                            continue
                        conn.execute(
                            """INSERT INTO stop_rules
                               (decision_id, instrument_id, rule_type, trigger_kind,
                                trigger_value, action, shares, priority, status)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (d002_id, iid_600219,
                             rule["rule_type"], rule["trigger_kind"],
                             rule["trigger_value"], rule["action"],
                             rule.get("shares"), rule["priority"], "armed"),
                        )
                        inserted += conn.execute("SELECT changes()").fetchone()[0]
                    except Exception as e:
                        failures.append(("stop_rules", rule["action"], str(e)))

        # ── Parse trade logs ───────────────────────────────────────────────
        for path in log_files:
            text = path.read_text(encoding="utf-8")
            # Infer decision_id from filename (log_002_001 -> decision_002)
            m = re.match(r"log_(\d+)_", path.stem)
            dec_no = f"decision_{m.group(1)}" if m else None
            did = decision_id_map.get(dec_no) if dec_no else None
            if did is None and dec_no:
                row = conn.execute(
                    "SELECT id FROM decisions WHERE decision_no=?", (dec_no,)
                ).fetchone()
                if row:
                    did = row["id"]

            trade_rows = _parse_trade_log(text, did, conn)
            rel_path = str(path.relative_to(path.parents[2]))
            for t in trade_rows:
                code = t.get("code")
                iid = instrument_id_by_code(conn, code) if code else None
                if iid is None:
                    failures.append((path.name, str(code), "instrument not found"))
                    continue
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO trades
                           (instrument_id, trade_date, side, shares, price,
                            amount, decision_id, source_doc, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (iid, t["trade_date"], t["side"],
                         t["shares"], t["price"], t["amount"],
                         t["decision_id"], rel_path, now),
                    )
                    inserted += conn.execute("SELECT changes()").fetchone()[0]
                except Exception as e:
                    failures.append((path.name, str(code), str(e)))

    for src, key, err in failures:
        log_failure(src, key, err)
    log_ok("05_parse_trades_decisions",
           f"{inserted} rows inserted, {len(failures)} failures")
    return inserted


if __name__ == "__main__":
    run()
