"""Investment calendar — Phase 7 Skill ⑤.

Manages investment tasks: cooldown expiry, earnings dates, rebalance reminders,
monthly/quarterly routines. Fills in the rebalance placeholder from Phase 3.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from investment.core.db import connect, transaction
from investment.agent_tools.translator import fmt_pct


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class CalendarTask:
    task_id: int
    title: str
    category: str
    due_date: str
    priority: str
    status: str
    related_code: Optional[str]
    notes: Optional[str]
    days_until_due: int
    urgency_label: str
    action_required: str


@dataclass
class CalendarReport:
    as_of: str
    period: str
    overdue: list[CalendarTask]
    due_soon: list[CalendarTask]      # within 3 days
    upcoming: list[CalendarTask]      # rest of period
    human_message: str


# ── Category labels ───────────────────────────────────────────────────────────

_CATEGORY_LABELS = {
    "cooldown":   "冷静期到期",
    "earnings":   "财报发布",
    "rebalance":  "再平衡",
    "monthly":    "月度例行",
    "quarterly":  "季度例行",
    "annual":     "年度例行",
    "custom":     "自定义",
}

_PRIORITY_LABELS = {"high": "🔴 高", "medium": "🟡 中", "low": "🔵 低"}


def _urgency(days: int, priority: str) -> str:
    if days < 0:
        return "⚠️ 已逾期"
    if days == 0:
        return "🔴 今日到期"
    if days <= 3:
        return f"🟡 {days} 天后到期"
    return f"🔵 {days} 天后"


def _action_for(category: str, code: Optional[str]) -> str:
    actions = {
        "cooldown":  f"冷静期已到，可以执行 `inv trade log {code or 'CODE'} ...` 记录成交",
        "earnings":  f"关注 {code or '持仓'} 财报，运行 `/earnings-analysis` 解读",
        "rebalance": "运行 `inv risk compute` 检查偏离度，执行再平衡操作",
        "monthly":   "运行 `inv thesis stale` 检查过期论点，更新月度评分",
        "quarterly": "运行 `inv attribution compute` 做季度业绩归因",
        "annual":    "做年度投资总结，更新目标配置",
        "custom":    "按任务说明执行",
    }
    return actions.get(category, "查看任务详情后执行")


# ── DB operations ─────────────────────────────────────────────────────────────

def create_task(
    title: str,
    category: str,
    due_date: str,
    priority: str = "medium",
    related_code: Optional[str] = None,
    notes: Optional[str] = None,
    db_path=None,
) -> int:
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with transaction(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO task_calendar
               (title, category, due_date, priority, status, related_code, notes, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (title, category, due_date, priority, "pending", related_code, notes, now, now),
        )
        task_id = cur.lastrowid
        conn.execute(
            "INSERT INTO task_log (task_id, action, logged_at) VALUES (?,?,?)",
            (task_id, "created", now),
        )
    return task_id


def complete_task(task_id: int, notes: str = "", db_path=None) -> bool:
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with transaction(db_path) as conn:
        row = conn.execute("SELECT id FROM task_calendar WHERE id=?", (task_id,)).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE task_calendar SET status='done', updated_at=? WHERE id=?",
            (now, task_id),
        )
        conn.execute(
            "INSERT INTO task_log (task_id, action, notes, logged_at) VALUES (?,?,?,?)",
            (task_id, "completed", notes, now),
        )
    return True


def get_tasks(
    period: str = "week",
    include_done: bool = False,
    db_path=None,
) -> list[dict]:
    today = date.today()
    if period == "today":
        end = today
    elif period == "week":
        end = today + timedelta(days=7)
    elif period == "month":
        end = today + timedelta(days=30)
    elif period == "quarter":
        end = today + timedelta(days=90)
    else:
        end = today + timedelta(days=365)

    conn = connect(db_path)
    status_filter = "" if include_done else "AND status NOT IN ('done','skipped')"
    rows = conn.execute(
        f"""SELECT * FROM task_calendar
            WHERE due_date <= ?
            {status_filter}
            ORDER BY
              CASE status WHEN 'overdue' THEN 0 ELSE 1 END,
              CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
              due_date""",
        (end.isoformat(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_overdue_tasks(db_path=None) -> int:
    today = date.today().isoformat()
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE task_calendar SET status='overdue', updated_at=? WHERE due_date < ? AND status='pending'",
            (now, today),
        )
        return conn.execute("SELECT changes()").fetchone()[0]


def seed_standard_tasks(db_path=None) -> int:
    """Seed monthly/quarterly routine tasks if not already present."""
    today = date.today()
    month_end = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    quarter_end = date(today.year, ((today.month - 1) // 3 + 1) * 3, 1)
    if quarter_end.month > 12:
        quarter_end = date(today.year + 1, 1, 1) - timedelta(days=1)
    else:
        quarter_end = (quarter_end + timedelta(days=32)).replace(day=1) - timedelta(days=1)

    conn = connect(db_path)
    existing = conn.execute(
        "SELECT title FROM task_calendar WHERE status NOT IN ('done','skipped')"
    ).fetchall()
    conn.close()
    existing_titles = {r["title"] for r in existing}

    tasks_to_seed = [
        ("月度论点评分", "monthly", month_end.isoformat(), "medium", None, "运行 inv thesis stale 检查过期论点"),
        ("月度候选池扫描", "monthly", month_end.isoformat(), "medium", None, "运行 /idea-generation 扫描候选"),
        ("季度业绩归因", "quarterly", quarter_end.isoformat(), "medium", None, "运行 inv attribution compute"),
        ("季度再平衡检查", "quarterly", quarter_end.isoformat(), "high", None, "运行 inv risk compute 检查偏离"),
    ]

    created = 0
    for title, cat, due, pri, code, notes in tasks_to_seed:
        if title not in existing_titles:
            create_task(title, cat, due, pri, code, notes, db_path)
            created += 1
    return created


# ── Rebalance placeholder fill-in (from Phase 3) ─────────────────────────────

def fill_rebalance_placeholder(db_path=None) -> int:
    """Create a rebalance task if position deviation > 5% (Phase 3 placeholder)."""
    from investment.agent_tools.position_monitor import run_position_monitor
    report = run_position_monitor(db_path=db_path)
    if not report.rebalance_needed:
        return 0

    today = date.today()
    due = (today + timedelta(days=7)).isoformat()
    conn = connect(db_path)
    existing = conn.execute(
        "SELECT id FROM task_calendar WHERE category='rebalance' AND status='pending' AND due_date >= ?",
        (today.isoformat(),),
    ).fetchone()
    conn.close()

    if existing:
        return 0

    create_task(
        title="仓位再平衡",
        category="rebalance",
        due_date=due,
        priority="high",
        notes=f"当前配置偏离目标超过 5%，建议在 {due} 前完成再平衡",
        db_path=db_path,
    )
    return 1


# ── Human message builder ─────────────────────────────────────────────────────

def _build_human_message(report: CalendarReport) -> str:
    lines = [f"## 投资日历 — {report.as_of}（{report.period}）\n"]

    if report.overdue:
        lines.append(f"### ⚠️ 已逾期（{len(report.overdue)} 项）")
        for t in report.overdue:
            lines.append(f"- **{t.title}**（{_CATEGORY_LABELS.get(t.category, t.category)}，截止 {t.due_date}）")
            lines.append(f"  所以你该做什么：{t.action_required}")
        lines.append("")

    if report.due_soon:
        lines.append(f"### 🔴 即将到期（{len(report.due_soon)} 项，3 天内）")
        for t in report.due_soon:
            lines.append(f"- **{t.title}** — {t.urgency_label}")
            if t.related_code:
                lines.append(f"  相关标的：{t.related_code}")
            lines.append(f"  所以你该做什么：{t.action_required}")
        lines.append("")

    if report.upcoming:
        lines.append(f"### 📋 本{report.period}计划（{len(report.upcoming)} 项）")
        lines.append("| 任务 | 类型 | 截止 | 优先级 |")
        lines.append("|------|------|------|--------|")
        for t in report.upcoming[:10]:
            cat = _CATEGORY_LABELS.get(t.category, t.category)
            pri = _PRIORITY_LABELS.get(t.priority, t.priority)
            lines.append(f"| {t.title} | {cat} | {t.due_date} | {pri} |")
        if len(report.upcoming) > 10:
            lines.append(f"| ...还有 {len(report.upcoming) - 10} 项 | | | |")
        lines.append("")

    if not report.overdue and not report.due_soon and not report.upcoming:
        lines.append(f"✅ 本{report.period}无待办任务。\n所以你该做什么：继续执行既定策略，下次月末检查。")

    return "\n".join(lines)


# ── Main entry point ──────────────────────────────────────────────────────────

def run_calendar(period: str = "week", db_path=None) -> CalendarReport:
    """Load and categorise calendar tasks for the given period."""
    today = date.today().isoformat()
    mark_overdue_tasks(db_path)
    seed_standard_tasks(db_path)
    fill_rebalance_placeholder(db_path)

    raw = get_tasks(period=period, db_path=db_path)
    today_dt = date.today()

    tasks: list[CalendarTask] = []
    for r in raw:
        due_dt = date.fromisoformat(r["due_date"])
        days = (due_dt - today_dt).days
        t = CalendarTask(
            task_id=r["id"], title=r["title"], category=r["category"],
            due_date=r["due_date"], priority=r["priority"], status=r["status"],
            related_code=r.get("related_code"), notes=r.get("notes"),
            days_until_due=days,
            urgency_label=_urgency(days, r["priority"]),
            action_required=_action_for(r["category"], r.get("related_code")),
        )
        tasks.append(t)

    overdue = [t for t in tasks if t.status == "overdue" or t.days_until_due < 0]
    due_soon = [t for t in tasks if 0 <= t.days_until_due <= 3 and t.status != "overdue"]
    upcoming = [t for t in tasks if t.days_until_due > 3]

    period_labels = {"today": "日", "week": "周", "month": "月", "quarter": "季", "year": "年"}
    report = CalendarReport(
        as_of=today, period=period_labels.get(period, period),
        overdue=overdue, due_soon=due_soon, upcoming=upcoming,
        human_message="",
    )
    report.human_message = _build_human_message(report)
    return report
