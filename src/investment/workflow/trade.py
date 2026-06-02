"""Trade workflow: decisions, trade logs, stop rules, holdings apply.

Covers:
  inv trade decision new/list/show
  inv trade log
  inv trade stop add/list
  inv trade apply
  inv exec monitor
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from investment.core.db import connect, transaction
from investment.core.settings import TRADES_DIR
from investment.migration.utils import instrument_id_by_code


# ── Decisions ─────────────────────────────────────────────────────────────

def new_decision(
    code: str,
    decision_type: str,
    notes: str = "",
    ic_memo_passed: bool = False,
    db_path=None,
) -> dict:
    """Create a new decision record and a stub markdown file."""
    today = date.today().isoformat()
    with transaction(db_path) as conn:
        iid = instrument_id_by_code(conn, code)
        # Auto-number: max existing + 1
        row = conn.execute(
            "SELECT MAX(CAST(SUBSTR(decision_no, 10) AS INTEGER)) AS n FROM decisions"
        ).fetchone()
        next_n = (row["n"] or 0) + 1
        decision_no = f"decision_{next_n:03d}"

        # Create stub markdown
        md_path = TRADES_DIR / f"{decision_no}.md"
        md_path.write_text(
            f"# 决策 {decision_no}\n\n"
            f"> **决策编号：** {decision_no}\n"
            f"> **决策日期：** {today}\n"
            f"> **决策类型：** {decision_type}\n\n"
            f"## 备注\n{notes}\n",
            encoding="utf-8",
        )
        rel_path = str(md_path.relative_to(md_path.parents[2]))

        conn.execute(
            """INSERT INTO decisions
               (decision_no, decision_date, decision_type, primary_instrument_id,
                body_path, ic_memo_passed, status)
               VALUES (?,?,?,?,?,?,?)""",
            (decision_no, today, decision_type.upper(), iid,
             rel_path, 1 if ic_memo_passed else 0, "active"),
        )
        did = conn.execute(
            "SELECT id FROM decisions WHERE decision_no=?", (decision_no,)
        ).fetchone()["id"]

    return {"id": did, "decision_no": decision_no, "body_path": str(md_path)}


def list_decisions(status: str = "active", db_path=None) -> list[dict]:
    conn = connect(db_path)
    rows = conn.execute(
        """SELECT d.id, d.decision_no, d.decision_date, d.decision_type,
                  d.status, d.ic_memo_passed, i.code, i.name
           FROM decisions d
           LEFT JOIN instruments i ON i.id=d.primary_instrument_id
           WHERE d.status=? OR ?='all'
           ORDER BY d.decision_date DESC""",
        (status, status),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def show_decision(decision_no: str, db_path=None) -> Optional[dict]:
    conn = connect(db_path)
    row = conn.execute(
        """SELECT d.*, i.code, i.name
           FROM decisions d
           LEFT JOIN instruments i ON i.id=d.primary_instrument_id
           WHERE d.decision_no=?""",
        (decision_no,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Trade log ─────────────────────────────────────────────────────────────

def log_trade(
    code: str,
    shares: float,
    price: float,
    side: str = "BUY",
    decision_no: Optional[str] = None,
    fees: float = 0.0,
    notes: str = "",
    trade_date: Optional[str] = None,
    db_path=None,
) -> int:
    """Record a trade. Returns trade id."""
    today = trade_date or date.today().isoformat()
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    amount = shares * price

    with transaction(db_path) as conn:
        iid = instrument_id_by_code(conn, code)
        if iid is None:
            raise ValueError(f"Instrument not found: {code}")

        did = None
        if decision_no:
            row = conn.execute(
                "SELECT id FROM decisions WHERE decision_no=?", (decision_no,)
            ).fetchone()
            if row:
                did = row["id"]

        conn.execute(
            """INSERT INTO trades
               (instrument_id, trade_date, side, shares, price, amount,
                fees, decision_id, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (iid, today, side.upper(), shares, price, amount,
             fees, did, notes or None, now),
        )
        trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return trade_id


# ── Stop rules ────────────────────────────────────────────────────────────

