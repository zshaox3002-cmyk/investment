"""Dashboard HTML generator — DB-backed replacement for scripts/dashboard.py.

Reads all data from portfolio.db instead of CSV/YAML files.
Preserves the same HTML structure and CSS as the original.
"""
from __future__ import annotations

import re
import yaml
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from investment.core.db import connect
from investment.core.settings import (
    ALERTS_DIR, CAPITAL_PATH, CONFIG_DIR, RULES_PATH, ROOT_DIR,
)

OUTPUT_PATH = ROOT_DIR / "DASHBOARD.html"
TRACKER_PATH = CONFIG_DIR / "execution_tracker.yaml"
MACRO_PATH = CONFIG_DIR / "macro.md"


# ── Helpers (identical to original) ──────────────────────────────────────

def _fmt_pct(val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    return f"{val * 100:.1f}%"


def _fmt_cny(val: float) -> str:
    if abs(val) >= 1_000_000:
        return f"¥{val/1_000_000:.2f}M"
    elif abs(val) >= 10_000:
        return f"¥{val/10_000:.1f}万"
    return f"¥{val:,.0f}"


def _status_badge(status: str) -> tuple[str, str]:
    mapping = {
        "pending": ("badge-pending", "待执行"),
        "in_progress": ("badge-progress", "进行中"),
        "done": ("badge-done", "已完成"),
        "skipped": ("badge-skipped", "已跳过"),
        "blocked": ("badge-blocked", "条件未触发"),
        "overdue": ("badge-overdue", "已逾期"),
    }
    return mapping.get(status, ("badge-pending", status))


def _severity_class(severity: str) -> str:
    return {"critical": "sev-critical", "warning": "sev-warning", "info": "sev-info"}.get(
        severity, "sev-info"
    )


def _days_between(d1: str, d2: Optional[str] = None) -> int:
    try:
        dt1 = datetime.strptime(d1, "%Y-%m-%d").date()
        dt2 = datetime.strptime(d2, "%Y-%m-%d").date() if d2 else date.today()
        return (dt1 - dt2).days
    except (ValueError, TypeError):
        return 999


def _compute_urgency(item: dict, today: str) -> str:
    check_date = item.get("deadline", "") or item.get("planned_date", "") or item.get("planned_end", "")
    if not check_date:
        return "P4"
    days = _days_between(str(check_date), today)
    if days < 0:
        return "P0"
    elif days == 0:
        return "P0"
    elif days <= 3:
        return "P1"
    elif days <= 7:
        return "P2"
    elif days <= 30:
        return "P3"
    return "P4"


def _urgency_sort_key(urgency: str) -> int:
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}.get(urgency, 5)


def _priority_order(status: str) -> int:
    if status == "overdue":
        return 0
    if status == "in_progress":
        return 1
    if status == "pending":
        return 2
    if status == "blocked":
        return 3
    return 4


def _urgency_badge(urgency: str) -> str:
    colors = {"P0": "#c53030", "P1": "#d69e2e", "P2": "#2b6cb0", "P3": "#718096", "P4": "#a0aec0"}
    bg = {"P0": "#fff5f5", "P1": "#fffbeb", "P2": "#ebf8ff", "P3": "#f7fafc", "P4": "#f7fafc"}
    color = colors.get(urgency, "#a0aec0")
    bg_color = bg.get(urgency, "#f7fafc")
    return (f'<span style="display:inline-block;padding:0 6px;border-radius:4px;'
            f'font-size:10px;font-weight:700;color:{color};background:{bg_color};'
            f'margin-left:4px">{urgency}</span>')


def _task_type_icon(task_type: str) -> str:
    return {"trade": "💰", "research": "🔬", "compliance": "⚠️", "monitor": "👁"}.get(task_type, "📋")


# ── Data loading from DB ──────────────────────────────────────────────────

