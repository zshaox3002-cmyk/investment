#!/usr/bin/env python3
"""
quarterly_review.py — 季度全量诊断

用法:
    python scripts/quarterly_review.py

职责:
  1. 运行仓位偏离检查（rebalance_check）
  2. 运行 thesis 更新清单（monthly_update）
  3. 汇总本季度告警
  4. 检查所有 rules.yaml 合规状态
  5. 生成季度诊断报告，输出到 reviews/quarterly/
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    ROOT_DIR, load_rules, load_holdings, load_etf_config, load_capital,
    load_thesis_scores, fetch_price, calc_holding, _fmt_pct,
    check_alerts, check_etf_alerts,
)
from rebalance_check import build_positions, analyse, generate_report as rebalance_report
from monthly_update import analyse_theses, check_alert_deadlines, generate_report as monthly_report

QUARTERLY_DIR = ROOT_DIR / "reviews" / "quarterly"
ALERTS_DIR = ROOT_DIR / "alerts"
QUARTERLY_DIR.mkdir(parents=True, exist_ok=True)


# ── Quarter helpers ───────────────────────────────────────────────────

def quarter_label(today: date) -> str:
    q = (today.month - 1) // 3 + 1
    return f"{today.year}-Q{q}"


def quarter_start(today: date) -> date:
    q = (today.month - 1) // 3
    return date(today.year, q * 3 + 1, 1)


# ── Alert summary ─────────────────────────────────────────────────────

def summarise_quarter_alerts(q_start: date, today: date) -> dict:
    """统计本季度告警数量和类型分布。"""
    counts: dict[str, int] = {}
    total = 0
    if not ALERTS_DIR.exists():
        return {"total": 0, "by_type": {}}

    for f in ALERTS_DIR.glob("*.md"):
        parts = f.stem.split("_", 1)
        try:
            file_date = datetime.strptime(parts[0], "%Y-%m-%d").date()
        except ValueError:
            continue
        if q_start <= file_date <= today:
            alert_type = parts[1] if len(parts) > 1 else "unknown"
            counts[alert_type] = counts.get(alert_type, 0) + 1
            total += 1

    return {"total": total, "by_type": counts}


# ── Compliance check ──────────────────────────────────────────────────

def check_compliance(rules: dict, c_positions: list[dict], total_c: float) -> list[dict]:
    """检查所有 rules.yaml 合规状态，返回违规列表。"""
    violations = []
    pr = rules.get("portfolio_rules", {})

    # 单股仓位
    single_max = pr.get("concentration", {}).get("single_stock_max", {}).get("threshold", 0.25)
    for p in c_positions:
        ratio = p["market_value"] / total_c if total_c > 0 else 0
        if ratio > single_max:
            violations.append({
                "rule": "single_stock_max",
                "code": p["code"],
                "name": p["name"],
                "current": ratio,
                "threshold": single_max,
                "severity": "critical",
            })

    # active_breaches
    for breach in rules.get("active_breaches", []):
        if breach.get("status") not in ("已整改", "已归零"):
            violations.append({
                "rule": breach.get("rule", "unknown"),
                "code": breach.get("stock", breach.get("theme", "")),
                "name": breach.get("stock_name", breach.get("theme", "")),
                "current": breach.get("current_value"),
                "threshold": breach.get("threshold"),
                "severity": "critical",
                "note": breach.get("notes", ""),
            })

    return violations


# ── Full report ───────────────────────────────────────────────────────

def generate_quarterly_report(today: date, q_label: str, q_start: date,
                               c_positions, etf_positions, stats,
                               theses, alert_deadlines, alert_summary,
                               violations, rules) -> str:
    lines = [
        f"# 季度全量诊断 — {q_label}",
        f"",
        f"> 诊断日期: {today}  |  季度起始: {q_start}",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"",
        f"---",
        f"",
    ]

    # ── 1. 合规状态 ──
    lines += ["## 一、合规状态", ""]
    if violations:
        lines.append(f"❌ 发现 {len(violations)} 项违规：")
        lines.append("")
        lines.append("| 规则 | 标的 | 当前值 | 阈值 | 严重级别 |")
        lines.append("|------|------|--------|------|---------|")
        for v in violations:
            cur = _fmt_pct(v["current"]) if isinstance(v["current"], float) else str(v["current"])
            thr = _fmt_pct(v["threshold"]) if isinstance(v["threshold"], float) else str(v["threshold"])
            lines.append(f"| {v['rule']} | {v['name']}({v['code']}) | {cur} | {thr} | {v['severity']} |")
    else:
        lines.append("✅ 无合规违规")
    lines.append("")

    # ── 2. 仓位偏离 ──
    lines += ["## 二、仓位偏离检查", ""]
    lines.append(rebalance_report(c_positions, etf_positions, stats))
    lines.append("")

    # ── 3. Thesis 状态 ──
    lines += ["## 三、Thesis 更新状态", ""]
    missing = [t for t in theses if t["status"] == "missing"]
    needs_update = [t for t in theses if t["needs_update"] and t["status"] != "orphan"]
    ok_count = len([t for t in theses if not t["needs_update"] and t["status"] == "exists"])

    lines.append(f"- 缺失 thesis: {len(missing)} 只")
    lines.append(f"- 需要更新: {len(needs_update)} 只")
    lines.append(f"- 状态正常: {ok_count} 只")
    lines.append("")

    if missing or needs_update:
        lines.append("| 代码 | 名称 | 状态 | 原因 |")
        lines.append("|------|------|------|------|")
        for t in missing + needs_update:
            lines.append(f"| {t['code']} | {t['name']} | {t['status']} | {t['reason']} |")
    lines.append("")

    # ── 4. 本季度告警汇总 ──
    lines += [f"## 四、本季度告警汇总（{q_start} ~ {today}）", ""]
    lines.append(f"共触发 **{alert_summary['total']}** 条告警")
    if alert_summary["by_type"]:
        lines.append("")
        lines.append("| 告警类型 | 次数 |")
        lines.append("|---------|------|")
        for atype, cnt in sorted(alert_summary["by_type"].items(), key=lambda x: x[1], reverse=True):
            lines.append(f"| {atype} | {cnt} |")
    lines.append("")

    # ── 5. 季度行动清单 ──
    lines += ["## 五、季度行动清单", ""]
    action_items = []

    for v in violations:
        action_items.append(f"🔴 【合规】{v['name']}({v['code']}) {v['rule']} 违规，需整改")
    for t in missing:
        action_items.append(f"🔴 【Thesis】{t['code']} {t['name']} 缺失 thesis，需创建")
    for t in needs_update:
        action_items.append(f"🟡 【Thesis】{t['code']} {t['name']} 需更新（{t['reason']}）")
    for d in alert_deadlines:
        action_items.append(f"🟡 【告警截止】{d['alert_file']} 截止 {d['deadline']}（剩 {d['days_left']} 天）")

    if action_items:
        for item in action_items:
            lines.append(f"- [ ] {item}")
    else:
        lines.append("✅ 无待处理行动项")
    lines.append("")

    lines += ["---", f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"]
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────

def main():
    today = date.today()
    q_label = quarter_label(today)
    q_start = quarter_start(today)

    print(f"季度全量诊断 — {q_label}")
    print("加载配置...")
    rules = load_rules()
    holdings = load_holdings()
    etf_configs = load_etf_config()
    capital = load_capital()

    print("拉取行情...")
    c_positions, etf_positions = build_positions(holdings, etf_configs, do_fetch=True)
    stats = analyse(rules, c_positions, etf_positions, capital)

    total_c = stats["total_c"]
    total_b = stats["total_b"]

    print("分析 thesis...")
    theses = analyse_theses(holdings, today)
    alert_deadlines = check_alert_deadlines(today)

    print("汇总告警...")
    alert_summary = summarise_quarter_alerts(q_start, today)

    print("检查合规...")
    violations = check_compliance(rules, c_positions, total_c)

    report = generate_quarterly_report(
        today, q_label, q_start,
        c_positions, etf_positions, stats,
        theses, alert_deadlines, alert_summary,
        violations, rules,
    )

    out_path = QUARTERLY_DIR / f"{q_label}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\n✅ 季度报告已写入 {out_path}")
    print(report)


if __name__ == "__main__":
    main()