def add_stop_rule(
    code: str,
    decision_no: str,
    rule_type: str,
    trigger_kind: str,
    trigger_value: float,
    action: str,
    shares: Optional[float] = None,
    priority: int = 100,
    db_path=None,
) -> int:
    """Add a stop rule. Returns stop_rule id."""
    with transaction(db_path) as conn:
        iid = instrument_id_by_code(conn, code)
        if iid is None:
            raise ValueError(f"Instrument not found: {code}")
        row = conn.execute(
            "SELECT id FROM decisions WHERE decision_no=?", (decision_no,)
        ).fetchone()
        if not row:
            raise ValueError(f"Decision not found: {decision_no}")
        did = row["id"]

        conn.execute(
            """INSERT INTO stop_rules
               (decision_id, instrument_id, rule_type, trigger_kind,
                trigger_value, action, shares, priority, status)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (did, iid, rule_type.upper(), trigger_kind.upper(),
             trigger_value, action, shares, priority, "armed"),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def list_stop_rules(code: Optional[str] = None, armed_only: bool = True, db_path=None) -> list[dict]:
    conn = connect(db_path)
    where = "WHERE sr.status='armed'" if armed_only else "WHERE 1=1"
    if code:
        where += f" AND i.code='{code}'"
    rows = conn.execute(
        f"""SELECT sr.id, sr.rule_type, sr.trigger_kind, sr.trigger_value,
                   sr.action, sr.shares, sr.priority, sr.status, sr.fired_at,
                   i.code, i.name, d.decision_no
            FROM stop_rules sr
            JOIN instruments i ON i.id=sr.instrument_id
            JOIN decisions d ON d.id=sr.decision_id
            {where}
            ORDER BY sr.priority, i.code"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Apply trade to holdings ────────────────────────────────────────────────

def apply_trade(trade_id: int, db_path=None) -> dict:
    """Update holdings based on a trade record.

    BUY: increase shares, recalculate weighted average cost.
    SELL: decrease shares.
    Returns updated holding dict.
    """
    today = date.today().isoformat()
    with transaction(db_path) as conn:
        trade = conn.execute(
            "SELECT * FROM trades WHERE id=?", (trade_id,)
        ).fetchone()
        if not trade:
            raise ValueError(f"Trade {trade_id} not found")

        iid = trade["instrument_id"]
        # Get latest holding
        holding = conn.execute(
            """SELECT * FROM holdings WHERE instrument_id=?
               ORDER BY effective_date DESC LIMIT 1""",
            (iid,),
        ).fetchone()

        if holding is None:
            if trade["side"] == "SELL":
                raise ValueError("Cannot SELL: no existing holding")
            new_shares = trade["shares"]
            new_cost = trade["price"]
        else:
            cur_shares = holding["shares"]
            cur_cost = holding["cost_price"]
            if trade["side"] == "BUY":
                new_shares = cur_shares + trade["shares"]
                # Weighted average cost
                new_cost = (cur_shares * cur_cost + trade["shares"] * trade["price"]) / new_shares
            else:  # SELL
                new_shares = cur_shares - trade["shares"]
                if new_shares < 0:
                    raise ValueError(f"Cannot SELL {trade['shares']} shares: only {cur_shares} held")
                new_cost = cur_cost  # cost unchanged on sell

        conn.execute(
            """INSERT OR REPLACE INTO holdings
               (instrument_id, effective_date, shares, cost_price, source)
               VALUES (?,?,?,?,?)""",
            (iid, today, new_shares, new_cost, "trade_apply"),
        )
        # Mark trade as applied
        conn.execute(
            "UPDATE trades SET notes=COALESCE(notes||' ', '')||'[applied]' WHERE id=?",
            (trade_id,),
        )
        # Auto-deactivate instrument when position is fully exited
        if new_shares == 0:
            conn.execute(
                "UPDATE instruments SET active=0 WHERE id=?",
                (iid,),
            )

        # ── 自动更新现金余额 ──
        # 卖出 → 回笼资金流入活期存款；买入 → 从活期存款扣款
        trade_amount = trade["amount"]  # shares × price（不含手续费）

        # 港股/美股需换算为人民币
        inst = conn.execute(
            "SELECT market FROM instruments WHERE id=?", (iid,)
        ).fetchone()
        market = inst["market"] if inst else "A"

        # 汇率（近似，后续可改为从 quotes/外部源获取）
        _FX_RATES = {"A": 1.0, "HK": 0.92, "US": 7.2, "OTC": 1.0}
        fx_rate = _FX_RATES.get(market, 1.0)
        amount_cny = trade_amount * fx_rate

        # 查找活期存款 CASH instrument
        cash_inst = conn.execute(
            "SELECT id FROM instruments WHERE asset_class='CASH' AND active=1 LIMIT 1"
        ).fetchone()

        if cash_inst:
            cash_iid = cash_inst["id"]
            latest_cash = conn.execute(
                """SELECT balance FROM cash_balances
                   WHERE instrument_id=?
                   ORDER BY effective_date DESC LIMIT 1""",
                (cash_iid,),
            ).fetchone()

            current_balance = latest_cash["balance"] if latest_cash else 0.0

            if trade["side"] == "SELL":
                new_balance = current_balance + amount_cny
                extra = f" (×{fx_rate} 汇率)" if fx_rate != 1.0 else ""
                notes = f"卖出回笼资金 +{amount_cny:,.2f}{extra}（trade #{trade_id}）"
            else:  # BUY
                if amount_cny > current_balance:
                    raise ValueError(
                        f"现金不足：买入需 {amount_cny:,.2f}，活期余额 {current_balance:,.2f}"
                    )
                new_balance = current_balance - amount_cny
                extra = f" (×{fx_rate} 汇率)" if fx_rate != 1.0 else ""
                notes = f"买入支出 -{amount_cny:,.2f}{extra}（trade #{trade_id}）"

            conn.execute(
                """INSERT INTO cash_balances
                   (instrument_id, effective_date, balance, annual_rate, status, notes)
                   VALUES (?, ?, ?, 0.0, 'auto', ?)""",
                (cash_iid, today, new_balance, notes),
            )

    return {"trade_id": trade_id, "new_shares": new_shares, "new_cost": new_cost}


# ── Execution monitor ─────────────────────────────────────────────────────

def monitor_executions(db_path=None) -> list[dict]:
    """Check armed stop_rules against latest quotes. Returns triggered list."""
    conn = connect(db_path)
    # Get latest prices
    price_rows = conn.execute(
        """SELECT i.code, q.close AS price
           FROM quotes q JOIN instruments i ON i.id=q.instrument_id
           WHERE q.quote_date=(SELECT MAX(quote_date) FROM quotes q2
             WHERE q2.instrument_id=q.instrument_id)"""
    ).fetchall()
    prices = {r["code"]: r["price"] for r in price_rows}

    # Get armed stop rules with cost
    rules = conn.execute(
        """SELECT sr.id, sr.rule_type, sr.trigger_kind, sr.trigger_value,
                  sr.action, sr.shares, sr.priority,
                  i.code, i.name,
                  h.cost_price, h.shares AS held_shares
           FROM stop_rules sr
           JOIN instruments i ON i.id=sr.instrument_id
           LEFT JOIN holdings h ON h.instrument_id=sr.instrument_id
             AND h.effective_date=(SELECT MAX(effective_date) FROM holdings h2
               WHERE h2.instrument_id=sr.instrument_id)
           WHERE sr.status='armed'
           ORDER BY sr.priority"""
    ).fetchall()
    conn.close()

    triggered = []
    for r in rules:
        code = r["code"]
        price = prices.get(code)
        if price is None:
            continue

        fired = False
        if r["trigger_kind"] == "PRICE_ABS" and r["trigger_value"] is not None:
            if r["rule_type"] in ("GRID_SELL", "TAKE_PROFIT"):
                fired = price >= r["trigger_value"]
            elif r["rule_type"] in ("GRID_BUY", "STOP_LOSS"):
                fired = price <= r["trigger_value"]
        elif r["trigger_kind"] == "PNL_PCT" and r["trigger_value"] is not None:
            cost = r["cost_price"] or 0
            if cost > 0:
                pnl_pct = (price - cost) / cost
                fired = pnl_pct <= r["trigger_value"]

        status = "🔴 TRIGGERED" if fired else "🟢 armed"
        triggered.append({
            "id": r["id"],
            "code": code,
            "name": r["name"],
            "rule_type": r["rule_type"],
            "trigger_kind": r["trigger_kind"],
            "trigger_value": r["trigger_value"],
            "current_price": price,
            "action": r["action"],
            "priority": r["priority"],
            "fired": fired,
            "status": status,
        })

    return triggered