def _load_portfolio_from_db(conn) -> dict:
    """Load portfolio summary from DB views."""
    rows = conn.execute(
        "SELECT * FROM v_portfolio_snapshot ORDER BY tranche, code"
    ).fetchall()

    by_tranche: dict[str, list] = {"A": [], "B": [], "C": [], "D": []}
    for r in rows:
        t = r["tranche"]
        if t in by_tranche:
            by_tranche[t].append(dict(r))

    # A tranche: cash_balances
    a_rows = conn.execute(
        """SELECT i.code, i.name, i.asset_class, cb.balance, cb.annual_rate, cb.notes
           FROM cash_balances cb JOIN instruments i ON i.id=cb.instrument_id
           WHERE i.tranche='A'
           AND cb.effective_date=(SELECT MAX(e) FROM cash_balances cb2
             JOIN (SELECT MAX(effective_date) AS e FROM cash_balances cb3
               WHERE cb3.instrument_id=cb.instrument_id) x ON 1=1
             WHERE cb2.instrument_id=cb.instrument_id)""",
    ).fetchall()

    # Simpler query
    a_rows = conn.execute(
        """SELECT i.code, i.name, i.asset_class, cb.balance, cb.annual_rate, cb.notes
           FROM cash_balances cb JOIN instruments i ON i.id=cb.instrument_id
           WHERE i.tranche='A'"""
    ).fetchall()

    d_rows = conn.execute(
        """SELECT i.code, i.name, cb.balance, cb.notes
           FROM cash_balances cb JOIN instruments i ON i.id=cb.instrument_id
           WHERE i.tranche='D'"""
    ).fetchall()

    # Get latest RSU price from quotes (stored as MTRSU or 03690)
    rsu_price_row = conn.execute(
        """SELECT q.close FROM quotes q
           JOIN instruments i ON i.id=q.instrument_id
           WHERE i.tranche='D' ORDER BY q.quote_date DESC LIMIT 1"""
    ).fetchone()
    rsu_price = rsu_price_row["close"] if rsu_price_row else 0.0

    total_a = sum(r["balance"] for r in a_rows)
    total_b = sum(r["market_value"] for r in by_tranche["B"])
    total_c = sum(r["market_value"] for r in by_tranche["C"])
    rsu_shares = d_rows[0]["balance"] if d_rows else 0
    total_d = rsu_shares * rsu_price if rsu_price > 0 else rsu_shares

    return {
        "a_rows": [dict(r) for r in a_rows],
        "b_positions": by_tranche["B"],
        "c_positions": by_tranche["C"],
        "d_rows": [dict(r) for r in d_rows],
        "total_a": total_a,
        "total_b": total_b,
        "total_c": total_c,
        "total_d": total_d,
        "total_all": total_a + total_b + total_c + total_d,
        "rsu_price": rsu_price,
    }


_SEVERITY_ORDER = {"critical": 3, "warning": 2, "info": 1}


def _dedup_alerts(alerts: list[dict]) -> list[dict]:
    """Remove redundant alerts.

    Pass 1: exact duplicates (same alert_type + stock_code) — prefer rows
            without body_path (cleaner checker-generated message).
    Pass 2: drawdown levels (same stock_code + same family) — keep only the
            highest severity level (L3 > L2 > L1).
    """
    if not alerts:
        return alerts

    # Pass 1: exact dedup by (alert_type, stock_code)
    seen: dict[tuple, dict] = {}
    deduped: list[dict] = []
    for a in alerts:
        key = (a["alert_type"], a["stock_code"])
        if key in seen:
            existing = seen[key]
            # Prefer the alert without body_path (cleaner message)
            if a.get("file") == "" and existing.get("file") != "":
                idx = deduped.index(existing)
                deduped[idx] = a
                seen[key] = a
        else:
            seen[key] = a
            deduped.append(a)

    # Pass 2: for leveled alerts, keep only the highest severity per (stock_code, family)
    families: dict[tuple, list[tuple[int, dict]]] = {}
    for a in deduped:
        m = re.match(r"^(.+)_l([123])$", a["alert_type"])
        if not m:
            continue
        family = m.group(1)
        key = (a["stock_code"], family)
        families.setdefault(key, []).append((_SEVERITY_ORDER.get(a["severity"], 0), a))

    drop_ids: set[int] = set()
    for key, items in families.items():
        if len(items) <= 1:
            continue
        items.sort(key=lambda x: x[0], reverse=True)
        for _, a in items[1:]:
            drop_ids.add(id(a))

    result = [a for a in deduped if id(a) not in drop_ids]

    # Recompute counts
    counts = {"critical": 0, "warning": 0, "info": 0}
    for a in result:
        counts[a["severity"]] = counts.get(a["severity"], 0) + 1
    return result


