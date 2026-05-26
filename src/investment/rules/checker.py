"""Alert checker: mirrors common.py check_alerts / check_etf_alerts / check_meituan_rsu_alerts.

Each check_* function returns a list of alert dicts:
  {type, severity, code, name, message}

write_alerts() persists them to the DB alerts table.
"""
from __future__ import annotations

import sys
from datetime import date
from typing import Any, Optional

_warned: set[str] = set()


def _warn_once(key: str) -> None:
    if key not in _warned:
        _warned.add(key)
        print(f"  ⚠ 配置缺失: {key}", file=sys.stderr)


_SEVERITY = {
    "force_reduce": "critical",
    "force_review": "critical",
    "force_exit": "critical",
    "trigger_ic_memo": "warning",
    "warning": "warning",
    "alert_only": "info",
}


def _sev(action: str) -> str:
    return _SEVERITY.get(action, "info")


def _pct(v: Optional[float]) -> str:
    return f"{v * 100:.2f}%" if v is not None else "N/A"


# ── 1) Single-stock drawdown (3 levels) ───────────────────────────────────

def check_stock_drawdown(rules: dict, positions: list[dict]) -> list[dict]:
    sl = rules.get("stock_rules", {}).get("stop_loss", {})
    l1 = sl.get("level_1_alert", {})
    l2 = sl.get("level_2_review", {})
    l3 = sl.get("level_3_hard", {})
    triggered = []

    for p in positions:
        pnl = p["pnl_pct"]
        l3_thresh = l3.get("threshold", -0.30)
        if pnl <= l3_thresh:
            triggered.append(dict(
                type="single_stock_drawdown_l3", severity=_sev(l3.get("action", "force_review")),
                code=p["code"], name=p["name"],
                message=f"{p['name']}({p['code']}) 回撤 {_pct(pnl)}，触发 L3 强制线 {_pct(l3_thresh)}",
            ))
            continue
        l2_thresh = l2.get("threshold", -0.20)
        if pnl <= l2_thresh:
            triggered.append(dict(
                type="single_stock_drawdown_l2", severity=_sev(l2.get("action", "trigger_ic_memo")),
                code=p["code"], name=p["name"],
                message=f"{p['name']}({p['code']}) 回撤 {_pct(pnl)}，触发 L2 审视线 {_pct(l2_thresh)}，需 7 天内更新 thesis",
            ))
            continue
        l1_thresh = l1.get("threshold", -0.10)
        if pnl <= l1_thresh:
            triggered.append(dict(
                type="single_stock_drawdown_l1", severity=_sev(l1.get("action", "alert_only")),
                code=p["code"], name=p["name"],
                message=f"{p['name']}({p['code']}) 回撤 {_pct(pnl)}，触发 L1 关注线 {_pct(l1_thresh)}",
            ))
    return triggered


# ── 2) Single-stock position limit ────────────────────────────────────────

def check_stock_position(rules: dict, positions: list[dict], total_c_value: float) -> list[dict]:
    cfg = rules.get("portfolio_rules", {}).get("concentration", {}).get("single_stock_max", {})
    threshold = cfg.get("threshold", 0.25)
    action = cfg.get("action", "force_reduce")
    triggered = []
    if total_c_value <= 0:
        return triggered
    for p in positions:
        ratio = p["market_value"] / total_c_value
        if ratio > threshold:
            triggered.append(dict(
                type="single_stock_position", severity=_sev(action),
                code=p["code"], name=p["name"],
                message=f"{p['name']}({p['code']}) 仓位 {_pct(ratio)}，超阈值 {_pct(threshold)}，需强制降权",
            ))
    return triggered


# ── 3) Account drawdown (3 levels) ────────────────────────────────────────

def check_account_drawdown(rules: dict, positions: list[dict]) -> list[dict]:
    total_cost = sum(p["cost_total"] for p in positions)
    total_value = sum(p["market_value"] for p in positions)
    if total_cost <= 0:
        return []
    dd = (total_value - total_cost) / total_cost
    dc = rules.get("portfolio_rules", {}).get("drawdown_control", {})
    triggered = []

    d3 = dc.get("level_3_hard", {})
    d3_thresh = -abs(d3.get("threshold", 0.20))
    if dd <= d3_thresh:
        triggered.append(dict(
            type="account_drawdown_l3", severity=_sev(d3.get("action", "force_reduce")),
            code="", name="账户",
            message=f"C 仓位总回撤 {_pct(dd)}，触发 L3 硬刹车 {_pct(d3_thresh)}，停止所有新建仓 60 天",
        ))
        return triggered

    d2 = dc.get("level_2_control", {})
    d2_thresh = -abs(d2.get("threshold", 0.15))
    if dd <= d2_thresh:
        triggered.append(dict(
            type="account_drawdown_l2", severity=_sev(d2.get("action", "force_review")),
            code="", name="账户",
            message=f"C 仓位总回撤 {_pct(dd)}，触发 L2 控制线 {_pct(d2_thresh)}，暂停新建个股 30 天",
        ))
        return triggered

    d1 = dc.get("level_1_alert", {})
    d1_thresh = -abs(d1.get("threshold", 0.10))
    if dd <= d1_thresh:
        triggered.append(dict(
            type="account_drawdown_l1", severity=_sev(d1.get("action", "alert_only")),
            code="", name="账户",
            message=f"C 仓位总回撤 {_pct(dd)}，触发 L1 预警线 {_pct(d1_thresh)}",
        ))
    return triggered


