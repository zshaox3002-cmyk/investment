#!/usr/bin/env python3
"""
monthly_update.py — 月度 thesis 评分卡更新

用法:
    python scripts/monthly_update.py

职责:
  1. 检查所有 thesis 文件的最后更新日期
  2. 标记超过 90 天未更新的标的（rules.yaml 要求）
  3. 检查 L2/L3 告警触发的 7 天更新截止
  4. 生成更新清单，输出到 reviews/monthly/
"""

import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    ROOT_DIR, load_rules, load_holdings, load_thesis_scores,
    _parse_frontmatter, _fmt_pct,
)

MONTHLY_DIR = ROOT_DIR / "reviews" / "monthly"
ALERTS_DIR = ROOT_DIR / "alerts"
THESES_DIR = ROOT_DIR / "theses"
MONTHLY_DIR.mkdir(parents=True, exist_ok=True)

# 从 rules.yaml 读取，带后备值
def _load_thesis_config():
    try:
        rules = load_rules()
        sr = rules.get("stock_rules", {})
        update_days = sr.get("thesis_maintenance", {}).get("update_frequency_days", 90)
        deadline_days = sr.get("stop_loss", {}).get("level_2_review", {}).get("decision_deadline_days", 7)
        return update_days, deadline_days
    except Exception:
        return 90, 7

THESIS_UPDATE_DAYS, DECISION_DEADLINE_DAYS = _load_thesis_config()


# ── Thesis file analysis ──────────────────────────────────────────────

def _extract_last_updated(text: str, filepath: Path) -> date | None:
    """从 thesis 文件中提取最后更新日期。"""
    # 1. frontmatter 中的 last_updated 字段
    fm = _parse_frontmatter(text)
    if fm and fm.get("last_updated"):
        try:
            return datetime.strptime(str(fm["last_updated"]), "%Y-%m-%d").date()
        except ValueError:
            pass

    # 2. 文件中的"更新日期"/"最后更新"字段
    m = re.search(r"(?:更新日期|最后更新|last.updated)[：:\s]+(\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass

    # 3. fallback: 文件修改时间
    try:
        mtime = filepath.stat().st_mtime
        return datetime.fromtimestamp(mtime).date()
    except Exception:
        return None


def analyse_theses(holdings, today: date) -> list[dict]:
    """分析所有持仓的 thesis 状态。"""
    holding_codes = {h["code"] for h in holdings}
    results = []

    for h in holdings:
        code = h["code"]
        thesis_path = THESES_DIR / f"{code}_thesis.md"

        if not thesis_path.exists():
            results.append({
                "code": code,
                "name": h["name"],
                "status": "missing",
                "last_updated": None,
                "days_since": None,
                "score": None,
                "rating": None,
                "needs_update": True,
                "urgency": "critical",
                "reason": "thesis 文件不存在",
            })
            continue

        text = thesis_path.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text) or {}
        last_updated = _extract_last_updated(text, thesis_path)
        days_since = (today - last_updated).days if last_updated else None

        needs_update = days_since is not None and days_since > THESIS_UPDATE_DAYS
        urgency = "ok"
        reason = ""

        if days_since is None:
            urgency = "warning"
            reason = "无法确定更新日期"
            needs_update = True
        elif days_since > THESIS_UPDATE_DAYS:
            urgency = "warning"
            reason = f"已 {days_since} 天未更新（上限 {THESIS_UPDATE_DAYS} 天）"
        else:
            reason = f"距上次更新 {days_since} 天"

        results.append({
            "code": code,
            "name": h["name"],
            "status": "exists",
            "last_updated": last_updated,
            "days_since": days_since,
            "score": fm.get("score"),
            "rating": fm.get("rating", ""),
            "needs_update": needs_update,
            "urgency": urgency,
            "reason": reason,
        })

    # 检查有 thesis 但不在持仓中的（孤立文件）
    for f in THESES_DIR.glob("*_thesis.md"):
        if f.name.startswith("_"):
            continue
        code = f.stem.replace("_thesis", "")
        if code not in holding_codes:
            results.append({
                "code": code,
                "name": "（已不在持仓）",
                "status": "orphan",
                "last_updated": None,
                "days_since": None,
                "score": None,
                "rating": None,
                "needs_update": False,
                "urgency": "info",
                "reason": "标的已不在持仓，thesis 可归档",
            })

    return results


# ── L2/L3 alert deadlines ─────────────────────────────────────────────