def _load_alerts_from_db(conn, today: str) -> dict:
    """Load today's alerts from DB."""
    rows = conn.execute(
        """SELECT a.id, a.alert_type, a.severity, a.message, a.body_path,
                  i.code, i.name
           FROM alerts a
           JOIN instruments i ON i.id=a.instrument_id AND i.active=1
           WHERE a.alert_date=? AND a.acknowledged=0
           ORDER BY CASE a.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END""",
        (today,),
    ).fetchall()

    result = {"critical": 0, "warning": 0, "info": 0, "total": len(rows), "latest": []}
    for r in rows:
        sev = r["severity"]
        snippet = ""
        if r["body_path"]:
            bp = ROOT_DIR / r["body_path"]
            if bp.exists():
                snippet = "\n".join(bp.read_text(encoding="utf-8").splitlines()[:15])
        result["latest"].append({
            "file": r["body_path"] or "",
            "title": r["message"][:80],
            "severity": sev,
            "stock_code": r["code"] or "",
            "alert_type": r["alert_type"],
            "snippet": snippet or r["message"],
        })

    # Dedup: remove redundant alerts (exact duplicates + lower drawdown levels)
    result["latest"] = _dedup_alerts(result["latest"])
    result["total"] = len(result["latest"])
    for a in result["latest"]:
        result[a["severity"]] = result.get(a["severity"], 0) + 1
    return result


def _load_executions_from_db(conn, today: str) -> list[dict]:
    """Load pending/in-progress executions as action items."""
    rows = conn.execute(
        """SELECT e.id, e.plan_name, e.phase, e.batch, e.side,
                  e.planned_date, e.planned_end, e.planned_amount,
                  e.trigger_type, e.trigger_spec, e.status, e.notes,
                  i.code, i.name, i.tranche
           FROM executions e JOIN instruments i ON i.id=e.instrument_id
           WHERE e.status IN ('pending','in_progress')
           ORDER BY e.planned_date NULLS LAST, e.id"""
    ).fetchall()

    actions = []
    for r in rows:
        item = dict(r)
        item["action"] = f"{r['side']} {r['name']}({r['code']}) — {r['phase']} batch{r['batch']}"
        if r["planned_amount"]:
            item["action"] += f" ¥{r['planned_amount']:,.0f}"
        item["task_type"] = "trade"
        item["_section"] = r["plan_name"]
        item["_effective_status"] = r["status"]
        item["_urgency"] = _compute_urgency(item, today)
        actions.append(item)

    actions.sort(key=lambda a: (
        _urgency_sort_key(a.get("_urgency", "P4")),
        _priority_order(a["_effective_status"]),
        str(a.get("planned_date") or "9999-99-99"),
    ))
    return actions


