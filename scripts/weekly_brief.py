#!/usr/bin/env python3
"""
weekly_brief.py — 每周事件日历简报

用法:
    python scripts/weekly_brief.py

职责:
  1. 汇总本周（周一至今）触发的告警
  2. 显示当前持仓状态摘要
  3. 列出下周关键事件（财报、决策截止日等）
  4. 输出到 reviews/weekly/YYYY-WNN.md
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    ROOT_DIR, load_rules, load_holdings, load_etf_config, load_capital,
    load_thesis_scores, _fmt_pct,
)

WEEKLY_DIR = ROOT_DIR / "reviews" / "weekly"
ALERTS_DIR = ROOT_DIR / "alerts"
TRADES_DIR = ROOT_DIR / "trades"
WEEKLY_DIR.mkdir(parents=True, exist_ok=True)


# ── Date helpers ──────────────────────────────────────────────────────

def week_range(today: date) -> tuple[date, date]:
    """返回本周一和本周日。"""
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def iso_week_label(today: date) -> str:
    return today.strftime("%Y-W%W")


# ── Alert summary ─────────────────────────────────────────────────────

def summarise_alerts(monday: date, today: date) -> list[dict]:
    """扫描 alerts/ 目录，返回本周告警列表。"""
    alerts = []
    if not ALERTS_DIR.exists():
        return alerts
    for f in sorted(ALERTS_DIR.glob("*.md")):
        # 文件名格式: YYYY-MM-DD_CODE_TYPE.md
        parts = f.stem.split("_", 1)
        if not parts:
            continue
        try:
            file_date = datetime.strptime(parts[0], "%Y-%m-%d").date()
        except ValueError:
            continue
        if monday <= file_date <= today:
            alerts.append({"date": file_date, "file": f.name, "stem": f.stem})
    return alerts


# ── Decision deadlines ────────────────────────────────────────────────

def upcoming_deadlines(today: date, lookahead_days: int = 14) -> list[dict]:
    """扫描 trades/decision_*.md，提取截止日期。"""
    import re
    deadlines = []
    if not TRADES_DIR.exists():
        return deadlines

    deadline_pattern = re.compile(
        r"(?:截止|最晚|兜底|必须完成)[^\n]*?(\d{4}-\d{2}-\d{2})"
    )
    title_pattern = re.compile(r"^#\s+(.+)$", re.MULTILINE)

    for f in sorted(TRADES_DIR.glob("decision_*.md")):
        text = f.read_text(encoding="utf-8")
        title_m = title_pattern.search(text)
        title = title_m.group(1)[:50] if title_m else f.stem

        for m in deadline_pattern.finditer(text):
            try:
                dl = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except ValueError:
                continue
            if today <= dl <= today + timedelta(days=lookahead_days):
                deadlines.append({
                    "date": dl,
                    "days_left": (dl - today).days,
                    "file": f.stem,
                    "title": title,
                    "context": m.group(0)[:60],
                })

    return sorted(deadlines, key=lambda x: x["date"])


# ── Holdings snapshot ─────────────────────────────────────────────────

def holdings_summary(holdings, thesis_scores) -> list[str]:
    lines = []
    for h in sorted(holdings, key=lambda x: float(x["shares"]) * float(x["cost_price"]), reverse=True):
        code = h["code"]
        cost = float(h["cost_price"])
        price = float(h["current_price"])
        pnl_pct = (price - cost) / cost * 100
        t = thesis_scores.get(code, {})
        thesis_str = f"{t.get('rating', '')} {t.get('score', '')}" if t else "—"
        flag = "🔴" if pnl_pct <= -20 else ("🟡" if pnl_pct <= -10 else "🟢")
        lines.append(
            f"| {code} | {h['name']} | ¥{price:.2f} | {pnl_pct:+.1f}% {flag} | {thesis_str} |"
        )
    return lines


# ── Report ────────────────────────────────────────────────────────────

def generate_report(today: date, monday: date, sunday: date,
                    holdings, thesis_scores, alerts, deadlines) -> str:
    week_label = iso_week_label(today)
    lines = [
        f"# 每周简报 — {week_label}（{monday} ~ {sunday}）",
        f"",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"",
    ]

    # ── 本周告警汇总 ──
    lines += [f"## 本周告警（{monday} ~ {today}）", ""]
    if alerts:
        lines.append(f"共 {len(alerts)} 条告警：")
        lines.append("")
        for a in alerts:
            lines.append(f"- `{a['date']}` {a['stem']}")
    else:
        lines.append("✅ 本周无告警触发")
    lines.append("")

    # ── 持仓状态 ──
    lines += [
        "## 持仓状态快照",
        "",
        "| 代码 | 名称 | 现价 | 盈亏 | Thesis |",
        "|------|------|------|------|--------|",
    ]
    lines += holdings_summary(holdings, thesis_scores)
    lines.append("")

    # ── 近 14 天截止事项 ──
    lines += [f"## 近 14 天截止事项", ""]
    if deadlines:
        lines.append(f"| 截止日 | 剩余天数 | 决策文件 | 事项 |")
        lines.append(f"|--------|---------|---------|------|")
        for d in deadlines:
            urgency = "🔴" if d["days_left"] <= 3 else ("🟡" if d["days_left"] <= 7 else "🟢")
            lines.append(
                f"| {d['date']} | {urgency} {d['days_left']}天 "
                f"| {d['file']} | {d['context']} |"
            )
    else:
        lines.append("✅ 近 14 天无截止事项")
    lines.append("")

    # ── 下周关注事项（手动维护区） ──
    lines += [
        "## 下周关注事项",
        "",
        "> 以下为手动维护区，每周更新",
        "",
        "- [ ] （请填写下周关键事件：财报、重大公告、宏观数据等）",
        "",
        "---",
        f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
    ]

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────

def main():
    today = date.today()
    monday, sunday = week_range(today)
    week_label = iso_week_label(today)

    print("加载配置...")
    holdings = load_holdings()
    thesis_scores = load_thesis_scores()

    print("扫描告警...")
    alerts = summarise_alerts(monday, today)

    print("扫描截止事项...")
    deadlines = upcoming_deadlines(today)

    report = generate_report(today, monday, sunday, holdings, thesis_scores, alerts, deadlines)

    out_path = WEEKLY_DIR / f"{week_label}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\n✅ 周报已写入 {out_path}")
    print(report)


if __name__ == "__main__":
    main()
