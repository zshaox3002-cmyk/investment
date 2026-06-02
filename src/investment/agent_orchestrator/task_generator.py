"""Task generator — maps orchestrator module outputs to task_calendar rows.

Each mapper follows the same contract:
  - Reads structured data from the relevant module result
  - Calls _upsert_task() for each task it wants to create
  - _upsert_task() checks dedup key before inserting (idempotent)
  - Returns list of newly inserted task IDs

Source → task mapping:
  position_monitor → rebalance / position correction / rule breach remediation
  risk_engine      → pseudo-diversification / high correlation / risk concentration
  exec_monitor     → stop-loss / take-profit triggered
  trade.decisions  → cooldown expired / trade confirmation needed
  theses           → stale thesis review / drawdown review
  calendar         → monthly / quarterly routines (already in task_calendar, re-layer them)
  causal           → actionable external signal
  attribution      → underperformance review

Dedup key: (source_module, source_ref, due_date) — same-day, same-origin = skip.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Optional

from investment.core.db import connect, transaction
from investment.agent_orchestrator.prioritizer import task_exists


# ── Low-level insert ──────────────────────────────────────────────────────────

def _upsert_task(
    title: str,
    category: str,
    due_date: str,
    priority: str,
    decision_layer: str,
    source_module: str,
    source_ref: str,
    action_type: str = "",
    evidence: dict | None = None,
    blocking_reason: str = "",
    suggested_command: str = "",
    related_code: str = "",
    notes: str = "",
    confidence: float = 1.0,
    db_path=None,
) -> Optional[int]:
    """Insert task if dedup key not present. Returns new task ID or None."""
    if task_exists(source_module, source_ref, due_date, db_path=db_path):
        return None

    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    evidence_json = json.dumps(evidence or {}, ensure_ascii=False)

    with transaction(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO task_calendar
               (title, category, due_date, priority, status,
                related_code, notes, created_at, updated_at,
                source_module, source_ref, action_type, decision_layer,
                evidence_json, blocking_reason, suggested_command, confidence)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (title, category, due_date, priority, "pending",
             related_code or None, notes or None, now, now,
             source_module, source_ref, action_type, decision_layer,
             evidence_json, blocking_reason or None, suggested_command or None,
             confidence),
        )
        task_id = cur.lastrowid
        conn.execute(
            "INSERT INTO task_log (task_id, action, logged_at) VALUES (?,?,?)",
            (task_id, "created", now),
        )
    return task_id


# ── Source mappers ────────────────────────────────────────────────────────────

def _from_position_monitor(pos_report: Any, today: str, db_path) -> list[int]:
    """position_monitor → rebalance / rule breach remediation tasks."""
    ids: list[int] = []
    if pos_report is None:
        return ids

    # Rebalance task
    if getattr(pos_report, "rebalance_needed", False):
        tid = _upsert_task(
            title="再平衡：组合偏离超过目标配置 5%",
            category="rebalance",
            due_date=today,
            priority="high",
            decision_layer="confirm",
            source_module="position_monitor",
            source_ref="rebalance",
            action_type="rebalance",
            evidence={"rebalance_needed": True},
            suggested_command="inv risk compute && inv dashboard render",
            db_path=db_path,
        )
        if tid:
            ids.append(tid)

    # Rule breach tasks
    for breach in getattr(pos_report, "rule_breaches", []) or []:
        rule = getattr(breach, "rule_name", "") or str(breach)
        action = getattr(breach, "action_required", "") or ""
        ref = f"breach_{getattr(breach, 'rule_name', str(breach))[:40]}"
        # Extract potential code from the breach for suggested command
        code_hint = ""
        for h in getattr(pos_report, "holdings", []) or []:
            pnl = getattr(h, "pnl_pct", 0)
            if pnl < -0.15:
                code_hint = getattr(h, "code", "")
                break

        tid = _upsert_task(
            title=f"违规处理：{rule}",
            category="custom",
            due_date=today,
            priority="high",
            decision_layer="executable",
            source_module="position_monitor",
            source_ref=ref,
            action_type="rule_breach",
            evidence={"rule": rule, "action": action},
            suggested_command=(
                f"inv trade decision {code_hint} --type REDUCE"
                if code_hint else "inv risk compute"
            ),
            db_path=db_path,
        )
        if tid:
            ids.append(tid)

    # Per-holding drawdown review tasks (≥ 15%)
    for h in getattr(pos_report, "holdings", []) or []:
        if getattr(h, "pnl_pct", 0) <= -0.15:
            code = getattr(h, "code", "")
            ref = f"drawdown_review_{code}"
            tid = _upsert_task(
                title=f"回撤强制审查：{getattr(h,'name',code)}（{code}）下跌超 15%",
                category="custom",
                due_date=today,
                priority="high",
                decision_layer="confirm",
                source_module="position_monitor",
                source_ref=ref,
                action_type="drawdown_review",
                related_code=code,
                evidence={"pnl_pct": getattr(h, "pnl_pct", 0), "code": code},
                suggested_command=f"inv thesis score {code} --score 1 --dimension overall",
                db_path=db_path,
            )
            if tid:
                ids.append(tid)

    return ids


def _from_risk_engine(risk_report: Any, today: str, db_path) -> list[int]:
    """risk_engine → pseudo-div / high-correlation tasks."""
    ids: list[int] = []
    if risk_report is None:
        return ids

    # Pseudo-diversification
    pd = getattr(risk_report, "pseudo_div", None)
    if pd and getattr(pd, "detected", False):
        top_code = getattr(pd, "top_contributor_code", "")
        tid = _upsert_task(
            title=f"伪分散风险：{getattr(pd,'concentrated_theme','') or top_code} 风险集中",
            category="custom",
            due_date=today,
            priority="medium",
            decision_layer="confirm",
            source_module="risk_engine",
            source_ref="pseudo_div",
            action_type="pseudo_diversification",
            related_code=top_code,
            evidence={
                "theme": getattr(pd, "concentrated_theme", ""),
                "top_code": top_code,
                "pct": getattr(pd, "top_contributor_pct", 0),
            },
            suggested_command=(
                f"inv trade decision {top_code} --type REDUCE"
                if top_code else "inv risk compute"
            ),
            db_path=db_path,
        )
        if tid:
            ids.append(tid)

    # High correlation pairs
    for hc in (getattr(risk_report, "high_correlations", []) or [])[:3]:
        code_a = hc.get("code_a", "")
        code_b = hc.get("code_b", "")
        corr   = hc.get("corr", 0)
        ref    = f"high_corr_{code_a}_{code_b}"
        tid = _upsert_task(
            title=f"高相关持仓：{hc.get('name_a',code_a)} × {hc.get('name_b',code_b)}（{corr:.2f}）",
            category="custom",
            due_date=today,
            priority="low",
            decision_layer="monitor",
            source_module="risk_engine",
            source_ref=ref,
            action_type="high_correlation",
            evidence={"code_a": code_a, "code_b": code_b, "corr": corr},
            suggested_command=f"inv risk compute",
            db_path=db_path,
        )
        if tid:
            ids.append(tid)

    return ids


def _from_exec_monitor(exec_data: Any, today: str, db_path) -> list[int]:
    """exec_monitor ToolResult → stop-loss/take-profit triggered tasks."""
    ids: list[int] = []
    if exec_data is None:
        return ids

    data = getattr(exec_data, "data", {}) or {}
    triggered = data.get("triggered_rules", [])
    for rule_desc in triggered:
        ref = f"stop_trigger_{str(rule_desc)[:40]}"
        # Try to extract code from rule description
        code = ""
        if isinstance(rule_desc, dict):
            code = str(rule_desc.get("code", ""))
            label = rule_desc.get("type", "止损/止盈")
        else:
            label = str(rule_desc)

        tid = _upsert_task(
            title=f"止损/止盈规则触发：{label}",
            category="custom",
            due_date=today,
            priority="high",
            decision_layer="executable",
            source_module="exec_monitor",
            source_ref=ref,
            action_type="stop_trigger",
            related_code=code,
            evidence={"rule": rule_desc},
            suggested_command=(
                f"inv trade log {code} --side SELL" if code else "inv exec monitor"
            ),
            db_path=db_path,
        )
        if tid:
            ids.append(tid)

    return ids


def _from_trade_decisions(today: str, db_path) -> list[int]:
    """decisions table → cooldown-expired / pending-confirmation tasks."""
    ids: list[int] = []
    conn = connect(db_path)
    rows = conn.execute(
        """SELECT d.id, d.decision_no, d.decision_type, d.cooling_until,
                  COALESCE(i.code,'') AS code, COALESCE(i.name,'') AS name
           FROM decisions d
           LEFT JOIN instruments i ON i.id = d.primary_instrument_id
           WHERE d.status = 'active'""",
    ).fetchall()
    conn.close()

    for r in rows:
        code = r["code"]
        dec_no = r["decision_no"]
        cooling = r["cooling_until"]

        if cooling and cooling <= today:
            # Cooldown has expired — actionable
            ref = f"cooldown_expired_{dec_no}"
            tid = _upsert_task(
                title=f"冷静期到期：{r['name'] or code}（{dec_no}）可执行",
                category="cooldown",
                due_date=today,
                priority="high",
                decision_layer="executable",
                source_module="trade_decisions",
                source_ref=ref,
                action_type="cooldown_expired",
                related_code=code,
                evidence={"decision_no": dec_no, "cooling_until": cooling},
                suggested_command=(
                    f"inv trade log {code} --side BUY -d {dec_no}"
                ),
                db_path=db_path,
            )
            if tid:
                ids.append(tid)
        elif cooling and cooling > today:
            # Still cooling — monitor
            ref = f"cooldown_pending_{dec_no}"
            tid = _upsert_task(
                title=f"冷静期进行中：{r['name'] or code}（到期 {cooling}）",
                category="cooldown",
                due_date=cooling,
                priority="medium",
                decision_layer="monitor",
                source_module="trade_decisions",
                source_ref=ref,
                action_type="cooldown_pending",
                related_code=code,
                evidence={"decision_no": dec_no, "cooling_until": cooling},
                blocking_reason=f"冷静期至 {cooling}",
                db_path=db_path,
            )
            if tid:
                ids.append(tid)

    return ids


def _from_theses(today: str, db_path) -> list[int]:
    """theses table → stale review / drawdown review tasks."""
    from datetime import timedelta
    ids: list[int] = []
    conn = connect(db_path)

    # Theses past next_review_date
    stale_rows = conn.execute(
        """SELECT t.instrument_id, t.next_review_date,
                  COALESCE(i.code,'') AS code, COALESCE(i.name,'') AS name,
                  t.current_score
           FROM theses t
           JOIN instruments i ON i.id = t.instrument_id
           WHERE t.next_review_date IS NOT NULL
             AND t.next_review_date <= ?""",
        (today,),
    ).fetchall()
    conn.close()

    for r in stale_rows:
        code = r["code"]
        ref  = f"thesis_stale_{code}"
        tid = _upsert_task(
            title=f"论点过期需更新：{r['name'] or code}（上次评分 {r['current_score'] or 'N/A'}）",
            category="monthly",
            due_date=today,
            priority="medium",
            decision_layer="confirm",
            source_module="theses",
            source_ref=ref,
            action_type="thesis_stale",
            related_code=code,
            evidence={"next_review_date": r["next_review_date"], "score": r["current_score"]},
            suggested_command=f"inv thesis score {code} --score N",
            db_path=db_path,
        )
        if tid:
            ids.append(tid)

    return ids


def _from_calendar(calendar_report: Any, today: str, db_path) -> list[int]:
    """CalendarReport → re-layer overdue tasks as confirm, add decision_layer."""
    ids: list[int] = []
    if calendar_report is None:
        return ids

    # Re-layer overdue items to confirm (they may lack decision_layer)
    for task in getattr(calendar_report, "overdue", []) or []:
        task_id = getattr(task, "task_id", None)
        if task_id is None:
            continue
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        try:
            with transaction(db_path) as conn:
                conn.execute(
                    """UPDATE task_calendar SET
                       decision_layer='confirm',
                       source_module=COALESCE(NULLIF(source_module,''), 'calendar'),
                       source_ref=COALESCE(NULLIF(source_ref,''), ?),
                       updated_at=?
                       WHERE id=? AND (decision_layer IS NULL OR decision_layer='monitor')""",
                    (f"calendar_overdue_{task_id}", now, task_id),
                )
        except Exception:
            pass

    return ids


def _from_causal(causal_result: Any, today: str, db_path) -> list[int]:
    """CausalInsightReport → actionable (A/B) signals as confirm tasks."""
    ids: list[int] = []
    if causal_result is None:
        return ids

    for ins in getattr(causal_result, "actionable", []) or []:
        code      = getattr(ins, "holding_code", "")
        tier      = getattr(ins, "credibility_tier", "B")
        direction = getattr(ins, "direction_label", "")
        narrative = (getattr(ins, "narrative", "") or "")[:80]
        ref = f"causal_{code}_{getattr(ins,'assessment_id',0)}"

        tid = _upsert_task(
            title=f"外部信号 [{tier}级] {code} {direction}：{narrative or '详见因果图'}",
            category="custom",
            due_date=today,
            priority="medium" if tier == "B" else "high",
            decision_layer="confirm" if tier == "A" else "monitor",
            source_module="causal",
            source_ref=ref,
            action_type="causal_signal",
            related_code=code,
            evidence={
                "tier": tier, "direction": direction,
                "narrative": narrative,
                "assessment_id": getattr(ins, "assessment_id", 0),
            },
            suggested_command=f"inv causal assess --code {code} --explain",
            confidence=0.9 if tier == "A" else 0.6,
            db_path=db_path,
        )
        if tid:
            ids.append(tid)

    return ids


def _from_attribution(attr_result: Any, today: str, db_path) -> list[int]:
    """AttributionResult → underperformance review task."""
    ids: list[int] = []
    if attr_result is None:
        return ids

    excess = getattr(attr_result, "excess_return", 0.0) or 0.0
    insuf  = getattr(attr_result, "insufficient_data", True)

    # Only flag meaningful underperformance and sufficient data
    if not insuf and excess < -0.02:
        ref = f"attribution_underperf_{today}"
        tid = _upsert_task(
            title=f"业绩归因：跑输基准 {abs(excess)*100:.1f}%，建议复盘",
            category="quarterly",
            due_date=today,
            priority="low",
            decision_layer="monitor",
            source_module="attribution",
            source_ref=ref,
            action_type="underperformance",
            evidence={
                "excess_return": excess,
                "total_return": getattr(attr_result, "total_return", 0),
                "benchmark_return": getattr(attr_result, "benchmark_return", 0),
            },
            suggested_command="inv attribution run",
            db_path=db_path,
        )
        if tid:
            ids.append(tid)

    return ids


# ── Main entry point ──────────────────────────────────────────────────────────

def apply_data_quality_guard(db_path=None) -> int:
    """Downgrade tasks whose related_code has mock/stale data to 'blocked'.

    Rules:
    - mock_price (price == cost_price with no real quote): confidence < 0.8 → blocked
    - stale_quote (no quote in last 2 days): → blocked

    Returns number of tasks downgraded.
    """
    from datetime import timedelta
    cutoff_stale = (date.today() - timedelta(days=2)).isoformat()
    today = date.today().isoformat()
    conn = connect(db_path)

    # Find codes with stale or mock prices
    stale_codes: set[str] = set()

    # Stale: active B/C instruments with no recent quote
    stale_rows = conn.execute(
        """SELECT i.code FROM instruments i
           LEFT JOIN quotes q ON q.instrument_id = i.id
             AND q.quote_date >= ?
           WHERE i.tranche IN ('B','C') AND i.active = 1
           GROUP BY i.id
           HAVING COUNT(q.instrument_id) = 0""",
        (cutoff_stale,),
    ).fetchall()
    for r in stale_rows:
        stale_codes.add(r["code"])

    # Mock: price == cost_price (no real quote, using fallback)
    mock_rows = conn.execute(
        """SELECT i.code FROM instruments i
           JOIN holdings h ON h.instrument_id = i.id
           LEFT JOIN v_portfolio_snapshot v ON v.id = i.id
           WHERE i.tranche IN ('B','C') AND i.active = 1
             AND h.effective_date = (
               SELECT MAX(h2.effective_date) FROM holdings h2
               WHERE h2.instrument_id = h.instrument_id
             )
             AND (v.price IS NULL OR v.price = h.cost_price)""",
    ).fetchall()
    for r in mock_rows:
        stale_codes.add(r["code"])

    if not stale_codes:
        conn.close()
        return 0

    # Find executable/confirm tasks for these codes that aren't already blocked
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    downgraded = 0
    for code in stale_codes:
        rows = conn.execute(
            """SELECT id FROM task_calendar
               WHERE related_code=? AND decision_layer IN ('executable','confirm')
                 AND status='pending' AND due_date >= ?""",
            (code, today),
        ).fetchall()
        for r in rows:
            conn.execute(
                """UPDATE task_calendar SET
                   decision_layer='blocked',
                   blocking_reason=?,
                   confidence=0.3,
                   updated_at=?
                   WHERE id=?""",
                (f"数据可信度不足：{code} 报价过期或使用成本价（mock 数据），不得进入执行判断", now, r["id"]),
            )
            downgraded += 1

    conn.commit()
    conn.close()
    return downgraded


def generate_tasks(
    orchestrator_result: Any,
    db_path=None,
) -> list[int]:
    """Map all module outputs to task_calendar rows. Returns new task IDs."""
    today = date.today().isoformat()
    new_ids: list[int] = []

    pos_report  = orchestrator_result.position_report
    risk_report = orchestrator_result.risk_report
    exec_data   = (orchestrator_result.exec_monitor.data
                   if orchestrator_result.exec_monitor.ok else None)
    cal_report  = orchestrator_result.calendar_report
    causal_res  = orchestrator_result.causal_result
    attr_result = orchestrator_result.attribution_result

    new_ids += _from_position_monitor(pos_report, today, db_path)
    new_ids += _from_risk_engine(risk_report, today, db_path)
    new_ids += _from_exec_monitor(exec_data, today, db_path)
    new_ids += _from_trade_decisions(today, db_path)
    new_ids += _from_theses(today, db_path)
    new_ids += _from_calendar(cal_report, today, db_path)
    new_ids += _from_causal(causal_res, today, db_path)
    new_ids += _from_attribution(attr_result, today, db_path)

    # Data quality guard: downgrade tasks with bad data to blocked
    apply_data_quality_guard(db_path)

    return new_ids