def _load_breaches_from_db(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT rb.rule_path, rb.current_value, rb.threshold,
                  rb.breach_amount, rb.grace_period_expires, rb.status, rb.notes,
                  i.code, i.name
           FROM rule_breaches rb
           LEFT JOIN instruments i ON i.id=rb.instrument_id
           WHERE rb.status IN ('active','remediating')"""
    ).fetchall()
    return [dict(r) for r in rows]


def _load_tracker_yaml() -> dict:
    """Load execution_tracker.yaml for WBS goals and checklist items."""
    if not TRACKER_PATH.exists():
        return {}
    with open(TRACKER_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_macro_events() -> list[dict]:
    events = []
    if not MACRO_PATH.exists():
        return events
    text = MACRO_PATH.read_text(encoding="utf-8")
    in_table = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("|") and "日期" in line and "事件" in line:
            in_table = True
            continue
        if in_table and line.startswith("|") and not line.startswith("|--"):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3 and parts[1]:
                events.append({"date": parts[1], "event": parts[2] if len(parts) > 2 else "",
                                "impact": parts[3] if len(parts) > 3 else ""})
        elif in_table and not line.startswith("|"):
            in_table = False
    return events


# ── HTML section renderers ────────────────────────────────────────────────

def _render_metrics(portfolio: dict, capital: dict, alerts: dict) -> str:
    total = portfolio["total_all"]
    total_a = portfolio["total_a"]
    total_b = portfolio["total_b"]
    total_c = portfolio["total_c"]
    total_d = portfolio["total_d"]
    target_a = capital.get("target_a", 0)
    target_b = capital.get("target_b", 0)
    target_c = capital.get("target_c", 0)

    def card(cls, label, value, sub=""):
        return (f'<div class="metric-card {cls}">'
                f'<div class="mc-label">{label}</div>'
                f'<div class="mc-value">{value}</div>'
                f'<div class="mc-sub">{sub}</div></div>')

    a_pct = _fmt_pct(total_a / total) if total else "N/A"
    b_pct = _fmt_pct(total_b / total) if total else "N/A"
    c_pct = _fmt_pct(total_c / total) if total else "N/A"
    d_pct = _fmt_pct(total_d / total) if total else "N/A"
    alert_count = alerts["critical"] + alerts["warning"]

    cards = [
        card("card-total", "总资产", _fmt_cny(total), f"再平衡基准 {_fmt_cny(total_a+total_b+total_c)}"),
        card("card-a", "A档 现金/债券", _fmt_cny(total_a), f"{a_pct} | 目标 {_fmt_cny(target_a)}"),
        card("card-b", "B档 核心ETF", _fmt_cny(total_b), f"{b_pct} | 目标 {_fmt_cny(target_b)}"),
        card("card-c", "C档 主动选股", _fmt_cny(total_c), f"{c_pct} | 目标 {_fmt_cny(target_c)}"),
        card("card-d", "D档 美团RSU", _fmt_cny(total_d), f"{d_pct} | 排除再平衡"),
        card("card-alert", "活跃告警", str(alert_count),
             f"🔴 {alerts['critical']} 严重 | 🟡 {alerts['warning']} 警告"),
    ]
    return '<div class="metrics">' + "".join(cards) + "</div>"


def _render_alerts(alerts: dict) -> str:
    if not alerts["latest"]:
        return '<div class="alert-box alert-info"><span class="alert-icon">✅</span><div>今日无活跃告警</div></div>'
    parts = []
    for i, a in enumerate(alerts["latest"]):
        sev = a["severity"]
        sev_cls = _severity_class(sev)
        dot_cls = f"dot-{sev}"
        icon = {"critical": "🔴", "warning": "🟡", "info": "🟢"}.get(sev, "⚪")
        type_label = a["alert_type"].replace("_", " ").title()
        stock = f'<span class="alert-stock">{a["stock_code"]}</span>' if a["stock_code"] else ""
        snippet_html = a["snippet"].replace("<", "&lt;").replace(">", "&gt;")
        parts.append(
            f'<div class="alert-card">'
            f'<div class="alert-card-header {sev_cls}" id="alert-header-{i}" onclick="toggleAlert({i})">'
            f'<span class="alert-dot {dot_cls}"></span>'
            f'<span class="alert-type-label">{icon} {type_label}</span>'
            f'{stock}'
            f'<span class="alert-title">{a["title"]}</span>'
            f'<span class="alert-expand">▼</span>'
            f'</div>'
            f'<div class="alert-card-body" id="alert-body-{i}">{snippet_html}</div>'
            f'</div>'
        )
    return "\n".join(parts)


def _render_action_table(actions: list[dict]) -> str:
    if not actions:
        return '<div class="alert-box alert-info"><span class="alert-icon">✅</span><div>无待执行任务</div></div>'
    rows = []
    for a in actions:
        status = a["_effective_status"]
        urgency = a.get("_urgency", "P4")
        cls, label = _status_badge(status)
        icon = _task_type_icon(a.get("task_type", ""))
        ub = _urgency_badge(urgency)
        date_s = str(a.get("planned_date") or a.get("planned_end") or "—")
        amount_s = _fmt_cny(a["planned_amount"]) if a.get("planned_amount") else "—"
        notes = str(a.get("notes") or "")[:60]
        rows.append(
            f'<tr id="task-{a.get("id","")}">'
            f'<td>{icon} {a.get("action","")}{ub}</td>'
            f'<td class="date-col">{date_s}</td>'
            f'<td class="amount">{amount_s}</td>'
            f'<td><span class="badge {cls}">{label}</span></td>'
            f'<td style="color:#718096;font-size:12px">{notes}</td>'
            f'</tr>'
        )
    header = ('<table class="data-table"><thead><tr>'
              '<th>任务</th><th>计划日期</th><th>金额</th><th>状态</th><th>备注</th>'
              '</tr></thead><tbody>')
    return header + "".join(rows) + "</tbody></table>"


def _render_progress(tracker: dict) -> str:
    sections = [
        ("c_tranche_restructure", "C仓位重组", "fill-purple"),
        ("b_tranche_build", "B仓ETF建仓", "fill-blue"),
        ("compliance_remediation", "合规整改", "fill-orange"),
        ("thesis_maintenance", "Thesis维护", "fill-green"),
    ]
    cards = []
    for key, label, fill_cls in sections:
        items = tracker.get(key, [])
        total = len(items)
        done = sum(1 for i in items if i.get("status") == "done")
        pct = int(done / total * 100) if total > 0 else 0
        cards.append(
            f'<div class="progress-card">'
            f'<div class="prog-label">{label}<span class="prog-stat">{done}/{total} ({pct}%)</span></div>'
            f'<div class="progress-bar"><div class="fill {fill_cls}" style="width:{pct}%"></div></div>'
            f'</div>'
        )
    return '<div class="progress-grid">' + "".join(cards) + "</div>"


def _render_compliance(breaches: list[dict]) -> str:
    if not breaches:
        return '<div class="alert-box alert-info"><span class="alert-icon">✅</span><div>无活跃合规违规</div></div>'
    rows = []
    for b in breaches:
        grace = b.get("grace_period_expires") or "—"
        days_left = _days_between(str(grace)) if grace != "—" else 999
        urgency = "🔴" if days_left <= 7 else "🟡" if days_left <= 30 else "🟢"
        stock = b.get("name") or b.get("code") or "组合层面"
        rows.append(
            f'<tr>'
            f'<td>{urgency} {b["rule_path"]}</td>'
            f'<td>{stock}</td>'
            f'<td class="amount">{_fmt_pct(b["current_value"])}</td>'
            f'<td class="amount">{_fmt_pct(b["threshold"])}</td>'
            f'<td class="amount">{_fmt_pct(b["breach_amount"])}</td>'
            f'<td class="date-col">{grace} ({days_left}天)</td>'
            f'<td><span class="badge badge-progress">{b["status"]}</span></td>'
            f'</tr>'
        )
    header = ('<table class="data-table"><thead><tr>'
              '<th>规则</th><th>标的</th><th>当前值</th><th>阈值</th><th>超标量</th><th>宽限期</th><th>状态</th>'
              '</tr></thead><tbody>')
    return header + "".join(rows) + "</tbody></table>"


def _render_pre_market(tracker: dict, events: list[dict]) -> str:
    items = [
        "查看昨日告警，确认无遗漏",
        "检查今日待执行任务（执行看板）",
        "确认冷静期是否满足",
        "查看宏观日历，确认今日重要事件",
        "检查 stop_rules 是否有触发",
    ]
    checks = "".join(
        f'<div class="checklist-item"><input type="checkbox"><span>{item}</span></div>'
        for item in items
    )
    events_html = ""
    if events:
        today = date.today().isoformat()
        upcoming = [e for e in events if str(e.get("date", "")) >= today][:5]
        if upcoming:
            event_rows = "".join(
                f'<div class="event-row">'
                f'<span class="event-date {"urgent" if _days_between(str(e["date"])) <= 3 else ""}">{e["date"]}</span>'
                f'<span>{e["event"]}</span>'
                f'</div>'
                for e in upcoming
            )
            events_html = f'<div style="margin-top:12px"><strong style="font-size:12px;color:#718096">近期宏观事件</strong>{event_rows}</div>'
    return f'<div class="checklist">{checks}{events_html}</div>'


def _render_post_market(tracker: dict) -> str:
    items = [
        "运行 inv snapshot pull 更新行情",
        "检查新触发告警，记录处理意见",
        "更新执行任务状态",
        "如有交易，运行 inv trade log 记录",
        "检查 stop_rules 触发状态",
    ]
    checks = "".join(
        f'<div class="checklist-item"><input type="checkbox"><span>{item}</span></div>'
        for item in items
    )
    return f'<div class="checklist">{checks}</div>'


def _render_upcoming(tracker: dict) -> str:
    events_raw = tracker.get("upcoming_events", [])
    if not events_raw:
        return '<div style="color:#a0aec0;font-size:13px;padding:12px">暂无记录的未来事件</div>'
    today = date.today().isoformat()
    rows = []
    for e in events_raw:
        if not isinstance(e, dict) or not e.get("date"):
            continue
        d = str(e["date"])
        days = _days_between(d, today)
        if days < -7:
            continue
        urgent = days <= 3
        rows.append(
            f'<div class="event-row">'
            f'<span class="event-date {"urgent" if urgent else ""}">{d}</span>'
            f'<span>{e.get("event","")}</span>'
            f'<span style="color:#a0aec0;font-size:11px;margin-left:auto">{e.get("impact","")}</span>'
            f'</div>'
        )
    if not rows:
        return '<div style="color:#a0aec0;font-size:13px;padding:12px">未来 30 天无记录事件</div>'
    return '<div style="background:#fff;border-radius:10px;padding:14px 18px;box-shadow:0 1px 4px rgba(0,0,0,.06)">' + "".join(rows) + "</div>"


# ── Main render ───────────────────────────────────────────────────────────

_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei","Helvetica Neue",sans-serif;background:#f0f2f5;color:#1a202c;font-size:14px;line-height:1.6}
#topbar{background:#0d1b2a;color:#fff;padding:0 28px;height:56px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(0,0,0,.2)}
#topbar .title{font-size:17px;font-weight:700;letter-spacing:.02em}
#topbar .title span{color:#5b9bd5}
#topbar .meta{font-size:12px;color:#8899aa}
#topbar .meta .mode{background:rgba(255,255,255,.12);padding:3px 10px;border-radius:12px;margin-left:10px}
#topbar .alerts-badge{background:#c53030;color:#fff;border-radius:12px;padding:2px 10px;font-size:11px;font-weight:600;margin-left:8px}
#main{max-width:1320px;margin:0 auto;padding:24px}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:24px}
.metric-card{background:#fff;border-radius:10px;padding:18px 20px;box-shadow:0 1px 4px rgba(0,0,0,.06);border-left:4px solid #e2e8f0}
.metric-card .mc-label{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:#718096;margin-bottom:6px}
.metric-card .mc-value{font-size:22px;font-weight:700}
.metric-card .mc-sub{font-size:11px;color:#a0aec0;margin-top:3px}
.metric-card.card-total{border-left-color:#0d1b2a}
.metric-card.card-a{border-left-color:#00875a}
.metric-card.card-b{border-left-color:#0066cc}
.metric-card.card-c{border-left-color:#7c3aed}
.metric-card.card-d{border-left-color:#718096}
.metric-card.card-alert{border-left-color:#c53030}
.section{margin-bottom:28px}
.section-title{font-size:15px;font-weight:700;color:#1a202c;margin-bottom:14px;padding-bottom:8px;border-bottom:2px solid #e2e8f0;display:flex;align-items:center;gap:10px}
.section-title .icon{font-size:18px}
.data-table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.data-table th{background:#f7f9fc;color:#4a5568;font-weight:600;font-size:12px;padding:10px 12px;text-align:left;border-bottom:2px solid #e2e8f0;white-space:nowrap}
.data-table td{padding:10px 12px;border-bottom:1px solid #f0f4f8;font-size:13px;color:#4a5568}
.data-table tr:hover td{background:#f7f9fc}
.data-table .amount{text-align:right;font-variant-numeric:tabular-nums}
.data-table .date-col{white-space:nowrap;font-size:12px}
.badge{display:inline-block;padding:2px 10px;border-radius:10px;font-size:11px;font-weight:600;white-space:nowrap}
.badge-pending{background:#edf2f7;color:#718096}
.badge-progress{background:#ebf4ff;color:#0066cc}
.badge-done{background:#f0fff4;color:#276749;text-decoration:line-through}
.badge-skipped{background:#f7fafc;color:#a0aec0;text-decoration:line-through}
.badge-blocked{background:#fffbeb;color:#b7791f}
.badge-overdue{background:#fff5f5;color:#c53030;font-weight:700}
.progress-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
.progress-card{background:#fff;border-radius:10px;padding:14px 18px;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.progress-card .prog-label{font-size:13px;font-weight:600;color:#2d3748;margin-bottom:8px;display:flex;justify-content:space-between}
.progress-card .prog-label .prog-stat{font-size:12px;color:#718096;font-weight:400}
.progress-bar{height:8px;background:#edf2f7;border-radius:4px;overflow:hidden}
.progress-bar .fill{height:100%;border-radius:4px;transition:width .3s}
.fill-blue{background:linear-gradient(90deg,#0066cc,#4299e1)}
.fill-green{background:linear-gradient(90deg,#00875a,#38b2ac)}
.fill-purple{background:linear-gradient(90deg,#7c3aed,#9f7aea)}
.fill-orange{background:linear-gradient(90deg,#d69e2e,#ecc94b)}
.alert-box{display:flex;align-items:flex-start;gap:10px;padding:12px 16px;border-radius:8px;margin-bottom:8px;font-size:13px}
.alert-critical{background:#fff5f5;border:1px solid #feb2b2;color:#742a2a}
.alert-warning{background:#fffbeb;border:1px solid #fbd38d;color:#744210}
.alert-info{background:#ebf8ff;border:1px solid #bee3f8;color:#2a4365}
.alert-icon{font-size:16px;flex-shrink:0}
.alert-card{background:#fff;border-radius:10px;margin-bottom:10px;box-shadow:0 1px 4px rgba(0,0,0,.06);overflow:hidden;cursor:pointer;transition:box-shadow .15s}
.alert-card:hover{box-shadow:0 2px 8px rgba(0,0,0,.1)}
.alert-card-header{display:flex;align-items:center;gap:10px;padding:12px 16px;font-size:13px;border-left:4px solid #e2e8f0;user-select:none}
.alert-card-header.sev-critical{border-left-color:#c53030}
.alert-card-header.sev-warning{border-left-color:#d69e2e}
.alert-card-header.sev-info{border-left-color:#3182ce}
.alert-card-header .alert-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.alert-dot.dot-critical{background:#c53030}
.alert-dot.dot-warning{background:#d69e2e}
.alert-dot.dot-info{background:#3182ce}
.alert-card-header .alert-type-label{font-weight:600;color:#2d3748;white-space:nowrap}
.alert-card-header .alert-stock{background:#edf2f7;padding:1px 8px;border-radius:4px;font-size:11px;font-weight:600;color:#4a5568;white-space:nowrap}
.alert-card-header .alert-title{flex:1;color:#4a5568;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.alert-card-header .alert-expand{font-size:11px;color:#a0aec0;transition:transform .2s;flex-shrink:0}
.alert-card-header.expanded .alert-expand{transform:rotate(180deg)}
.alert-card-body{display:none;padding:12px 16px 16px 30px;font-size:12px;color:#718096;border-top:1px solid #f0f4f8;white-space:pre-wrap;max-height:300px;overflow-y:auto}
.checklist{background:#fff;border-radius:10px;padding:16px 20px;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.checklist-item{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #f0f4f8;font-size:13px;color:#4a5568}
.checklist-item:last-child{border-bottom:none}
.checklist-item input[type=checkbox]{width:18px;height:18px;accent-color:#0066cc;flex-shrink:0}
.event-row{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid #f0f4f8;font-size:13px}
.event-row:last-child{border-bottom:none}
.event-date{background:#edf2f7;padding:2px 10px;border-radius:6px;font-size:11px;font-weight:600;white-space:nowrap;color:#4a5568}
.event-date.urgent{background:#fff5f5;color:#c53030}
.mode-pre .pre-market-section{display:block}
.mode-pre .post-market-section{display:none}
.mode-post .post-market-section{display:block}
.mode-post .pre-market-section{display:none}
.mode-standard .pre-market-section,.mode-standard .post-market-section{display:block}
.col2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
#footer{text-align:center;padding:20px;font-size:11px;color:#a0aec0;border-top:1px solid #e2e8f0;margin-top:32px}
@media(max-width:800px){.metrics{grid-template-columns:repeat(2,1fr)}.col2{grid-template-columns:1fr}#topbar{padding:0 16px}#main{padding:16px}}
"""

_JS = """
function toggleAlert(id){var b=document.getElementById('alert-body-'+id);var h=document.getElementById('alert-header-'+id);if(b.style.display==='none'||!b.style.display){b.style.display='block';h.classList.add('expanded')}else{b.style.display='none';h.classList.remove('expanded')}}
function scrollToAlertSection(){var e=document.getElementById('section-alerts');if(e){e.scrollIntoView({behavior:'smooth'});var cards=e.querySelectorAll('.alert-card-body');cards.forEach(function(c){c.style.display='block'});var headers=e.querySelectorAll('.alert-card-header');headers.forEach(function(h){h.classList.add('expanded')})}}
function toggleCausal(idx){var d=document.getElementById('causal-detail-'+idx);if(d.style.display==='none'||!d.style.display){d.style.display='table-row'}else{d.style.display='none'}}
"""


def render(
    portfolio: dict,
    capital: dict,
    alerts: dict,
    actions: list[dict],
    breaches: list[dict],
    tracker: dict,
    events: list[dict],
    mode: str,
    causal_section: str = "",
) -> str:
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][today.weekday()]
    alert_count = alerts["critical"] + alerts["warning"]
    mode_label = {"standard": "全景", "pre-market": "盘前", "post-market": "盘后"}.get(mode, "全景")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>每日战情室 · {today_str}</title>
