"""FastAPI application — investment dashboard v3."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from investment.core.db import connect, transaction

_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Investment Dashboard v3", docs_url="/docs", redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


def _rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]


def _today() -> str:
    return date.today().isoformat()


# ── GET /api/operating-state/today ───────────────────────────────────────────

@app.get("/api/operating-state/today")
def get_operating_state():
    today = _today()
    conn = connect()
    row = conn.execute(
        "SELECT * FROM daily_operating_state WHERE state_date=?", (today,)
    ).fetchone()
    conn.close()

    if not row:
        return {
            "state_date": today,
            "health_light": "unknown",
            "state_label": "尚未运行 inv agent run",
            "executable_count": 0,
            "confirm_count": 0,
            "monitor_count": 0,
            "blocked_count": 0,
            "critical_count": 0,
            "warning_count": 0,
            "top_message": "请执行 inv agent run --mode premarket 初始化今日状态",
            "evidence": {},
        }

    d = _row_to_dict(row)
    try:
        d["evidence"] = json.loads(d.get("evidence_json") or "{}")
    except Exception:
        d["evidence"] = {}

    # Add display fields used by the frontend
    import calendar as _cal
    today_date = date.today()
    weekday_names = ["周一","周二","周三","周四","周五","周六","周日"]
    d["weekday"] = weekday_names[today_date.weekday()]
    d["is_trading_day"] = today_date.weekday() < 5  # simplified; no holiday check
    d["updated_at"] = d.get("updated_at", "")
    return d


# ── GET /api/tasks/{id}/checks ────────────────────────────────────────────────

@app.get("/api/tasks/{task_id}/checks")
def get_task_checks(task_id: int):
    """Run 7 pre-execution checks for a task. Returns check results."""
    conn = connect()
    task = conn.execute(
        "SELECT * FROM task_calendar WHERE id=?", (task_id,)
    ).fetchone()
    if not task:
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")

    code = task["related_code"] or ""
    action_type = task["action_type"] or ""

    # Determine BUY/SELL from action or title
    title_lower = (task["title"] or "").lower()
    is_sell = any(x in title_lower for x in ["卖", "减仓", "止损", "stop_loss", "sell"])
    side = "SELL" if is_sell else "BUY"

    checks = []

    if not code:
        checks.append({
            "name": "标的代码", "status": "skip",
            "message": "该任务无关联标的，跳过执行前校验",
            "auto": True,
        })
        conn.close()
        return {
            "task_id": task_id, "code": code, "side": "BUY",
            "checks": checks, "all_pass": True,
            "fail_count": 0, "warn_count": 0,
        }

    today = _today()

    # 1. 停牌检查
    suspension_row = conn.execute(
        """SELECT alert_type FROM alerts
           WHERE alert_date=? AND instrument_id=(
             SELECT id FROM instruments WHERE code=? LIMIT 1
           ) AND alert_type LIKE '%suspend%'""",
        (today, code),
    ).fetchone()
    checks.append({
        "name": "停牌检查",
        "status": "fail" if suspension_row else "pass",
        "message": "标的当前停牌，不可交易" if suspension_row else "未发现停牌告警",
        "auto": True,
    })

    # 2. 涨跌停检查
    quote_row = conn.execute(
        """SELECT change_pct, close, prev_close, open
           FROM quotes q JOIN instruments i ON i.id=q.instrument_id
           WHERE i.code=? AND q.quote_date=?""",
        (code, today),
    ).fetchone()
    if quote_row and quote_row["change_pct"] is not None:
        pct = abs(float(quote_row["change_pct"]))
        limit = 0.10
        at_limit = pct >= limit * 0.98
        checks.append({
            "name": "涨跌停检查",
            "status": "warn" if at_limit else "pass",
            "message": f"今日涨跌幅 {float(quote_row['change_pct'])*100:+.1f}%，{'接近' if at_limit else '未触'}涨跌停",
            "auto": True,
            "value": round(float(quote_row["change_pct"]) * 100, 2),
        })
    else:
        checks.append({
            "name": "涨跌停检查",
            "status": "skip",
            "message": "今日报价暂无，请先执行 inv snapshot pull",
            "auto": False,
        })

    # 3. 未消化公告
    checks.append({
        "name": "未消化公告",
        "status": "skip",
        "message": "无公告数据库，需人工确认近期是否有重大公告（分红/增发/退市警示）",
        "auto": False,
    })

    # 4. 开盘价偏差
    if quote_row and quote_row["open"] and quote_row["prev_close"]:
        dev = (float(quote_row["open"]) - float(quote_row["prev_close"])) / float(quote_row["prev_close"])
        warn = abs(dev) > 0.03
        checks.append({
            "name": "开盘偏差 ±3%",
            "status": "warn" if warn else "pass",
            "message": f"开盘相对昨收偏差 {dev*100:+.2f}%，{'超过 3%，注意滑点' if warn else '在正常范围'}",
            "auto": True,
            "value": round(dev * 100, 2),
        })
    else:
        checks.append({
            "name": "开盘偏差 ±3%",
            "status": "skip",
            "message": "缺少今日开盘价或昨收价",
            "auto": False,
        })

    # 5. 持仓充足（卖出时）
    if side == "SELL":
        holding_row = conn.execute(
            """SELECT h.shares FROM holdings h
               JOIN instruments i ON i.id=h.instrument_id
               WHERE i.code=? AND h.shares > 0
               ORDER BY h.effective_date DESC LIMIT 1""",
            (code,),
        ).fetchone()
        if holding_row and float(holding_row["shares"]) > 0:
            checks.append({
                "name": "持仓充足",
                "status": "pass",
                "message": f"当前持仓 {float(holding_row['shares']):.0f} 股，可卖出",
                "auto": True,
                "value": float(holding_row["shares"]),
            })
        else:
            checks.append({
                "name": "持仓充足",
                "status": "fail",
                "message": "持仓为零或无持仓记录，无法卖出",
                "auto": True,
            })
    else:
        checks.append({
            "name": "持仓充足",
            "status": "skip",
            "message": "买入操作，跳过持仓充足检查",
            "auto": True,
        })

    # 6. 最小交易单位（A股 100 股）
    inst_row = conn.execute(
        "SELECT market, price_tick FROM instruments WHERE code=? LIMIT 1", (code,)
    ).fetchone()
    lot_size = 100 if (inst_row and inst_row["market"] == "A") else 1
    checks.append({
        "name": "最小交易单位",
        "status": "pass",
        "message": f"{'A股最小交易单位 100 股' if lot_size == 100 else '港/美股无批量限制'}",
        "auto": True,
        "value": lot_size,
    })

    # 7. 冷静期
    cooling_row = conn.execute(
        """SELECT d.cooling_until, d.decision_no
           FROM decisions d JOIN instruments i ON i.id=d.primary_instrument_id
           WHERE i.code=? AND d.status='active' AND d.cooling_until IS NOT NULL
           ORDER BY d.cooling_until DESC LIMIT 1""",
        (code,),
    ).fetchone()
    if cooling_row:
        cooling = cooling_row["cooling_until"]
        still_cooling = cooling > today
        checks.append({
            "name": "冷静期",
            "status": "fail" if still_cooling else "pass",
            "message": f"冷静期{'尚未到期（到 ' + cooling + '）' if still_cooling else '已到期（' + cooling + '）'}，决策编号 {cooling_row['decision_no']}",
            "auto": True,
            "cooling_until": cooling,
        })
    else:
        checks.append({
            "name": "冷静期",
            "status": "pass",
            "message": "无活跃决策的冷静期约束",
            "auto": True,
        })

    conn.close()

    all_pass = all(c["status"] in ("pass", "skip") for c in checks)
    fail_count = sum(1 for c in checks if c["status"] == "fail")
    warn_count = sum(1 for c in checks if c["status"] == "warn")

    return {
        "task_id": task_id,
        "code": code,
        "side": side,
        "checks": checks,
        "all_pass": all_pass,
        "fail_count": fail_count,
        "warn_count": warn_count,
    }


# ── GET /api/tasks ────────────────────────────────────────────────────────────

@app.get("/api/tasks")
def get_tasks(
    layer: str = Query("", description="executable|confirm|monitor|blocked|info"),
    status: str = Query("", description="pending|done|skipped|overdue"),
    days: int = Query(7, description="Due within N days"),
):
    today = _today()
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    conn = connect()

    clauses = ["due_date <= ?"]
    params: list = [cutoff]

    if layer:
        clauses.append("decision_layer=?")
        params.append(layer)
    if status:
        clauses.append("status=?")
        params.append(status)
    else:
        clauses.append("status NOT IN ('done','skipped')")

    where = " AND ".join(clauses)
    rows = conn.execute(
        f"""SELECT id, title, status, priority, decision_layer, category,
                   related_code, due_date, suggested_command, source_module,
                   source_ref, action_type, evidence_json, blocking_reason,
                   confidence, notes
            FROM task_calendar
            WHERE {where}
            ORDER BY
              CASE decision_layer
                WHEN 'executable' THEN 0 WHEN 'confirm' THEN 1
                WHEN 'monitor'    THEN 2 WHEN 'blocked' THEN 3
                ELSE 4 END,
              CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
              due_date""",
        params,
    ).fetchall()
    conn.close()

    result = []
    for r in rows:
        d = dict(r)
        try:
            d["evidence"] = json.loads(d.get("evidence_json") or "{}")
        except Exception:
            d["evidence"] = {}
        result.append(d)
    return result


# ── GET /api/portfolio/health ─────────────────────────────────────────────────

@app.get("/api/portfolio/health")
def get_portfolio_health():
    conn = connect()

    # Latest calc_date from position_health
    meta = conn.execute(
        "SELECT MAX(calc_date) AS latest FROM position_health"
    ).fetchone()
    latest = meta["latest"] if meta else None

    if not latest:
        # Fall back to v_portfolio_snapshot only
        rows = conn.execute(
            "SELECT v.*, NULL as health_score, 'unknown' as health_label, "
            "NULL as suggested_action "
            "FROM v_portfolio_snapshot v ORDER BY market_value DESC"
        ).fetchall()
        conn.close()
        return {
            "calc_date": _today(), "holdings": _rows_to_list(rows),
            "tranches": [], "abc_total": 0, "source": "snapshot_only",
        }

    rows = conn.execute(
        """SELECT ph.*, i.code, i.name, i.tranche, i.industry, i.theme,
                  v.market_value, v.cost_total, v.pnl_pct, v.price, v.shares
           FROM position_health ph
           JOIN instruments i ON i.id = ph.instrument_id
           LEFT JOIN v_portfolio_snapshot v ON v.id = ph.instrument_id
           WHERE ph.calc_date = ?
           ORDER BY
             CASE ph.health_label
               WHEN 'act'    THEN 0 WHEN 'review'   THEN 1
               WHEN 'watch'  THEN 2 WHEN 'healthy'  THEN 3
               ELSE 4 END,
             ph.health_score ASC""",
        (latest,),
    ).fetchall()

    holdings = []
    for r in rows:
        d = dict(r)
        try:
            d["evidence"] = json.loads(d.get("evidence_json") or "{}")
        except Exception:
            d["evidence"] = {}
        holdings.append(d)

    # Tranche breakdown with denominator labels (must run before conn.close)
    tranche_rows = conn.execute(
        """SELECT i.tranche,
                  SUM(v.market_value) AS total_value,
                  COUNT(*) AS count
           FROM v_portfolio_snapshot v
           JOIN instruments i ON i.id = v.id
           WHERE v.market_value > 0
           GROUP BY i.tranche"""
    ).fetchall()
    abc_total = sum(
        float(r["total_value"] or 0) for r in tranche_rows if r["tranche"] in ("A","B","C")
    )
    tranches = []
    for r in tranche_rows:
        tv = float(r["total_value"] or 0)
        tranches.append({
            "tranche": r["tranche"],
            "total_value": tv,
            "count": r["count"],
            "weight_of_abc": tv / abc_total if abc_total > 0 else None,
            "denominator_label": "占ABC档（不含D档RSU）",
        })

    conn.close()
    return {
        "calc_date": latest,
        "holdings": holdings,
        "tranches": tranches,
        "abc_total": abc_total,
        "source": "position_health",
    }


# ── GET /api/risk/summary ─────────────────────────────────────────────────────

@app.get("/api/risk/summary")
def get_risk_summary():
    conn = connect()

    # Latest risk_metrics
    metrics = conn.execute(
        "SELECT * FROM risk_metrics ORDER BY calc_date DESC, id DESC LIMIT 1"
    ).fetchone()

    # Active rule_breaches
    breaches = conn.execute(
        """SELECT rb.*, COALESCE(i.code,'') AS code, COALESCE(i.name,'') AS name
           FROM rule_breaches rb
           LEFT JOIN instruments i ON i.id = rb.instrument_id
           WHERE rb.status IN ('active','remediating')
           ORDER BY rb.detected_at DESC"""
    ).fetchall()

    # Top risk contributions (latest calc_date)
    rc_meta = conn.execute("SELECT MAX(calc_date) AS d FROM risk_contribution").fetchone()
    rc_date = rc_meta["d"] if rc_meta else None
    contribs = []
    if rc_date:
        contribs = conn.execute(
            """SELECT rc.*, i.code, i.name, i.tranche
               FROM risk_contribution rc
               JOIN instruments i ON i.id = rc.instrument_id
               WHERE rc.calc_date = ?
               ORDER BY rc.risk_contrib_pct DESC""",
            (rc_date,),
        ).fetchall()

    # High correlations (latest)
    corr_meta = conn.execute("SELECT MAX(calc_date) AS d FROM correlation_matrix").fetchone()
    high_corrs = []
    if corr_meta and corr_meta["d"]:
        high_corrs = conn.execute(
            """SELECT cm.corr_value, ia.code AS code_a, ia.name AS name_a,
                      ib.code AS code_b, ib.name AS name_b
               FROM correlation_matrix cm
               JOIN instruments ia ON ia.id = cm.instrument_id_a
               JOIN instruments ib ON ib.id = cm.instrument_id_b
               WHERE cm.calc_date = ? AND cm.corr_value >= 0.7
               ORDER BY cm.corr_value DESC LIMIT 5""",
            (corr_meta["d"],),
        ).fetchall()

    conn.close()

    # Compute pseudo-diversification indicator from position_health records
    pseudo_div = None
    try:
        conn2 = connect()
        top_risk = conn2.execute(
            """SELECT i.code, i.name, i.theme, ph.risk_contrib_pct
               FROM position_health ph JOIN instruments i ON i.id=ph.instrument_id
               WHERE ph.calc_date=(SELECT MAX(calc_date) FROM position_health)
               ORDER BY ph.risk_contrib_pct DESC NULLS LAST LIMIT 1"""
        ).fetchone()
        if top_risk and top_risk["risk_contrib_pct"] and float(top_risk["risk_contrib_pct"]) > 0.40:
            pseudo_div = {
                "detected": True,
                "top_code": top_risk["code"],
                "top_name": top_risk["name"],
                "theme": top_risk["theme"] or "",
                "contrib_pct": float(top_risk["risk_contrib_pct"]),
                "message": f"{top_risk['name']}（{top_risk['code']}）占组合风险 {float(top_risk['risk_contrib_pct'])*100:.0f}%，存在风险集中"
            }
        conn2.close()
    except Exception:
        pass

    return {
        "calc_date": _row_to_dict(metrics).get("calc_date") if metrics else None,
        "metrics": _row_to_dict(metrics),
        "rule_breaches": _rows_to_list(breaches),
        "risk_contributions": _rows_to_list(contribs),
        "high_correlations": _rows_to_list(high_corrs),
        "pseudo_div": pseudo_div,
    }


# ── GET /api/goals/progress ───────────────────────────────────────────────────

@app.get("/api/goals/progress")
def get_goals_progress():
    conn = connect()
    row = conn.execute(
        "SELECT * FROM goal_progress ORDER BY progress_date DESC LIMIT 1"
    ).fetchone()
    # Historical series (last 90 days)
    series = conn.execute(
        """SELECT progress_date, actual_ytd_return, target_ytd_return,
                  benchmark_return_ytd, portfolio_value
           FROM goal_progress
           WHERE progress_date >= date('now', '-90 days')
           ORDER BY progress_date""",
    ).fetchall()
    conn.close()

    # Latest attribution for decomposition display
    attr_row = None
    try:
        conn2 = connect()
        attr_row = conn2.execute(
            "SELECT * FROM performance_attribution ORDER BY period_end DESC LIMIT 1"
        ).fetchone()
        conn2.close()
    except Exception:
        pass

    return {
        "latest": _row_to_dict(row),
        "series": _rows_to_list(series),
        "attribution": _row_to_dict(attr_row) if attr_row else {},
    }


# ── GET /api/research/tasks ───────────────────────────────────────────────────

@app.get("/api/research/tasks")
def get_research_tasks():
    """Research-related tasks: thesis, earnings, IC memo, candidate."""
    today = _today()
    cutoff = (date.today() + timedelta(days=30)).isoformat()
    conn = connect()

    rows = conn.execute(
        """SELECT id, title, status, priority, decision_layer, category,
                  related_code, due_date, suggested_command, source_module,
                  action_type, notes
           FROM task_calendar
           WHERE status NOT IN ('done','skipped')
             AND due_date <= ?
             AND (
               action_type IN ('thesis_stale','earnings','ic_memo','candidate')
               OR category IN ('monthly','quarterly','annual','earnings')
               OR source_module IN ('theses','causal')
             )
           ORDER BY
             CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
             due_date""",
        (cutoff,),
    ).fetchall()
    conn.close()
    return _rows_to_list(rows)


# ── GET /api/data-quality/issues ─────────────────────────────────────────────

@app.get("/api/data-quality/issues")
def get_data_quality():
    today = _today()
    cutoff_stale = (date.today() - timedelta(days=2)).isoformat()
    conn = connect()

    issues = []

    # Stale quotes (no update in last 2 trading days for active B/C holdings)
    stale_rows = conn.execute(
        """SELECT i.code, i.name, i.tranche,
                  MAX(q.quote_date) AS last_quote_date
           FROM instruments i
           LEFT JOIN quotes q ON q.instrument_id = i.id
           WHERE i.tranche IN ('B','C') AND i.active = 1
           GROUP BY i.id
           HAVING last_quote_date IS NULL OR last_quote_date < ?""",
        (cutoff_stale,),
    ).fetchall()
    for r in stale_rows:
        issues.append({
            "type": "stale_quote",
            "severity": "warning",
            "code": r["code"],
            "name": r["name"],
            "message": f"{r['name']}（{r['code']}）报价过期，最后更新：{r['last_quote_date'] or '从未'}",
            "resolution": f"inv snapshot pull",
            "data_quality": "stale",
        })

    # Positions without thesis
    no_thesis = conn.execute(
        """SELECT i.code, i.name
           FROM instruments i
           LEFT JOIN theses t ON t.instrument_id = i.id
           WHERE i.tranche = 'C' AND i.active = 1
             AND t.instrument_id IS NULL""",
    ).fetchall()
    for r in no_thesis:
        issues.append({
            "type": "missing_thesis",
            "severity": "warning",
            "code": r["code"],
            "name": r["name"],
            "message": f"{r['name']}（{r['code']}）缺少投资论点",
            "resolution": f"在 theses/ 目录创建 {r['code']}.md",
            "data_quality": "unverified",
        })

    # Stale thesis (past next_review_date)
    stale_thesis = conn.execute(
        """SELECT i.code, i.name, t.next_review_date, t.current_score
           FROM theses t JOIN instruments i ON i.id = t.instrument_id
           WHERE t.next_review_date IS NOT NULL AND t.next_review_date < ?""",
        (today,),
    ).fetchall()
    for r in stale_thesis:
        issues.append({
            "type": "stale_thesis",
            "severity": "info",
            "code": r["code"],
            "name": r["name"],
            "message": f"{r['name']} 论点逾期（应复查于 {r['next_review_date']}）",
            "resolution": f"inv thesis score {r['code']} --score N",
            "data_quality": "stale",
        })

    # Holdings with no recent price data (mock-detected: cost_price == current price)
    mock_prices = conn.execute(
        """SELECT i.code, i.name, h.cost_price, v.price
           FROM holdings h
           JOIN instruments i ON i.id = h.instrument_id
           LEFT JOIN v_portfolio_snapshot v ON v.id = h.instrument_id
           WHERE i.tranche IN ('B','C') AND i.active = 1
             AND h.effective_date = (
               SELECT MAX(effective_date) FROM holdings h2
               WHERE h2.instrument_id = h.instrument_id
             )
             AND (v.price IS NULL OR v.price = h.cost_price)""",
    ).fetchall()
    for r in mock_prices:
        issues.append({
            "type": "mock_price",
            "severity": "info",
            "code": r["code"],
            "name": r["name"],
            "message": f"{r['name']} 使用成本价作为当前价格（可能是 mock 数据）",
            "resolution": "inv snapshot pull",
            "data_quality": "mock",
        })

    conn.close()

    # Severity sort: warning first
    issues.sort(key=lambda x: 0 if x["severity"] == "warning" else 1)
    return {"issues": issues, "total": len(issues), "checked_at": today}


# ── POST /api/data-quality/suppress ──────────────────────────────────────────

class DQSuppressBody(BaseModel):
    issue_type: str        # mock_price | stale_quote | missing_thesis | stale_thesis
    code: str = ""
    reason: str = ""


@app.post("/api/data-quality/suppress")
def suppress_data_quality_issue(body: DQSuppressBody):
    """Record that a data quality issue has been acknowledged for today.

    Restores blocked tasks for the affected code to their original layer.
    Stores a 'done' task in task_calendar as the suppression record.
    """
    today = _today()
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    source_ref = f"dq_suppress_{body.issue_type}_{body.code}_{today}"

    with transaction() as conn:
        # Create suppression record
        tid = conn.execute(
            """INSERT OR IGNORE INTO task_calendar
               (title, category, due_date, priority, status,
                related_code, source_module, source_ref, action_type,
                decision_layer, notes, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"[DQ已确认] {body.issue_type} {body.code}",
             "custom", today, "low", "done",
             body.code or None, "data_quality", source_ref, "dq_suppress",
             "info", body.reason or None, now, now),
        ).lastrowid

        # Restore blocked tasks for this code (if suppressed)
        if body.code:
            conn.execute(
                """UPDATE task_calendar SET
                   decision_layer='confirm',
                   blocking_reason=NULL,
                   confidence=0.6,
                   updated_at=?
                   WHERE related_code=? AND decision_layer='blocked'
                     AND status='pending' AND due_date=?""",
                (now, body.code, today),
            )

    return {"ok": True, "suppression_id": tid, "code": body.code, "issue_type": body.issue_type}