# ── 4) Theme concentration ─────────────────────────────────────────────────

def check_theme_concentration(rules: dict, positions: list[dict], total_c_value: float) -> list[dict]:
    if total_c_value <= 0:
        return []
    theme_cfg = rules.get("portfolio_rules", {}).get("theme_concentration", {})
    if not theme_cfg:
        return []
    triggered = []
    energy = theme_cfg.get("new_energy_and_power_chain", {})
    industries = set(energy.get("includes", []))
    threshold = energy.get("threshold", 0.35)
    action = energy.get("action", "warning")
    energy_value = sum(p["market_value"] for p in positions if p.get("industry", "") in industries)
    if energy_value > 0:
        ratio = energy_value / total_c_value
        if ratio > threshold:
            count = sum(1 for p in positions if p.get("industry", "") in industries)
            triggered.append(dict(
                type="theme_concentration", severity=_sev(action),
                code="", name="新能源/电力链",
                message=f"新能源/电力链主题占 C 仓位 {_pct(ratio)}，超阈值 {_pct(threshold)}，涵盖 {count} 只个股",
            ))
    return triggered


# ── 5) ETF drawdown ────────────────────────────────────────────────────────

def check_etf_drawdown(rules: dict, etf_positions: list[dict]) -> list[dict]:
    monitoring = rules.get("monitoring", {})
    dd_warn = monitoring.get("etf_drawdown_warn", 0.20)
    threshold = -abs(dd_warn)
    triggered = []
    for e in etf_positions:
        pnl = e.get("pnl_pct")
        if pnl is not None and pnl <= threshold:
            triggered.append(dict(
                type="etf_drawdown", severity="warning",
                code=e["code"], name=e["name"],
                message=f"{e['name']}({e['code']}) B档 ETF 回撤 {_pct(pnl)}，超关注线 {_pct(threshold)}",
            ))
    return triggered


# ── 6) ETF drift ───────────────────────────────────────────────────────────

def check_etf_drift(rules: dict, etf_positions: list[dict]) -> list[dict]:
    monitoring = rules.get("monitoring", {})
    drift_threshold = monitoring.get("etf_drift_threshold", 0.05)
    triggered = []
    for e in etf_positions:
        drift = e.get("drift_raw")
        if drift is not None and abs(drift) > drift_threshold:
            direction = "超配" if drift > 0 else "低配"
            triggered.append(dict(
                type="etf_drift", severity="info",
                code=e["code"], name=e["name"],
                message=f"{e['name']}({e['code']}) {direction} {_pct(abs(drift))}，偏离目标 {_pct(e['target_ratio'])}",
            ))
    return triggered


# ── 7) Meituan RSU ─────────────────────────────────────────────────────────

def check_meituan_rsu(capital: dict, current_hk_price: float) -> list[dict]:
    rsu_shares = capital.get("meituan_rsu_shares", 0)
    if rsu_shares <= 0:
        return []
    prev_value = capital.get("meituan_rsu_value", 0)
    current_value = rsu_shares * current_hk_price
    triggered = []
    if prev_value > 0:
        change_pct = (current_value - prev_value) / prev_value
        if change_pct <= -0.15:
            triggered.append(dict(
                type="meituan_rsu_drawdown", severity="warning",
                code="03690", name="美团RSU",
                message=f"美团RSU市值变动 {_pct(change_pct)}，从 ¥{prev_value:,.0f} 降至 ¥{current_value:,.0f}，超过 15% 关注线",
            ))
        elif change_pct <= -0.05:
            triggered.append(dict(
                type="meituan_rsu_daily_drop", severity="info",
                code="03690", name="美团RSU",
                message=f"美团RSU单日变动 {_pct(change_pct)}，关注",
            ))
    return triggered