<style>{_CSS}</style>
</head>
<body class="mode-{mode.replace('-','_') if mode != 'pre-market' else 'pre'} mode-{mode.replace('-','_') if mode != 'post-market' else 'post'}">
<header id="topbar">
  <div class="title">每日战情室 · <span>{today_str}</span> {weekday}</div>
  <div class="meta">
    活跃告警 <span class="alerts-badge" onclick="scrollToAlertSection()" style="cursor:pointer">{alert_count}</span>
    <span class="mode">{mode_label}</span>
  </div>
</header>
<div id="main">

<div class="section">
  <div class="section-title"><span class="icon">📊</span> 组合概览</div>
  {_render_metrics(portfolio, capital, alerts)}
</div>

<div class="section" id="section-alerts">
  <div class="section-title"><span class="icon">🔔</span> 活跃告警详情</div>
  {_render_alerts(alerts)}
</div>

<div class="section">
  <div class="section-title"><span class="icon">📋</span> 执行看板</div>
  {_render_action_table(actions)}
</div>

<div class="section">
  <div class="section-title"><span class="icon">📈</span> 执行进度</div>
  {_render_progress(tracker)}
</div>

<div class="section">
  <div class="section-title"><span class="icon">⚠️</span> 合规预警</div>
  {_render_compliance(breaches)}
