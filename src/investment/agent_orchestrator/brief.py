"""Daily brief generator — data-driven, no LLM free-form generation.

Structure:
  1. Account status   — total value, daily P&L, health light
  2. Must-handle      — executable-layer tasks (red, act today)
  3. Confirm needed   — confirm-layer tasks (yellow, decide)
  4. Monitor only     — monitor-layer tasks (blue, watch)
  5. Risk changes     — new / resolved rule_breaches
  6. External signals — high-credibility causal signals (A/B tier)
  7. Next action      — top executable task's suggested_command
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from investment.core.db import connect


@dataclass
class DailyBrief:
    brief_date: str
    health_light: str        # green / yellow / red
    state_label: str

    total_value: float = 0.0
    daily_pnl_pct: float = 0.0

    executable_count: int = 0
    confirm_count: int = 0
    monitor_count: int = 0

    executable_tasks: list[dict] = field(default_factory=list)
    confirm_tasks: list[dict] = field(default_factory=list)
    monitor_tasks: list[dict] = field(default_factory=list)

    new_breaches: list[str] = field(default_factory=list)
    resolved_breaches: list[str] = field(default_factory=list)
    causal_signals: list[str] = field(default_factory=list)

    next_action: str = ""
    next_command: str = ""

    human_message: str = ""


# ── Data extraction helpers ───────────────────────────────────────────────────

def _get_portfolio_summary(position_report: Any) -> tuple[float, float]:
    """Return (total_value, daily_pnl_pct). Falls back to 0 on missing data."""
    if position_report is None:
        return 0.0, 0.0
    total = getattr(position_report, "total_portfolio_value", 0.0) or 0.0
    # Compute weighted average P&L across holdings
    holdings = getattr(position_report, "holdings", []) or []
    if not holdings or total <= 0:
        return total, 0.0
    weighted_pnl = sum(
        h.pnl_pct * h.market_value for h in holdings if h.market_value > 0
    )
    avg_pnl = weighted_pnl / total if total > 0 else 0.0
    return total, avg_pnl


def _get_tasks_from_db(db_path) -> tuple[list[dict], list[dict], list[dict]]:
    """Read task_calendar for today's window, split by decision_layer."""
    today = date.today().isoformat()
    conn = connect(db_path)
    rows = conn.execute(
        """SELECT id, title, related_code, decision_layer, priority,
                  suggested_command, evidence_json, blocking_reason, due_date
           FROM task_calendar
           WHERE status NOT IN ('done','skipped')
             AND due_date <= date(?, '+7 days')
           ORDER BY
             CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
             due_date""",
        (today,),
    ).fetchall()
    conn.close()

    executable, confirm, monitor = [], [], []
    for r in rows:
        task = {
            "id": r["id"],
            "title": r["title"],
            "code": r["related_code"],
            "command": r["suggested_command"] or "",
            "priority": r["priority"],
            "due_date": r["due_date"],
            "blocking_reason": r["blocking_reason"],
        }
        layer = r["decision_layer"] or "monitor"
        if layer == "executable":
            executable.append(task)
        elif layer == "confirm":
            confirm.append(task)
        elif layer in ("monitor", "info", "blocked"):
            monitor.append(task)
        else:
            monitor.append(task)

    return executable, confirm, monitor


def _get_breach_changes(db_path) -> tuple[list[str], list[str]]:
    """New breaches (active, detected today) and recently resolved."""
    today = date.today().isoformat()
    conn = connect(db_path)
    new_rows = conn.execute(
        "SELECT rule_path FROM rule_breaches WHERE status='active' AND date(detected_at)=?",
        (today,),
    ).fetchall()
    # rule_breaches has no resolved_at — use status='resolved' with detected_at as proxy
    resolved_rows = conn.execute(
        "SELECT rule_path FROM rule_breaches WHERE status='resolved'",
    ).fetchall()
    conn.close()
    return (
        [r["rule_path"] for r in new_rows],
        [r["rule_path"] for r in resolved_rows],
    )


def _get_causal_signals(causal_result: Any) -> list[str]:
    """Extract A/B-tier actionable signals from CausalInsightReport."""
    if causal_result is None:
        return []
    actionable = getattr(causal_result, "actionable", []) or []
    signals = []
    for ins in actionable[:5]:
        code = getattr(ins, "holding_code", "")
        direction = getattr(ins, "direction_label", "")
        tier = getattr(ins, "credibility_tier", "")
        narrative = getattr(ins, "narrative", "") or ""
        signals.append(
            f"{code} [{tier}级] {direction}：{narrative[:60]}" if narrative
            else f"{code} [{tier}级] {direction}"
        )
    return signals