# ── 8) Stop-rules monitor ──────────────────────────────────────────────────

def check_stop_rules(conn, positions: list[dict]) -> list[dict]:
    """Check armed stop_rules against current prices."""
    triggered = []
    price_map = {p["code"]: p.get("current_price", 0) for p in positions}

    rows = conn.execute(
        """SELECT sr.id, sr.rule_type, sr.trigger_kind, sr.trigger_value,
                  sr.action, sr.priority, i.code, i.name,
                  h.cost_price, h.shares
           FROM stop_rules sr
           JOIN instruments i ON i.id = sr.instrument_id
           LEFT JOIN holdings h ON h.instrument_id = sr.instrument_id
             AND h.effective_date = (
               SELECT MAX(effective_date) FROM holdings h2
               WHERE h2.instrument_id = sr.instrument_id
             )
           WHERE sr.status = 'armed'
           ORDER BY sr.priority"""
    ).fetchall()

    for row in rows:
        code = row["code"]
        price = price_map.get(code)
        if price is None:
            continue

        fired = False
        if row["trigger_kind"] == "PRICE_ABS" and row["trigger_value"] is not None:
            if row["rule_type"] in ("GRID_SELL", "TAKE_PROFIT"):
                fired = price >= row["trigger_value"]
            elif row["rule_type"] in ("GRID_BUY", "STOP_LOSS"):
                fired = price <= row["trigger_value"]
        elif row["trigger_kind"] == "PNL_PCT" and row["trigger_value"] is not None:
            cost = row["cost_price"] or 0
            if cost > 0:
                pnl_pct = (price - cost) / cost
                fired = pnl_pct <= row["trigger_value"]  # trigger_value is negative

        if fired:
            sev = "critical" if row["rule_type"] == "HARD_DD" else "warning"
            triggered.append(dict(
                type=f"stop_rule_{row['rule_type'].lower()}",
                severity=sev,
                code=code, name=row["name"],
                message=f"{row['name']}({code}) 触发 {row['rule_type']} @ {price:.3f}，动作: {row['action']}",
                stop_rule_id=row["id"],
            ))
    return triggered


# ── Persist alerts to DB ───────────────────────────────────────────────────

def write_alerts(
    alerts: list[dict],
    alert_date: Optional[str] = None,
    db_path=None,
) -> int:
    """Write alert list to DB. Deduplicates by (alert_date, alert_type, code)."""
    if not alerts:
        return 0
    from investment.core.db import transaction
    from investment.migration.utils import instrument_id_by_code

    today = alert_date or date.today().isoformat()
    inserted = 0

    with transaction(db_path) as conn:
        for a in alerts:
            code = a.get("code", "") or ""
            iid = instrument_id_by_code(conn, code) if code else None
            # Dedup: skip if same (date, type, code) already exists
            existing = conn.execute(
                """SELECT id FROM alerts
                   WHERE alert_date=? AND alert_type=? AND (instrument_id=? OR (instrument_id IS NULL AND ? IS NULL))""",
                (today, a["type"], iid, iid),
            ).fetchone()
            if existing:
                continue
            try:
                conn.execute(
                    """INSERT INTO alerts
                       (alert_date, alert_type, severity, instrument_id, message)
                       VALUES (?,?,?,?,?)""",
                    (today, a["type"], a["severity"], iid, a["message"]),
                )
                inserted += conn.execute("SELECT changes()").fetchone()[0]
            except Exception as e:
                print(f"  [警告] 写入告警失败 {a['type']}: {e}")

        # Mark triggered stop_rules
        for a in alerts:
            if "stop_rule_id" in a:
                conn.execute(
                    "UPDATE stop_rules SET status='triggered', fired_at=? WHERE id=?",
                    (today, a["stop_rule_id"]),
                )
    return inserted


# ── Convenience: run all checks ────────────────────────────────────────────

def run_all_checks(
    rules: dict,
    capital: dict,
    c_positions: list[dict],
    etf_positions: list[dict],
    total_c_value: float,
    meituan_price: float = 0,
    conn=None,
) -> list[dict]:
    alerts = []
    alerts += check_stock_drawdown(rules, c_positions)
    alerts += check_stock_position(rules, c_positions, total_c_value)
    alerts += check_account_drawdown(rules, c_positions)
    alerts += check_theme_concentration(rules, c_positions, total_c_value)
    alerts += check_etf_drawdown(rules, etf_positions)
    alerts += check_etf_drift(rules, etf_positions)
    if meituan_price > 0:
        alerts += check_meituan_rsu(capital, meituan_price)
    if conn is not None:
        alerts += check_stop_rules(conn, c_positions)
    return alerts