</div>

{causal_section}

<div class="col2">
  <div class="section pre-market-section">
    <div class="section-title"><span class="icon">🌅</span> 盘前检查清单</div>
    {_render_pre_market(tracker, events)}
  </div>
  <div class="section post-market-section">
    <div class="section-title"><span class="icon">🌇</span> 盘后检查清单</div>
    {_render_post_market(tracker)}
  </div>
</div>

<div class="section">
  <div class="section-title"><span class="icon">📅</span> 未来 30 天关键事件</div>
  {_render_upcoming(tracker)}
</div>

</div>
<div id="footer">
  每日战情室 · 由 inv dashboard render 自动生成 · {datetime.now().strftime('%Y-%m-%d %H:%M')} · 数据来源: portfolio.db
</div>
<script>{_JS}</script>
</body>
</html>"""


# ── Entry point ───────────────────────────────────────────────────────────

def run(mode: str = "standard", db_path=None) -> Path:
    """Generate DASHBOARD.html and return its path."""
    today = date.today().isoformat()

    with open(CAPITAL_PATH, encoding="utf-8") as f:
        capital = yaml.safe_load(f) or {}

    conn = connect(db_path)
    portfolio = _load_portfolio_from_db(conn)
    alerts = _load_alerts_from_db(conn, today)
    actions = _load_executions_from_db(conn, today)
    breaches = _load_breaches_from_db(conn)

    # Causal impact chain section
    causal_section = ""
    try:
        from investment.causal.dashboard_section import load_causal_assessments, render_causal_section
        assessments = load_causal_assessments(conn, today)
        causal_section = render_causal_section(assessments)
    except Exception:
        pass

    conn.close()

    tracker = _load_tracker_yaml()
    events = _load_macro_events()

    html = render(
        portfolio, capital, alerts, actions, breaches, tracker, events,
        mode, causal_section=causal_section,
    )
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    return OUTPUT_PATH
