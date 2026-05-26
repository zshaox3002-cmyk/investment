#!/usr/bin/env python3
"""
dashboard.py — 每日战情室生成器

读取 execution_tracker.yaml + 各源文件，生成自包含 DASHBOARD.html。
所有面向用户的查阅内容使用 HTML 展示。

用法:
    python scripts/dashboard.py              # 标准模式
    python scripts/dashboard.py --pre-market  # 盘前模式（强调今日待办 + 盘前检查）
    python scripts/dashboard.py --post-market # 盘后模式（强调进度更新 + 盘后检查）
"""

import argparse
import csv
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml

# ── Paths ────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
CONFIG_DIR = ROOT_DIR / "config"
TRACKER_PATH = CONFIG_DIR / "execution_tracker.yaml"
RULES_PATH = CONFIG_DIR / "rules.yaml"
CAPITAL_PATH = CONFIG_DIR / "capital.yaml"
MACRO_PATH = CONFIG_DIR / "macro.md"
SNAPSHOT_PATH = CONFIG_DIR / "portfolio_snapshot.csv"
ALERTS_DIR = ROOT_DIR / "alerts"
TRADES_DIR = ROOT_DIR / "trades"
OUTPUT_PATH = ROOT_DIR / "DASHBOARD.html"

# ── Helpers ──────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


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


def _status_badge(status: str) -> str:
    """Return (css_class, label) for a status value."""
    mapping = {
        "pending": ("badge-pending", "待执行"),
        "in_progress": ("badge-progress", "进行中"),
        "done": ("badge-done", "已完成"),
        "skipped": ("badge-skipped", "已跳过"),
        "blocked": ("badge-blocked", "条件未触发"),
        "overdue": ("badge-overdue", "已逾期"),
    }
    cls, label = mapping.get(status, ("badge-pending", status))
    return cls, label


def _severity_class(severity: str) -> str:
    return {"critical": "sev-critical", "warning": "sev-warning", "info": "sev-info"}.get(
        severity, "sev-info"
    )


def _priority_order(status: str) -> int:
    """Sort key: critical items first, then pending, then blocked."""
    if status == "overdue":
        return 0
    if status == "in_progress":
        return 1
    if status == "pending":
        return 2
    if status == "blocked":
        return 3
    return 4


def _days_between(d1: str, d2: Optional[str] = None) -> int:
    """Calculate days between two date strings."""
    try:
        dt1 = datetime.strptime(d1, "%Y-%m-%d").date()
        dt2 = datetime.strptime(d2, "%Y-%m-%d").date() if d2 else date.today()
        return (dt1 - dt2).days
    except (ValueError, TypeError):
        return 999


def _compute_urgency(item: dict, today: str) -> str:
    """Compute urgency level P0-P4 based on dates."""
    deadline = item.get("deadline", "")
    planned = item.get("planned_date", "")
    check_date = deadline or planned
    if not check_date:
        return "P4"
    days = _days_between(check_date, today)
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
    """Sort key for urgency levels."""
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}.get(urgency, 5)


def _task_type_icon(task_type: str) -> str:
    """Return emoji icon for task type."""
    return {"trade": "💰", "research": "🔬", "compliance": "⚠️", "monitor": "👁"}.get(task_type, "📋")


def _urgency_badge(urgency: str) -> str:
    """Return small inline badge HTML for urgency level."""
    colors = {"P0": "#c53030", "P1": "#d69e2e", "P2": "#2b6cb0", "P3": "#718096", "P4": "#a0aec0"}
    bg = {"P0": "#fff5f5", "P1": "#fffbeb", "P2": "#ebf8ff", "P3": "#f7fafc", "P4": "#f7fafc"}
    color = colors.get(urgency, "#a0aec0")
    bg_color = bg.get(urgency, "#f7fafc")
    return f'<span style="display:inline-block;padding:0 6px;border-radius:4px;font-size:10px;font-weight:700;color:{color};background:{bg_color};margin-left:4px">{urgency}</span>'


def _check_dep_blocked(item: dict, all_items: list[dict]) -> tuple:
    """Return (is_blocked, blocked_by_ids) based on dependency status."""
    deps = item.get("depends_on", [])
    if not deps:
        return False, []
    status_map = {}
    for section_items in all_items:
        for i in section_items:
            status_map[i["id"]] = i.get("status", "pending")
    blocked_by = [d for d in deps if status_map.get(d) != "done"]
    return len(blocked_by) > 0, blocked_by


# ── Data Loading ─────────────────────────────────────────────────────────


def load_tracker() -> dict:
    return _load_yaml(TRACKER_PATH)


def load_rules() -> dict:
    return _load_yaml(RULES_PATH)


def load_capital() -> dict:
    return _load_yaml(CAPITAL_PATH)


def load_portfolio_snapshot() -> list[dict]:
    """Load portfolio_snapshot.csv and return structured data."""
    return _load_csv(SNAPSHOT_PATH)


def load_alerts_summary() -> dict:
    """Summarize today's alerts from alerts/ directory."""
    today = date.today().strftime("%Y-%m-%d")
    alerts = {"critical": 0, "warning": 0, "info": 0, "total": 0, "latest": []}
    if not ALERTS_DIR.exists():
        return alerts

    alert_files = sorted(ALERTS_DIR.glob(f"{today}*.md"), reverse=True)
    alerts["total"] = len(alert_files)

    for af in alert_files[:8]:
        text = af.read_text(encoding="utf-8")
        first_line = text.strip().split("\n")[0].lstrip("#").strip() if text.strip() else af.stem
        severity = "info"
        if "l3" in af.stem.lower() or "critical" in af.stem.lower():
            severity = "critical"
        elif "l2" in af.stem.lower() or "warning" in af.stem.lower():
            severity = "warning"
        # Extract stock code and alert type from filename: YYYY-MM-DD_CODE_type.md
        parts = af.stem.split("_", 2)
        stock_code = parts[1] if len(parts) > 1 else ""
        alert_type = parts[2] if len(parts) > 2 else ""
        alerts[severity] += 1
        alerts["latest"].append({
            "file": af.name, "title": first_line, "severity": severity,
            "stock_code": stock_code, "alert_type": alert_type,
            "snippet": "\n".join(text.strip().split("\n")[:15]),
        })

    return alerts