# ── Human message builder ─────────────────────────────────────────────────────

_LIGHT_LABELS = {"red": "🔴", "yellow": "🟡", "green": "🟢"}


def _fmt_value(v: float) -> str:
    if v >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if v >= 10_000:
        return f"{v/10_000:.1f}万"
    return f"{v:.0f}"


def _build_human_message(brief: DailyBrief) -> str:
    lines = [
        f"=== 今日简报 {brief.brief_date} ===",
        f"{_LIGHT_LABELS.get(brief.health_light, '⚪')} {brief.state_label}",
        "",
    ]

    # Account status
    if brief.total_value > 0:
        pnl_sign = "+" if brief.daily_pnl_pct >= 0 else ""
        lines.append(
            f"账户总值：{_fmt_value(brief.total_value)}  "
            f"持仓盈亏：{pnl_sign}{brief.daily_pnl_pct*100:.2f}%"
        )
        lines.append("")

    # Task counts
    lines.append(
        f"可执行 {brief.executable_count}  待确认 {brief.confirm_count}  仅监控 {brief.monitor_count}"
    )
    lines.append("")

    # Executable tasks
    if brief.executable_tasks:
        lines.append("── 🔴 必须处理 ──")
        for t in brief.executable_tasks[:5]:
            cmd = f"  → {t['command']}" if t.get("command") else ""
            lines.append(f"  • {t['title']}{cmd}")
        lines.append("")

    # Confirm tasks
    if brief.confirm_tasks:
        lines.append("── 🟡 待确认 ──")
        for t in brief.confirm_tasks[:5]:
            lines.append(f"  • {t['title']}")
        lines.append("")

    # Risk changes
    if brief.new_breaches:
        lines.append("── 风控变化 ──")
        for b in brief.new_breaches[:3]:
            lines.append(f"  ⚠ 新增违规：{b}")
        lines.append("")

    if brief.resolved_breaches:
        for b in brief.resolved_breaches[:3]:
            lines.append(f"  ✓ 已解除违规：{b}")
        lines.append("")

    # Causal signals
    if brief.causal_signals:
        lines.append("── 🔵 外部信号 ──")
        for s in brief.causal_signals[:3]:
            lines.append(f"  • {s}")
        lines.append("")

    # Next action
    if brief.next_action:
        lines.append(f"── 下一步 ──")
        lines.append(f"  {brief.next_action}")
        if brief.next_command:
            lines.append(f"  → {brief.next_command}")

    return "\n".join(lines)


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_brief(
    orchestrator_result: Any,
    operating_state: Any,
    db_path=None,
) -> DailyBrief:
    """Build DailyBrief from orchestrator results and operating state."""
    today = date.today().isoformat()

    total_value, daily_pnl = _get_portfolio_summary(orchestrator_result.position_report)
    executable, confirm, monitor = _get_tasks_from_db(db_path)
    new_breaches, resolved_breaches = _get_breach_changes(db_path)
    causal_signals = _get_causal_signals(orchestrator_result.causal_result)

    # Update operating state task counts (back-fill after task_generator runs in Phase 3)
    exe_count = len(executable)
    con_count  = len(confirm)
    mon_count  = len(monitor)

    # Next action = first executable task
    next_action = ""
    next_command = ""
    if executable:
        t = executable[0]
        next_action = t["title"]
        next_command = t.get("command", "")

    brief = DailyBrief(
        brief_date=today,
        health_light=getattr(operating_state, "health_light", "green"),
        state_label=getattr(operating_state, "state_label", ""),
        total_value=total_value,
        daily_pnl_pct=daily_pnl,
        executable_count=exe_count,
        confirm_count=con_count,
        monitor_count=mon_count,
        executable_tasks=executable[:10],
        confirm_tasks=confirm[:10],
        monitor_tasks=monitor[:10],
        new_breaches=new_breaches,
        resolved_breaches=resolved_breaches,
        causal_signals=causal_signals,
        next_action=next_action,
        next_command=next_command,
    )
    brief.human_message = _build_human_message(brief)
    return brief


def format_brief_text(brief: DailyBrief) -> str:
    """Return the pre-built human message (or rebuild if empty)."""
    return brief.human_message or _build_human_message(brief)