def check_alert_deadlines(today: date) -> list[dict]:
    """检查 L2/L3 告警触发的 7 天 thesis 更新截止。"""
    deadlines = []
    if not ALERTS_DIR.exists():
        return deadlines

    for f in sorted(ALERTS_DIR.glob("*.md")):
        if "drawdown_l2" not in f.stem and "drawdown_l3" not in f.stem:
            continue
        parts = f.stem.split("_", 1)
        try:
            alert_date = datetime.strptime(parts[0], "%Y-%m-%d").date()
        except ValueError:
            continue
        deadline = alert_date + timedelta(days=DECISION_DEADLINE_DAYS)
        days_left = (deadline - today).days
        if days_left >= 0:
            deadlines.append({
                "alert_file": f.name,
                "alert_date": alert_date,
                "deadline": deadline,
                "days_left": days_left,
            })

    return sorted(deadlines, key=lambda x: x["deadline"])


# ── Report ────────────────────────────────────────────────────────────

def generate_report(today: date, theses: list[dict], alert_deadlines: list[dict]) -> str:
    month_label = today.strftime("%Y-%m")
    lines = [
        f"# 月度 Thesis 更新清单 — {month_label}",
        f"",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"",
    ]

    # ── 紧急：L2/L3 告警 7 天截止 ──
    if alert_deadlines:
        lines += ["## 🔴 紧急：告警触发的 Thesis 更新截止", ""]
        lines.append("| 告警文件 | 告警日期 | 截止日 | 剩余天数 |")
        lines.append("|---------|---------|--------|---------|")
        for d in alert_deadlines:
            urgency = "🔴" if d["days_left"] <= 2 else ("🟡" if d["days_left"] <= 5 else "🟢")
            lines.append(
                f"| {d['alert_file']} | {d['alert_date']} "
                f"| {d['deadline']} | {urgency} {d['days_left']} 天 |"
            )
        lines.append("")

    # ── 需要更新的 thesis ──
    needs_update = [t for t in theses if t["needs_update"] and t["status"] != "orphan"]
    ok_theses = [t for t in theses if not t["needs_update"] and t["status"] == "exists"]
    missing = [t for t in theses if t["status"] == "missing"]
    orphans = [t for t in theses if t["status"] == "orphan"]

    if missing:
        lines += ["## 🔴 缺失 Thesis（必须创建）", ""]
        for t in missing:
            lines.append(f"- **{t['code']} {t['name']}** — {t['reason']}")
        lines.append("")

    if needs_update:
        lines += ["## 🟡 需要更新的 Thesis", ""]
        lines.append("| 代码 | 名称 | 上次更新 | 已过天数 | 评分 | 原因 |")
        lines.append("|------|------|---------|---------|------|------|")
        for t in sorted(needs_update, key=lambda x: x["days_since"] or 999, reverse=True):
            score_str = f"{t['rating']} {t['score']}" if t["score"] else "—"
            lines.append(
                f"| {t['code']} | {t['name']} | {t['last_updated'] or '未知'} "
                f"| {t['days_since'] or '?'} 天 | {score_str} | {t['reason']} |"
            )
        lines.append("")

    if ok_theses:
        lines += ["## ✅ Thesis 状态正常", ""]
        lines.append("| 代码 | 名称 | 上次更新 | 已过天数 | 评分 |")
        lines.append("|------|------|---------|---------|------|")
        for t in ok_theses:
            score_str = f"{t['rating']} {t['score']}" if t["score"] else "—"
            lines.append(
                f"| {t['code']} | {t['name']} | {t['last_updated']} "
                f"| {t['days_since']} 天 | {score_str} |"
            )
        lines.append("")

    if orphans:
        lines += ["## ℹ️ 孤立 Thesis（标的已不在持仓）", ""]
        for t in orphans:
            lines.append(f"- `{t['code']}` — {t['reason']}")
        lines.append("")

    lines += ["---", f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"]
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────

def main():
    today = date.today()
    month_label = today.strftime("%Y-%m")

    print("加载持仓...")
    holdings = load_holdings()

    print("分析 thesis 文件...")
    theses = analyse_theses(holdings, today)

    print("检查告警截止日...")
    alert_deadlines = check_alert_deadlines(today)

    report = generate_report(today, theses, alert_deadlines)

    out_path = MONTHLY_DIR / f"{month_label}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\n✅ 月度更新清单已写入 {out_path}")
    print(report)


if __name__ == "__main__":
    main()