def load_macro_events() -> list[dict]:
    """Extract upcoming macro events from config/macro.md."""
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
                events.append({
                    "date": parts[1],
                    "event": parts[2] if len(parts) > 2 else "",
                    "impact": parts[3] if len(parts) > 3 else "",
                })
        elif in_table and not line.startswith("|"):
            in_table = False

    return events


def load_goals(tracker: dict) -> list[dict]:
    """Load WBS goals from execution_tracker.yaml, if present."""
    return tracker.get("goals", [])


def _build_task_lookup(tracker: dict) -> dict:
    """Build a flat {task_id: task_dict} lookup across all 4 task sections."""
    lookup = {}
    for section_key in ["c_tranche_restructure", "b_tranche_build",
                        "compliance_remediation", "thesis_maintenance"]:
        for item in tracker.get(section_key, []):
            lookup[item["id"]] = {**item, "_section": section_key}
    return lookup


def _compute_goal_stats(goal: dict, task_lookup: dict) -> dict:
    """Recursively compute task completion stats for a goal and its sub-goals."""
    counts = {"total": 0, "done": 0, "in_progress": 0, "pending": 0, "blocked": 0}
    for tid in goal.get("task_refs", []):
        task = task_lookup.get(tid)
        if task:
            counts["total"] += 1
            status = task.get("status", "pending")
            if status == "done":
                counts["done"] += 1
            elif status == "in_progress":
                counts["in_progress"] += 1
            elif status == "blocked":
                counts["blocked"] += 1
            else:
                counts["pending"] += 1
    for sg in goal.get("sub_goals", []):
        sub = _compute_goal_stats(sg, task_lookup)
        for k in counts:
            counts[k] += sub[k]
    return counts


def get_today_actions(tracker: dict) -> list[dict]:
    """Collect all active (non-done, non-skipped) actions with urgency and deps."""
    today = date.today().strftime("%Y-%m-%d")
    actions = []

    for section_key in ["c_tranche_restructure", "b_tranche_build",
                        "compliance_remediation", "thesis_maintenance"]:
        for item in tracker.get(section_key, []):
            status = item.get("status", "pending")

            # Skip done and skipped items
            if status in ("done", "skipped"):
                continue

            planned = item.get("planned_date", "")
            deadline = item.get("deadline", "")

            # Check if overdue
            if status in ("pending", "in_progress"):
                check_date = deadline or planned
                if check_date and _days_between(check_date, today) < 0:
                    status = "overdue"

            urgency = _compute_urgency(item, today)

            actions.append({**item, "_section": section_key, "_effective_status": status,
                           "_urgency": urgency})

    # Check dependencies across all collected items
    for a in actions:
        dep_blocked, blocked_by = _check_dep_blocked(a, [tracker.get(k, []) for k in [
            "c_tranche_restructure", "b_tranche_build", "compliance_remediation",
            "thesis_maintenance"]])
        a["_dep_blocked"] = dep_blocked
        a["_blocked_by"] = blocked_by

    # Sort: urgency (P0 first) > effective status > date
    actions.sort(key=lambda a: (
        _urgency_sort_key(a.get("_urgency", "P4")),
        _priority_order(a["_effective_status"]),
        a.get("planned_date", a.get("deadline", "9999-99-99")),
    ))
    return actions


def calculate_progress(tracker: dict) -> dict:
    """Calculate progress bars for each section."""
    progress = {}

    for section_key, label in [
        ("c_tranche_restructure", "C仓位重组"),
        ("b_tranche_build", "B仓ETF建仓"),
        ("compliance_remediation", "合规整改"),
        ("thesis_maintenance", "Thesis维护"),
    ]:
        items = tracker.get(section_key, [])
        total = len(items)
        done = sum(1 for i in items if i.get("status") == "done")
        in_prog = sum(1 for i in items if i.get("status") == "in_progress")
        progress[section_key] = {
            "label": label,
            "total": total,
            "done": done,
            "in_progress": in_prog,
            "pct": (done / total * 100) if total > 0 else 0,
        }

    return progress


# ── HTML Generation ──────────────────────────────────────────────────────


def render(tracker: dict, rules: dict, capital: dict, snapshot: list[dict],
           alerts: dict, events: list[dict], mode: str,
           goals: list[dict] | None = None, task_lookup: dict | None = None) -> str:
    """Render the complete DASHBOARD.html."""
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][today.weekday()]

    if goals is None:
        goals = []
    if task_lookup is None:
        task_lookup = {}

    actions = get_today_actions(tracker)
    progress = calculate_progress(tracker)
    breaches = rules.get("active_breaches", [])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>每日战情室 · {today_str}</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei","Helvetica Neue",sans-serif;background:#f0f2f5;color:#1a202c;font-size:14px;line-height:1.6}}

/* ── Top Bar ── */
#topbar{{background:#0d1b2a;color:#fff;padding:0 28px;height:56px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;box-shadow:0 2px 12px rgba(0,0,0,.2)}}
#topbar .title{{font-size:17px;font-weight:700;letter-spacing:.02em}}
#topbar .title span{{color:#5b9bd5}}
#topbar .meta{{font-size:12px;color:#8899aa}}
#topbar .meta .mode{{background:rgba(255,255,255,.12);padding:3px 10px;border-radius:12px;margin-left:10px}}
#topbar .alerts-badge{{background:#c53030;color:#fff;border-radius:12px;padding:2px 10px;font-size:11px;font-weight:600;margin-left:8px}}

