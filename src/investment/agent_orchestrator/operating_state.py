"""Operating state — compute health light and write to daily_operating_state.

Health light rules (first match wins — most severe takes precedence):

RED:
  - any critical alert exists for today
  - any rule_breach with grace_until <= today+3 days (or no grace_until)
  - any stop_rule triggered today (exec_monitor found triggers)

YELLOW:
  - any warning alert
  - any active rule_breach (regardless of grace period)
  - any overdue task_calendar item
  - cooldown period expires today or has expired
  - pseudo-diversification detected
  - any high-correlation pair > 0.7
  - tranche rebalance deviation > 5%

GREEN:
  - none of the above
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal, Optional

from investment.core.db import connect, transaction


HealthLight = Literal["green", "yellow", "red"]


@dataclass
class OperatingState:
    state_date: str
    health_light: HealthLight
    state_label: str

    executable_count: int = 0
    confirm_count: int = 0
    monitor_count: int = 0
    blocked_count: int = 0
    critical_count: int = 0
    warning_count: int = 0

    top_message: str = ""
    evidence_json: str = "{}"


# ── Evidence collectors ───────────────────────────────────────────────────────

def _check_alerts(conn, today: str) -> tuple[int, int, list[str]]:
    """Return (critical_count, warning_count, messages)."""
    rows = conn.execute(
        "SELECT severity, message FROM alerts WHERE alert_date=? AND (acknowledged IS NULL OR acknowledged=0)",
        (today,),
    ).fetchall()
    critical = [r["message"] for r in rows if r["severity"] == "critical"]
    warning  = [r["message"] for r in rows if r["severity"] == "warning"]
    return len(critical), len(warning), critical + warning


def _check_rule_breaches(conn, today: str) -> tuple[bool, bool, list[str]]:
    """Return (has_urgent, has_any, messages).

    urgent = no grace_period_expires OR grace_period_expires <= today+3.
    """
    rows = conn.execute(
        "SELECT rule_path, grace_period_expires FROM rule_breaches WHERE status IN ('active','remediating')",
    ).fetchall()
    if not rows:
        return False, False, []

    deadline = (date.fromisoformat(today) + timedelta(days=3)).isoformat()
    urgent_msgs: list[str] = []
    any_msgs: list[str] = []
    for r in rows:
        msg = f"规则违规：{r['rule_path']}"
        any_msgs.append(msg)
        grace = r["grace_period_expires"]
        if grace is None or grace <= deadline:
            urgent_msgs.append(msg)

    return bool(urgent_msgs), True, urgent_msgs or any_msgs


def _check_stop_triggers(exec_monitor_data: Any) -> tuple[bool, list[str]]:
    """Return (triggered, messages) from exec_monitor ToolResult."""
    if exec_monitor_data is None:
        return False, []
    # ToolResult.data dict may contain 'triggered_rules'
    data = getattr(exec_monitor_data, "data", {}) or {}
    triggered = data.get("triggered_rules", [])
    if triggered:
        msgs = [f"止损/止盈触发：{t}" for t in triggered]
        return True, msgs
    # Also check human_message for trigger keywords
    human = getattr(exec_monitor_data, "human_message", "") or ""
    if "触发" in human or "triggered" in human.lower():
        return True, ["止损/止盈规则已触发，请立即处理"]
    return False, []


def _check_rebalance(position_report: Any) -> tuple[bool, list[str]]:
    """Return (needs_rebalance, messages) from PositionReport."""
    if position_report is None:
        return False, []
    if getattr(position_report, "rebalance_needed", False):
        tranches = getattr(position_report, "tranches", [])
        msgs = []
        for t in tranches:
            dev_text = getattr(t, "deviation_text", "")
            if dev_text and "偏离" in dev_text:
                msgs.append(f"{t.tranche}档再平衡：{dev_text}")
        return True, msgs or ["组合需要再平衡"]
    return False, []


def _check_pseudo_div(risk_report: Any) -> tuple[bool, list[str]]:
    """Return (detected, messages) from RiskReport."""
    if risk_report is None:
        return False, []
    pd = getattr(risk_report, "pseudo_div", None)
    if pd and getattr(pd, "detected", False):
        return True, [f"伪分散风险：{pd.description[:80]}"]
    return False, []


def _check_high_corr(risk_report: Any) -> tuple[bool, list[str]]:
    """Return (has_high_corr, messages)."""
    if risk_report is None:
        return False, []
    hc = getattr(risk_report, "high_correlations", []) or []
    if hc:
        msgs = [f"高相关持仓：{h.get('code_a','')} × {h.get('code_b','')} ({h.get('corr',0):.2f})"
                for h in hc[:3]]
        return True, msgs
    return False, []


def _check_overdue_tasks(conn) -> tuple[bool, list[str]]:
    """Return (has_overdue, messages)."""
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT title FROM task_calendar WHERE status='overdue' OR (status='pending' AND due_date < ?)",
        (today,),
    ).fetchall()
    if rows:
        msgs = [f"逾期任务：{r['title']}" for r in rows[:5]]
        return True, msgs
    return False, []


def _check_cooldown_expiry(conn, today: str) -> tuple[bool, list[str]]:
    """Return (has_expiry, messages) — cooldown tasks due today or overdue."""
    rows = conn.execute(
        "SELECT title, related_code FROM task_calendar "
        "WHERE category='cooldown' AND status NOT IN ('done','skipped') AND due_date <= ?",
        (today,),
    ).fetchall()
    if rows:
        msgs = [f"冷静期到期：{r['related_code'] or r['title']}" for r in rows]
        return True, msgs
    return False, []


# ── Main computation ──────────────────────────────────────────────────────────

def compute_operating_state(
    orchestrator_result: Any,
    db_path=None,
) -> OperatingState:
    """Derive health light from orchestrator results + DB state."""
    today = date.today().isoformat()
    conn = connect(db_path)

    exec_data   = orchestrator_result.exec_monitor.data if orchestrator_result.exec_monitor.ok else None
    pos_report  = orchestrator_result.position.data     if orchestrator_result.position.ok     else None
    risk_report = orchestrator_result.risk.data         if orchestrator_result.risk.ok         else None

    # Collect evidence
    critical_count, warning_count, alert_msgs = _check_alerts(conn, today)
    breach_urgent, breach_any, breach_msgs    = _check_rule_breaches(conn, today)
    stop_triggered, stop_msgs                 = _check_stop_triggers(exec_data)
    rebalance_needed, rebalance_msgs          = _check_rebalance(pos_report)
    pseudo_div, pseudo_msgs                   = _check_pseudo_div(risk_report)
    high_corr, corr_msgs                      = _check_high_corr(risk_report)
    overdue, overdue_msgs                     = _check_overdue_tasks(conn)
    cooldown, cooldown_msgs                   = _check_cooldown_expiry(conn, today)
    conn.close()

    all_evidence: list[str] = []

    # ── RED conditions ────────────────────────────────────────────────────────
    red_triggers: list[str] = []
    if critical_count > 0:
        red_triggers.extend(alert_msgs[:3])
    if breach_urgent:
        red_triggers.extend(breach_msgs[:3])
    if stop_triggered:
        red_triggers.extend(stop_msgs)

    # ── YELLOW conditions ─────────────────────────────────────────────────────
    yellow_triggers: list[str] = []
    if warning_count > 0:
        yellow_triggers.extend([m for m in alert_msgs if "warning" not in m][:3])
    if breach_any and not breach_urgent:
        yellow_triggers.extend(breach_msgs[:2])
    if overdue:
        yellow_triggers.extend(overdue_msgs[:3])
    if cooldown:
        yellow_triggers.extend(cooldown_msgs[:3])
    if pseudo_div:
        yellow_triggers.extend(pseudo_msgs)
    if high_corr:
        yellow_triggers.extend(corr_msgs[:2])
    if rebalance_needed:
        yellow_triggers.extend(rebalance_msgs[:2])

    # ── Determine light ───────────────────────────────────────────────────────
    if red_triggers:
        light: HealthLight = "red"
        label = "有紧急事项需处理"
        top_msg = red_triggers[0]
        all_evidence = red_triggers + yellow_triggers
    elif yellow_triggers:
        light = "yellow"
        label = "有待确认事项"
        top_msg = yellow_triggers[0]
        all_evidence = yellow_triggers
    else:
        light = "green"
        label = "组合状态正常"
        top_msg = "所有指标在正常范围内，无需特别操作"
        all_evidence = []

    evidence = {
        "red": red_triggers,
        "yellow": yellow_triggers,
        "critical_alerts": critical_count,
        "warning_alerts": warning_count,
    }

    return OperatingState(
        state_date=today,
        health_light=light,
        state_label=label,
        critical_count=critical_count,
        warning_count=warning_count,
        top_message=top_msg,
        evidence_json=json.dumps(evidence, ensure_ascii=False),
    )


def save_operating_state(state: OperatingState, db_path=None) -> bool:
    """Upsert OperatingState into daily_operating_state."""
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    try:
        with transaction(db_path) as conn:
            conn.execute(
                """INSERT INTO daily_operating_state
                   (state_date, health_light, state_label,
                    executable_count, confirm_count, monitor_count, blocked_count,
                    critical_count, warning_count,
                    top_message, evidence_json, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(state_date) DO UPDATE SET
                     health_light=excluded.health_light,
                     state_label=excluded.state_label,
                     executable_count=excluded.executable_count,
                     confirm_count=excluded.confirm_count,
                     monitor_count=excluded.monitor_count,
                     blocked_count=excluded.blocked_count,
                     critical_count=excluded.critical_count,
                     warning_count=excluded.warning_count,
                     top_message=excluded.top_message,
                     evidence_json=excluded.evidence_json,
                     updated_at=excluded.updated_at""",
                (state.state_date, state.health_light, state.state_label,
                 state.executable_count, state.confirm_count,
                 state.monitor_count, state.blocked_count,
                 state.critical_count, state.warning_count,
                 state.top_message, state.evidence_json, now),
            )
        return True
    except Exception:
        return False


def compute_and_save(orchestrator_result: Any, db_path=None) -> OperatingState:
    """Compute operating state, backfill task counts from prioritizer, and persist."""
    state = compute_operating_state(orchestrator_result, db_path)

    # Back-fill layer counts from task_calendar (task_generator runs first in runner)
    try:
        from investment.agent_orchestrator.prioritizer import prioritize_all_pending
        grouped = prioritize_all_pending(db_path=db_path)
        state.executable_count = len(grouped.get("executable", []))
        state.confirm_count    = len(grouped.get("confirm",    []))
        state.monitor_count    = len(grouped.get("monitor",    []))
        state.blocked_count    = len(grouped.get("blocked",    []))
    except Exception:
        pass

    save_operating_state(state, db_path)
    return state