# ── POST /api/tasks/{id}/complete|snooze|skip ─────────────────────────────────

class TaskActionBody(BaseModel):
    notes: str = ""
    snooze_days: int = 1  # for snooze only


@app.post("/api/tasks/{task_id}/complete")
def complete_task(task_id: int, body: TaskActionBody = TaskActionBody()):
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with transaction() as conn:
        row = conn.execute("SELECT id FROM task_calendar WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        conn.execute(
            "UPDATE task_calendar SET status='done', updated_at=? WHERE id=?", (now, task_id)
        )
        conn.execute(
            "INSERT INTO task_log (task_id, action, notes, logged_at) VALUES (?,?,?,?)",
            (task_id, "completed", body.notes or None, now),
        )
    return {"ok": True, "task_id": task_id, "status": "done"}


@app.post("/api/tasks/{task_id}/snooze")
def snooze_task(task_id: int, body: TaskActionBody = TaskActionBody()):
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    new_due = (date.today() + timedelta(days=max(1, body.snooze_days))).isoformat()
    with transaction() as conn:
        row = conn.execute("SELECT id FROM task_calendar WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        conn.execute(
            "UPDATE task_calendar SET due_date=?, updated_at=? WHERE id=?",
            (new_due, now, task_id),
        )
        conn.execute(
            "INSERT INTO task_log (task_id, action, notes, logged_at) VALUES (?,?,?,?)",
            (task_id, "snoozed", f"延期至 {new_due}; {body.notes or ''}".strip("; "), now),
        )
    return {"ok": True, "task_id": task_id, "new_due": new_due}


@app.post("/api/tasks/{task_id}/skip")
def skip_task(task_id: int, body: TaskActionBody = TaskActionBody()):
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with transaction() as conn:
        row = conn.execute("SELECT id FROM task_calendar WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        conn.execute(
            "UPDATE task_calendar SET status='skipped', updated_at=? WHERE id=?", (now, task_id)
        )
        conn.execute(
            "INSERT INTO task_log (task_id, action, notes, logged_at) VALUES (?,?,?,?)",
            (task_id, "skipped", body.notes or None, now),
        )
    return {"ok": True, "task_id": task_id, "status": "skipped"}


# ── Static files + SPA fallback ───────────────────────────────────────────────

if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


@app.get("/")
def serve_index():
    index = _STATIC / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse({"status": "API running", "docs": "/docs"})