/* ── Main Content ── */
#main{{max-width:1320px;margin:0 auto;padding:24px}}

/* ── Metric Cards Row ── */
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:24px}}
.metric-card{{background:#fff;border-radius:10px;padding:18px 20px;box-shadow:0 1px 4px rgba(0,0,0,.06);border-left:4px solid #e2e8f0}}
.metric-card .mc-label{{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:#718096;margin-bottom:6px}}
.metric-card .mc-value{{font-size:22px;font-weight:700}}
.metric-card .mc-sub{{font-size:11px;color:#a0aec0;margin-top:3px}}
.metric-card.card-total{{border-left-color:#0d1b2a}}
.metric-card.card-a{{border-left-color:#00875a}}
.metric-card.card-b{{border-left-color:#0066cc}}
.metric-card.card-c{{border-left-color:#7c3aed}}
.metric-card.card-d{{border-left-color:#718096}}
.metric-card.card-alert{{border-left-color:#c53030}}

/* ── Section ── */
.section{{margin-bottom:28px}}
.section-title{{font-size:15px;font-weight:700;color:#1a202c;margin-bottom:14px;padding-bottom:8px;border-bottom:2px solid #e2e8f0;display:flex;align-items:center;gap:10px}}
.section-title .icon{{font-size:18px}}

/* ── Table ── */
.data-table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.data-table th{{background:#f7f9fc;color:#4a5568;font-weight:600;font-size:12px;padding:10px 12px;text-align:left;border-bottom:2px solid #e2e8f0;white-space:nowrap}}
.data-table td{{padding:10px 12px;border-bottom:1px solid #f0f4f8;font-size:13px;color:#4a5568}}
.data-table tr:hover td{{background:#f7f9fc}}
.data-table .amount{{text-align:right;font-variant-numeric:tabular-nums}}
.data-table .date-col{{white-space:nowrap;font-size:12px}}

/* ── Badges ── */
.badge{{display:inline-block;padding:2px 10px;border-radius:10px;font-size:11px;font-weight:600;white-space:nowrap}}
.badge-pending{{background:#edf2f7;color:#718096}}
.badge-progress{{background:#ebf4ff;color:#0066cc}}
.badge-done{{background:#f0fff4;color:#276749;text-decoration:line-through}}
.badge-skipped{{background:#f7fafc;color:#a0aec0;text-decoration:line-through}}
.badge-blocked{{background:#fffbeb;color:#b7791f}}
.badge-overdue{{background:#fff5f5;color:#c53030;font-weight:700}}

/* ── Progress Bar ── */
.progress-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}}
.progress-card{{background:#fff;border-radius:10px;padding:14px 18px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.progress-card .prog-label{{font-size:13px;font-weight:600;color:#2d3748;margin-bottom:8px;display:flex;justify-content:space-between}}
.progress-card .prog-label .prog-stat{{font-size:12px;color:#718096;font-weight:400}}
.progress-bar{{height:8px;background:#edf2f7;border-radius:4px;overflow:hidden}}
.progress-bar .fill{{height:100%;border-radius:4px;transition:width .3s}}
.fill-blue{{background:linear-gradient(90deg,#0066cc,#4299e1)}}
.fill-green{{background:linear-gradient(90deg,#00875a,#38b2ac)}}
.fill-purple{{background:linear-gradient(90deg,#7c3aed,#9f7aea)}}
.fill-orange{{background:linear-gradient(90deg,#d69e2e,#ecc94b)}}

/* ── Alert/Warning Boxes ── */
.alert-box{{display:flex;align-items:flex-start;gap:10px;padding:12px 16px;border-radius:8px;margin-bottom:8px;font-size:13px}}
.alert-critical{{background:#fff5f5;border:1px solid #feb2b2;color:#742a2a}}
.alert-warning{{background:#fffbeb;border:1px solid #fbd38d;color:#744210}}
.alert-info{{background:#ebf8ff;border:1px solid #bee3f8;color:#2a4365}}
.alert-icon{{font-size:16px;flex-shrink:0}}
.alert-deadline{{font-size:11px;color:#a0aec0;margin-top:2px}}
/* Alert detail cards */
.alert-card{{background:#fff;border-radius:10px;margin-bottom:10px;box-shadow:0 1px 4px rgba(0,0,0,.06);overflow:hidden;cursor:pointer;transition:box-shadow .15s}}
.alert-card:hover{{box-shadow:0 2px 8px rgba(0,0,0,.1)}}
.alert-card-header{{display:flex;align-items:center;gap:10px;padding:12px 16px;font-size:13px;border-left:4px solid #e2e8f0;user-select:none}}
.alert-card-header.sev-critical{{border-left-color:#c53030}}
.alert-card-header.sev-warning{{border-left-color:#d69e2e}}
.alert-card-header.sev-info{{border-left-color:#3182ce}}
.alert-card-header .alert-dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
.alert-dot.dot-critical{{background:#c53030}}
.alert-dot.dot-warning{{background:#d69e2e}}
.alert-dot.dot-info{{background:#3182ce}}
.alert-card-header .alert-type-label{{font-weight:600;color:#2d3748;white-space:nowrap}}
.alert-card-header .alert-stock{{background:#edf2f7;padding:1px 8px;border-radius:4px;font-size:11px;font-weight:600;color:#4a5568;white-space:nowrap}}
.alert-card-header .alert-title{{flex:1;color:#4a5568;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.alert-card-header .alert-expand{{font-size:11px;color:#a0aec0;transition:transform .2s;flex-shrink:0}}
.alert-card-header.expanded .alert-expand{{transform:rotate(180deg)}}
.alert-card-body{{display:none;padding:12px 16px 16px 30px;font-size:12px;color:#718096;border-top:1px solid #f0f4f8;white-space:pre-wrap;max-height:300px;overflow-y:auto}}
/* ── WBS Goal Accordion ── */
.wbs-goal{{background:#fff;border-radius:10px;margin-bottom:12px;box-shadow:0 1px 4px rgba(0,0,0,.06);overflow:hidden}}
.wbs-goal-header{{display:flex;align-items:center;gap:10px;padding:14px 18px;cursor:pointer;transition:background .15s;user-select:none;border-bottom:1px solid transparent}}
.wbs-goal-header:hover{{background:#f7f9fc}}
.wbs-goal-header.expanded{{border-bottom-color:#e2e8f0}}
.wbs-goal-header .wbs-code{{background:#0d1b2a;color:#fff;border-radius:4px;padding:2px 8px;font-size:11px;font-weight:700;font-family:monospace;flex-shrink:0}}
.wbs-goal-header .wbs-name{{font-weight:600;font-size:14px;color:#1a202c;flex:1}}
.wbs-goal-header .wbs-stat{{font-size:11px;color:#718096;white-space:nowrap}}
.wbs-goal-header .mini-progress{{width:80px;height:5px;background:#edf2f7;border-radius:3px;overflow:hidden;flex-shrink:0}}
.wbs-goal-header .mini-progress .mini-fill{{height:100%;border-radius:3px;transition:width .3s}}
.wbs-goal-header .wbs-expand{{font-size:11px;color:#a0aec0;transition:transform .2s;flex-shrink:0}}
.wbs-goal-header.expanded .wbs-expand{{transform:rotate(180deg)}}
.wbs-goal-body{{display:none;padding:0}}
.wbs-goal-desc{{padding:10px 18px;font-size:12px;color:#718096;border-bottom:1px solid #f0f4f8}}
.wbs-sub-goals{{padding:0 0 0 20px}}
.wbs-goal.nested{{box-shadow:none;border:1px solid #edf2f7}}
.wbs-goal.nested .wbs-goal-header{{padding:10px 14px;background:#fafbfc}}
.wbs-goal.nested .wbs-goal-header .wbs-name{{font-size:13px}}
.wbs-task-row{{display:flex;align-items:center;gap:8px;padding:8px 18px;font-size:12px;border-bottom:1px solid #f0f4f8;color:#4a5568;transition:background .3s}}
.wbs-task-row:last-child{{border-bottom:none}}

/* ── Checklist ── */
.checklist{{background:#fff;border-radius:10px;padding:16px 20px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.checklist-item{{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #f0f4f8;font-size:13px;color:#4a5568}}
.checklist-item:last-child{{border-bottom:none}}
.checklist-item input[type=checkbox]{{width:18px;height:18px;accent-color:#0066cc;flex-shrink:0}}

/* ── Event Timeline ── */
.event-row{{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid #f0f4f8;font-size:13px}}
.event-row:last-child{{border-bottom:none}}
.event-date{{background:#edf2f7;padding:2px 10px;border-radius:6px;font-size:11px;font-weight:600;white-space:nowrap;color:#4a5568}}
.event-date.urgent{{background:#fff5f5;color:#c53030}}

/* ── Mode Highlight ── */
.mode-pre .pre-market-section{{display:block}}
.mode-pre .post-market-section{{display:none}}
.mode-post .post-market-section{{display:block}}
.mode-post .pre-market-section{{display:none}}
.mode-standard .pre-market-section,.mode-standard .post-market-section{{display:block}}

/* ── Two Column ── */
.col2{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}

/* ── Footer ── */
#footer{{text-align:center;padding:20px;font-size:11px;color:#a0aec0;border-top:1px solid #e2e8f0;margin-top:32px}}

@media(max-width:800px){{
  .metrics{{grid-template-columns:repeat(2,1fr)}}
  .col2{{grid-template-columns:1fr}}
  #topbar{{padding:0 16px}}
  #main{{padding:16px}}
}}
</style>
</head>
<body class="mode-{mode}">
<header id="topbar">
  <div class="title">每日战情室 · <span>{today_str}</span> {weekday}</div>
  <div class="meta">
    活跃告警 <span class="alerts-badge" onclick="scrollToAlertSection()" style="cursor:pointer;transition:transform .15s" onmouseover="this.style.transform='scale(1.1)'" onmouseout="this.style.transform='scale(1)'" title="点击查看告警详情">{alerts['critical'] + alerts['warning']}</span>
    <span class="mode">{ {'standard':'全景','pre-market':'盘前','post-market':'盘后'}.get(mode, '全景') }</span>
  </div>
</header>

<div id="main">

<!-- ═══════════ 组合概览 ═══════════ -->
{_render_metrics(snapshot, capital, alerts)}

<!-- ═══════════ 活跃告警 ═══════════ -->
<div class="section" id="section-alerts">
  <div class="section-title"><span class="icon">&#x1F514;</span> 活跃告警详情</div>
  {_render_alerts(alerts)}
</div>

<!-- ═══════════ 执行看板 ═══════════ -->
<div class="section">
  <div class="section-title"><span class="icon">&#x1F4CB;</span> 执行看板</div>
  {_render_wbs(goals, task_lookup, today_str, actions)}
</div>

<!-- ═══════════ 执行进度 ═══════════ -->
<div class="section">
  <div class="section-title"><span class="icon">&#x1F4CA;</span> 执行进度</div>
  {_render_progress(progress)}
</div>

<!-- ═══════════ 合规预警 ═══════════ -->
<div class="section">
  <div class="section-title"><span class="icon">&#x26A0;&#xFE0F;</span> 合规预警</div>
  {_render_compliance(breaches, tracker)}
</div>

<!-- ═══════════ 盘前/盘后检查 ═══════════ -->
<div class="col2">
  <div class="section pre-market-section">
    <div class="section-title"><span class="icon">&#x1F305;</span> 盘前检查清单</div>
    {_render_pre_market(tracker, events)}
  </div>
  <div class="section post-market-section">
    <div class="section-title"><span class="icon">&#x1F307;</span> 盘后检查清单</div>
    {_render_post_market(tracker)}
  </div>
</div>

<!-- ═══════════ 即将到来的事件 ═══════════ -->
<div class="section">
  <div class="section-title"><span class="icon">&#x1F4C5;</span> 未来 30 天关键事件</div>
  {_render_upcoming(tracker)}
</div>

</div>

<div id="footer">
  每日战情室 · 由 dashboard.py 自动生成 · {datetime.now().strftime('%Y-%m-%d %H:%M')} · 数据来源: execution_tracker.yaml + rules.yaml + portfolio_snapshot.csv
</div>

<script>
function toggleAlert(id){{var b=document.getElementById('alert-body-'+id);var h=document.getElementById('alert-header-'+id);if(b.style.display==='none'||!b.style.display){{b.style.display='block';h.classList.add('expanded')}}else{{b.style.display='none';h.classList.remove('expanded')}}}}
function toggleGoal(id){{var b=document.getElementById('goal-body-'+id);var h=document.getElementById('goal-header-'+id);if(b.style.display==='none'||!b.style.display){{b.style.display='block';h.classList.add('expanded')}}else{{b.style.display='none';h.classList.remove('expanded')}}}}
function scrollToAlertSection(){{var e=document.getElementById('section-alerts');if(e){{e.scrollIntoView({{behavior:'smooth'}});var cards=e.querySelectorAll('.alert-card-body');cards.forEach(function(c){{c.style.display='block'}});var headers=e.querySelectorAll('.alert-card-header');headers.forEach(function(h){{h.classList.add('expanded')}})}}}}
function scrollAndHighlight(id){{var e=document.getElementById(id);if(e){{e.scrollIntoView({{behavior:'smooth',block:'center'}});e.style.transition='background .3s';e.style.background='#fffbeb';setTimeout(function(){{e.style.background=''}},2000)}}}}
</script>
</body>
</html>"""


# ── WBS 目标看板 ──

def _render_wbs(goals: list[dict], task_lookup: dict, today_str: str,
                actions: list[dict]) -> str:
    """Render WBS goal accordion, falling back to flat table if no goals."""
    if not goals:
        return _render_action_table(actions)
    parts = []
    for g in goals:
        parts.append(_render_wbs_goal(g, task_lookup, today_str, depth=0))
    return "\n".join(parts)


def _render_wbs_goal(goal: dict, task_lookup: dict, today_str: str,
                     depth: int = 0) -> str:
    """Recursively render a WBS goal card with its sub-goals and tasks."""
    gid = goal["id"]
    stats = _compute_goal_stats(goal, task_lookup)
    done = stats["done"]
    total = stats["total"]
    pct = int(done / total * 100) if total > 0 else 0
    wbs = goal.get("wbs_code", "")
    icon = goal.get("icon", "")
    name = goal["name"]
    desc = goal.get("description", "")
    sub_goals = goal.get("sub_goals", [])
    task_refs = goal.get("task_refs", [])

    prog_color = "#38a169" if pct == 100 else "#0066cc" if pct > 0 else "#cbd5e0"
    nested_class = " nested" if depth > 0 else ""
    desc_html = f'<div class="wbs-goal-desc">{desc}</div>' if desc else ""

    # Build body content: sub-goals recursively, then direct tasks
    body_parts = []

    # Render sub-goals
    for sg in sub_goals:
        body_parts.append(_render_wbs_goal(sg, task_lookup, today_str, depth + 1))

    # Render direct tasks
    for tid in task_refs:
        task = task_lookup.get(tid)
        if not task:
            continue
        status = task.get("status", "pending")
        status_badge = {
            "done": '<span class="badge badge-done">&#x2705; 完成</span>',
            "in_progress": '<span class="badge badge-active">&#x23F3; 进行中</span>',
            "blocked": '<span class="badge badge-blocked">&#x1F512; 阻塞</span>',
            "pending": '<span class="badge badge-pending">&#x23F3; 待办</span>',
            "skipped": '<span class="badge" style="background:#e2e8f0;color:#718096">&#x23ED; 跳过</span>',
        }.get(status, f'<span class="badge">{status}</span>')

        d = task.get("deadline") or task.get("planned_date") or ""
        days_left = _days_between(d) if d else 999
        if days_left <= 0:
            urgency_badge = '<span class="badge badge-urgent">P0</span>'
        elif days_left <= 3:
            urgency_badge = '<span class="badge badge-warn">P1</span>'
        elif days_left <= 7:
            urgency_badge = '<span class="badge" style="background:#e8f0fe;color:#0066cc">P2</span>'
        elif days_left <= 30:
            urgency_badge = '<span class="badge" style="background:#f0f4f8;color:#718096">P3</span>'
        else:
            urgency_badge = '<span class="badge" style="background:#f0f4f8;color:#a0aec0">P4</span>'

        amt = task.get("amount", "")
        amt_str = f'<span style="font-weight:600;color:#1a202c">¥{amt:,.0f}</span>' if amt else ""

        dep_ids = task.get("depends_on", [])
        dep_html = ""
        if dep_ids:
            dep_labels = []
            for did in dep_ids:
                dt = task_lookup.get(did)
                if dt:
                    dstatus = dt.get("status", "pending")
                    ddone = "&#x2705;" if dstatus == "done" else "&#x23F3;"
                    dep_labels.append(f"{ddone} {did}")
                else:
                    dep_labels.append(did)
            dep_html = f' <span style="font-size:10px;color:#a0aec0" title="依赖: {", ".join(dep_labels)}">&#x1F517; {" ".join(dep_labels)}</span>'

        notes = task.get("notes", "") or task.get("condition", "") or ""
        notes_str = f'<span style="color:#a0aec0;font-size:11px;margin-left:8px">{notes[:60]}</span>' if notes else ""

        row_style = 'opacity:0.5' if status in ("skipped",) else ''
        body_parts.append(f"""<div class="wbs-task-row" id="task-{tid}" style="{row_style}">
  {urgency_badge} {status_badge}
  <span style="flex:1;margin:0 8px">{task["action"]}{notes_str}</span>
  {amt_str}
  <span style="font-size:11px;color:#a0aec0;min-width:65px;text-align:right">{d}</span>
  {dep_html}
</div>""")

    body = "\n".join(body_parts) if body_parts else '<div class="wbs-goal-desc" style="color:#a0aec0">暂无任务</div>'
    # Only wrap direct task rows in a container; sub-goals are already cards
    # We combine sub-goals (which are full cards) with direct tasks

    return f"""<div class="wbs-goal{nested_class}">
  <div class="wbs-goal-header" id="goal-header-{gid}" onclick="toggleGoal('{gid}')">
    <span class="wbs-code">{wbs}</span>
    <span style="font-size:16px">{icon}</span>
    <span class="wbs-name">{name}</span>
    <span class="wbs-stat">{done}/{total}</span>
    <div class="mini-progress"><div class="mini-fill" style="width:{pct}%;background:{prog_color}"></div></div>
    <span class="wbs-stat" style="min-width:36px;text-align:right">{pct}%</span>
    <span class="wbs-expand">&#x25BC;</span>
  </div>
  <div class="wbs-goal-body" id="goal-body-{gid}">
    {desc_html}
    <div class="wbs-sub-goals">{body}</div>
  </div>
</div>"""


def _render_metrics(snapshot: list[dict], capital: dict, alerts: dict) -> str:
    """Render the metric cards row."""
    total_assets = 0
    a_total = b_total = c_total = d_total = 0

    for row in snapshot:
        try:
            val = float(row.get("current_value", 0))
        except (ValueError, TypeError):
            val = 0
        total_assets += val
        cat = row.get("category", "")
        if cat == "A档现金" or cat == "A档债券" or "A档" in cat:
            a_total += val
        elif cat == "B档ETF" or "B档" in cat:
            b_total += val
        elif cat == "C档个股" or "C档" in cat:
            c_total += val
        elif cat == "D档其他" or "D档" in cat:
            d_total += val

    if total_assets == 0:
        a_total = capital.get("target_a", 0)
        b_total = capital.get("target_b", 0) if capital.get("target_b", 0) else sum(
            float(r.get("current_value", 0)) for r in snapshot if "B档" in str(r.get("category", ""))
        )
        c_total = sum(float(r.get("current_value", 0)) for r in snapshot if "C档" in str(r.get("category", "")))
        d_total = sum(float(r.get("current_value", 0)) for r in snapshot if "D档" in str(r.get("category", "")))
        total_assets = a_total + b_total + c_total + d_total

    rebalance_base = capital.get("rebalance_base_capital", total_assets - d_total)
    target_b = capital.get("target_b", 0)

    def _pct(part, whole):
        return f"{part/whole*100:.1f}%" if whole > 0 else "0%"

    return f"""<div class="metrics">
  <div class="metric-card card-total">
    <div class="mc-label">总资产</div>
    <div class="mc-value">{_fmt_cny(total_assets)}</div>
    <div class="mc-sub">再平衡基准 {_fmt_cny(rebalance_base)}</div>
  </div>
  <div class="metric-card card-a">
    <div class="mc-label">A 档 · 现金/债券</div>
    <div class="mc-value">{_fmt_cny(a_total)} <small style="font-size:14px;color:#718096">({_pct(a_total, total_assets)})</small></div>
    <div class="mc-sub">目标 {_fmt_cny(capital.get('target_a', 0))}</div>
  </div>
  <div class="metric-card card-b">
    <div class="mc-label">B 档 · 核心 ETF</div>
    <div class="mc-value">{_fmt_cny(b_total)} <small style="font-size:14px;color:#718096">({_pct(b_total, total_assets)})</small></div>
    <div class="mc-sub">目标 {_fmt_cny(target_b)}</div>
  </div>
  <div class="metric-card card-c">
    <div class="mc-label">C 档 · 主动选股</div>
    <div class="mc-value">{_fmt_cny(c_total)} <small style="font-size:14px;color:#718096">({_pct(c_total, total_assets)})</small></div>
    <div class="mc-sub">目标 {_fmt_cny(capital.get('target_c', 0))} | 上限 30%</div>
  </div>
  <div class="metric-card card-alert">
    <div class="mc-label">活跃告警</div>
    <div class="mc-value" style="color:#c53030;cursor:pointer" onclick="scrollToAlertSection()" title="点击查看告警详情">{alerts['critical'] + alerts['warning']}</div>
    <div class="mc-sub">严重 {alerts['critical']} · 警告 {alerts['warning']} · 信息 {alerts['info']}</div>
  </div>
</div>"""


def _render_action_table(actions: list[dict]) -> str:
    """Render all active action items as a table with priority, type, and dependency info."""
    if not actions:
        return '<div class="checklist"><div class="checklist-item" style="color:#718096">暂无待执行事项</div></div>'

    today = date.today().strftime("%Y-%m-%d")
    rows = []
    for a in actions:
        status = a["_effective_status"]
        cls, label = _status_badge(status)
        section_label = {
            "compliance_remediation": "合规整改",
            "c_tranche_restructure": "C仓重组",
            "b_tranche_build": "B仓建仓",
            "thesis_maintenance": "Thesis维护",
        }.get(a["_section"], a["_section"])

        task_type = a.get("task_type", "")
        type_icon = _task_type_icon(task_type)
        urgency = a.get("_urgency", "")
        urgency_html = _urgency_badge(urgency) if urgency else ""

        dep_blocked = a.get("_dep_blocked", False)
        blocked_by = a.get("_blocked_by", [])

        amount = a.get("amount", 0)
        amount_str = _fmt_cny(amount) if amount else "—"
        planned = a.get("planned_date", "") or a.get("deadline", "") or "—"
        date_class = ""
        if status == "overdue":
            date_class = ' style="color:#c53030;font-weight:600"'

        # Dependency-blocked items get muted styling
        row_style = ""
        effective_label = label
        badge_cls = cls
        if dep_blocked:
            row_style = ' style="opacity:0.55"'
            effective_label = f"等待前置"
            badge_cls = "badge-blocked"

        # Notes: for blocked items, show condition; for dep-blocked, show missing deps
        notes_text = a.get('notes', a.get('progress_note', ''))
        if dep_blocked:
            notes_text = f"⛔ 等待: {', '.join(blocked_by)}"
        elif status == "blocked":
            notes_text = a.get("condition", notes_text)
        if not notes_text:
            notes_text = a.get("condition", "")

        rows.append(f"""<tr{row_style}>
      <td><span style="white-space:nowrap"><span class="badge {badge_cls}">{effective_label}</span>{urgency_html}</span></td>
      <td><span style="font-size:16px" title="{task_type}">{type_icon}</span></td>
      <td><span style="font-size:11px;color:#a0aec0">{section_label}</span></td>
      <td>{a.get('action', a.get('id', '—'))}</td>
      <td class="amount">{amount_str}</td>
      <td class="date-col"{date_class}>{planned}</td>
      <td style="font-size:12px;color:#a0aec0">{notes_text}</td>
    </tr>""")

    return f"""<table class="data-table">
    <thead><tr>
      <th>状态</th><th>类型</th><th>类别</th><th>行动</th><th>金额</th><th>计划日期</th><th>备注</th>
    </tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>"""


def _render_progress(progress: dict) -> str:
    """Render progress bars."""
    cards = []
    bar_colors = ["fill-blue", "fill-green", "fill-purple", "fill-orange"]
    for i, (key, p) in enumerate(progress.items()):
        if p["total"] == 0:
            continue
        color = bar_colors[i % len(bar_colors)]
        done_pct = p["pct"]
        in_prog_pct = (p["in_progress"] / p["total"] * 100) if p["total"] > 0 else 0
        total_pct = done_pct + in_prog_pct * 0.5

        cards.append(f"""<div class="progress-card">
      <div class="prog-label">
        <span>{p['label']}</span>
        <span class="prog-stat">{p['done']}/{p['total']} 完成 ({done_pct:.0f}%){f" + {p['in_progress']}项进行中" if p['in_progress'] > 0 else ""}</span>
      </div>
      <div class="progress-bar">
        <div class="fill {color}" style="width:{max(total_pct, 2)}%"></div>
      </div>
    </div>""")

    return f'<div class="progress-grid">{"".join(cards)}</div>' if cards else '<div style="color:#718096;font-size:13px">暂无进度数据</div>'


def _render_compliance(breaches: list[dict], tracker: dict | None = None) -> str:
    """Render compliance breaches from rules.yaml, with cross-reference to WBS tasks."""
    if not breaches:
        return '<div class="alert-box alert-info"><span class="alert-icon">&#x2705;</span> 无活跃违规事项</div>'

    # Build rule -> task mapping from compliance_remediation
    rule_task_map = {}
    if tracker:
        for item in tracker.get("compliance_remediation", []):
            if item.get("rule"):
                rule_task_map[item["rule"]] = item["id"]

    boxes = []
    for b in breaches:
        rule = b.get("rule", "")
        stock = b.get("stock_name", b.get("stock", ""))
        current = b.get("current_value", b.get("current", ""))
        threshold = b.get("threshold", "")
        days_left = _days_between(b.get("grace_period_expires", "")) if b.get("grace_period_expires") else 999
        status = b.get("status", "")

        if days_left <= 7:
            sev = "alert-critical"
            icon = "&#x1F534;"
        elif days_left <= 30:
            sev = "alert-warning"
            icon = "&#x1F7E1;"
        else:
            sev = "alert-info"
            icon = "&#x1F7E2;"

        rule_name = {
            "single_stock_max": "单股仓位超限",
            "theme_concentration": "主题集中度超限",
            "active_position_total": "C档总仓位超配",
        }.get(rule, rule)

        deadline_str = f"宽限期至 {b.get('grace_period_expires', '')}，剩余 {days_left} 天" if days_left < 999 else ""

        # Cross-reference to WBS remediation task
        related_id = rule_task_map.get(rule, "")
        related_html = ""
        if related_id:
            related_html = f'<div style="font-size:11px;margin-top:4px"><a href="javascript:scrollAndHighlight(\'task-{related_id}\')" style="color:#0066cc;text-decoration:none;font-weight:500">&#x1F517; 相关任务: {related_id} &#x2192;</a></div>'

        boxes.append(f"""<div class="alert-box {sev}">
      <span class="alert-icon">{icon}</span>
      <div>
        <strong>{rule_name}</strong> — {stock}：当前 {current}，阈值 {threshold}，状态：{status}
        {f'<div class="alert-deadline">{deadline_str}</div>' if deadline_str else ''}
        {f'<div style="font-size:12px;margin-top:3px;color:#718096">{b.get("notes","")}</div>' if b.get("notes") else ''}
        {related_html}
      </div>
    </div>""")

    return "\n".join(boxes)


def _render_alerts(alerts: dict) -> str:
    """Render expandable alert detail cards."""
    latest = alerts.get("latest", [])
    if not latest:
        return '<div style="color:#718096;font-size:13px;padding:12px 0">&#x2705; 今日无活跃告警</div>'

    cards = []
    for i, a in enumerate(latest):
        sev = a["severity"]
        sev_dot = {"critical": "dot-critical", "warning": "dot-warning", "info": "dot-info"}.get(sev, "dot-info")
        sev_label = {"critical": "严重", "warning": "警告", "info": "提示"}.get(sev, "提示")
        sev_icon = {"critical": "&#x1F534;", "warning": "&#x1F7E1;", "info": "&#x1F535;"}.get(sev, "&#x1F535;")
        stock_badge = f'<span class="alert-stock">{a["stock_code"]}</span>' if a.get("stock_code") else ""
        type_label = a.get("alert_type", "").replace("_", " ")

        cards.append(f"""<div class="alert-card">
  <div class="alert-card-header sev-{sev}" id="alert-header-{i}" onclick="toggleAlert({i})">
    <span class="alert-dot {sev_dot}"></span>
    <span class="alert-type-label">{sev_icon} {sev_label}</span>
    {stock_badge}
    <span class="alert-title">{a['title']}</span>
    <span class="alert-expand">&#x25BC;</span>
  </div>
  <div class="alert-card-body" id="alert-body-{i}">{a.get("snippet", a["title"])}</div>
</div>""")

    return "\n".join(cards)


def _render_pre_market(tracker: dict, events: list[dict]) -> str:
    """Render pre-market checklist."""
    checks = tracker.get("daily_checks", {}).get("pre_market", [])
    today = date.today().strftime("%Y-%m-%d")
    today_events = [e for e in events if e.get("date") == today]
    upcoming = [e for e in events if e.get("date") > today][:3]

    items = []
    for c in checks:
        items.append(f'<div class="checklist-item"><input type="checkbox">{c["action"]}</div>')

    if today_events:
        items.append('<div class="checklist-item" style="font-weight:600;color:#c53030">&#x1F4E2; 今日宏观事件：</div>')
        for e in today_events:
            items.append(f'<div class="checklist-item" style="padding-left:30px">· {e["date"]}: {e["event"]} — {e.get("impact","")}</div>')

    if upcoming:
        items.append('<div class="checklist-item" style="font-weight:600;color:#d69e2e">&#x1F4C5; 近期事件：</div>')
        for e in upcoming:
            items.append(f'<div class="checklist-item" style="padding-left:30px">· {e["date"]}: {e["event"]}</div>')

    if not items:
        items.append('<div class="checklist-item" style="color:#718096">无可用的盘前检查项</div>')

    return f'<div class="checklist">{"".join(items)}</div>'


def _render_post_market(tracker: dict) -> str:
    """Render post-market checklist."""
    checks = tracker.get("daily_checks", {}).get("post_market", [])
    items = []
    for c in checks:
        items.append(f'<div class="checklist-item"><input type="checkbox">{c["action"]}</div>')

    if not items:
        items.append('<div class="checklist-item" style="color:#718096">无可用的盘后检查项</div>')

    return f'<div class="checklist">{"".join(items)}</div>'


def _render_upcoming(tracker: dict) -> str:
    """Render upcoming events from the explicit upcoming_events list only."""
    events = tracker.get("upcoming_events", [])
    today = date.today().strftime("%Y-%m-%d")

    rows = []
    for e in events:
        if e["date"] < today:
            continue
        days_left = _days_between(e["date"], today)
        urgent = days_left <= 3
        date_class = ' urgent' if urgent else ''
        days_str = f' ({days_left}天后)' if days_left >= 0 else ''

        prefix = ''
        if urgent:
            prefix = '&#x1F7E1; '
        if days_left < 0:
            prefix = '&#x1F534; '

        rows.append(f"""<div class="event-row">
      <span class="event-date{date_class}">{e['date']}{days_str}</span>
      <span>{prefix}{e['event']}</span>
      <span style="font-size:12px;color:#a0aec0">{e.get('impact','')}</span>
    </div>""")

    return "\n".join(rows[:15]) if rows else '<div style="color:#718096;font-size:13px">暂无近期事件</div>'


# ── Main ─────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="每日战情室 Dashboard 生成器")
    parser.add_argument("--pre-market", action="store_true", help="盘前模式")
    parser.add_argument("--post-market", action="store_true", help="盘后模式")
    args = parser.parse_args()

    if args.pre_market and args.post_market:
        print("不能同时使用 --pre-market 和 --post-market")
        sys.exit(1)

    mode = "standard"
    if args.pre_market:
        mode = "pre-market"
    elif args.post_market:
        mode = "post-market"

    # Load data
    tracker = load_tracker()
    rules = load_rules()
    capital = load_capital()
    snapshot = load_portfolio_snapshot()
    alerts = load_alerts_summary()
    events = load_macro_events()
    goals = load_goals(tracker)
    task_lookup = _build_task_lookup(tracker)

    # Render
    html = render(tracker, rules, capital, snapshot, alerts, events, mode, goals, task_lookup)

    # Write
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"✅ 每日战情室已生成: {OUTPUT_PATH}")
    print(f"   模式: {mode}")
    print(f"   告警: critical={alerts['critical']} warning={alerts['warning']} info={alerts['info']}")
    if alerts["latest"]:
        print("   ➕ 告警详情已嵌入 HTML，点击战情室顶部告警徽章查看")

    actions = get_today_actions(tracker)
    pending_count = sum(1 for a in actions if a["_effective_status"] in ("pending", "in_progress", "overdue"))
    dep_blocked_count = sum(1 for a in actions if a.get("_dep_blocked"))
    print(f"   待执行事项: {pending_count} 项 (其中 {dep_blocked_count} 项等待前置依赖)")


if __name__ == "__main__":
    main()
