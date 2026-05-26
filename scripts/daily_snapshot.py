#!/usr/bin/env python3
"""
daily_snapshot.py — 每日收盘后数据快照 + 告警检查

用法:
    python scripts/daily_snapshot.py

依赖:
    pip install pyyaml

职责:
  1. 读取 config/rules.yaml 风控配置
  2. 读取 config/holdings.csv (C 仓位)、config/core_etf.csv (B 仓位)、
     config/cash_positions.csv (A 档现金/债券 + D 档美团RSU)
  3. 拉取所有持仓最新价、涨跌幅
  4. 计算累计盈亏、仓位占比
  5. 写入 Markdown 报告到 reviews/daily/{YYYY-MM-DD}.md
  6. 追加时间序列数据到 data/portfolio_ts.csv
  7. 检查告警阈值，触发时调用 alert_monitor.py
"""

import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from common import (
    ROOT_DIR, CONFIG_DIR, SCRIPTS_DIR,
    RULES_PATH, HOLDINGS_PATH, CORE_ETF_PATH, CASH_POSITIONS_PATH,
    CAPITAL_PATH, THESES_DIR,
    ACTION_SEVERITY_MAP,
    load_rules, load_holdings, load_etf_config, load_capital,
    load_cash_positions, load_thesis_scores,
    _fmt_pct, _parse_frontmatter, _is_ashare,
    _tencent_code, _parse_tencent_line, fetch_prices_batch, fetch_price,
    calc_holding, _get_severity,
    check_alerts, check_etf_alerts, check_meituan_rsu_alerts,
    _check_thesis_analysis_sync,
    append_portfolio_ts,
)

REVIEWS_DIR = ROOT_DIR / "reviews" / "daily"
ALERTS_DIR = ROOT_DIR / "alerts"
REVIEWS_DIR.mkdir(parents=True, exist_ok=True)


# ── Report Generation ────────────────────────────────────────────────

def generate_report(date_str: str, rules: dict, etf_positions: list[dict], c_positions: list[dict],
                    total_c_cost: float, total_c_value: float, total_etf_cost: float,
                    total_etf_value: float, alerts: list[dict],
                    c_allocated: float = 0, thesis_scores: Optional[dict] = None,
                    cash_positions: Optional[list] = None, capital: Optional[dict] = None,
                    stale_analyses: Optional[list] = None,
                    meituan_price: float = 0) -> str:
    """生成 Markdown 日报。"""
    lines = []
    lines.append(f"# 每日快照 — {date_str}")
    lines.append("")

    # ── A/D 档数据 ──
    cash_rows = cash_positions or []
    a_rows = [r for r in cash_rows if r["code"] not in ("MTRSU",)]
    d_rows = [r for r in cash_rows if r["code"] == "MTRSU"]
    total_a_value = sum(r["current_value"] for r in a_rows)
    total_d_value = sum(r["current_value"] for r in d_rows)

    # ── 总资产概览 ──
    total_all = total_a_value + total_etf_value + total_c_value + total_d_value
    rebalance_base = capital.get("rebalance_base_capital", 0) if capital else 0
    target_b = capital.get("target_b", 0) if capital else 0
    target_c = capital.get("target_c", 0) if capital else 0
    target_a = capital.get("target_a", 0) if capital else 0

    lines.append("## 总资产概览")
    lines.append("| 档位 | 市值 | 占总资产 | 目标 | 状态 |")
    lines.append("|------|------|---------|------|------|")
    if total_all > 0:
        a_pct = total_a_value / total_all
        b_pct = total_etf_value / total_all
        c_pct = total_c_value / total_all
        d_pct = total_d_value / total_all
        lines.append(f"| A档（现金/债券） | {total_a_value:,.0f} | {a_pct*100:.2f}% | {target_a:,.0f} | {'✅' if total_a_value >= target_a * 0.9 else '🔴'} |")
        lines.append(f"| B档（核心ETF） | {total_etf_value:,.0f} | {b_pct*100:.2f}% | {target_b:,.0f} | {'✅' if total_etf_value >= target_b * 0.9 else '🔴'} |")
        lines.append(f"| C档（主动选股） | {total_c_value:,.0f} | {c_pct*100:.2f}% | {target_c:,.0f} | {'✅' if total_c_value <= target_c * 1.1 else '🟡'} |")
        lines.append(f"| D档（美团RSU）⚠排除再平衡基准 | {total_d_value:,.0f} | {d_pct*100:.2f}% | — | 按计划减持 |")
        lines.append(f"| **合计** | **{total_all:,.0f}** | **100%** | — | — |")
    lines.append(f"")
    if rebalance_base > 0:
        lines.append(f"> 再平衡基准（排除D档）：¥{rebalance_base:,.0f}，美团RSU第二档完成后（2027-11）重新纳入")
        # RSU 解锁倒计时
        meituan_review = capital.get("meituan_rsu_review_date", "2027-11-01") if capital else "2027-11-01"
        try:
            review_date = datetime.strptime(meituan_review, "%Y-%m-%d").date()
            days_to_review = (review_date - datetime.now().date()).days
            if days_to_review > 0:
                lines.append(f"> 美团RSU第二档解锁倒计时: **{days_to_review} 天**（{meituan_review}）")
            else:
                lines.append(f"> 美团RSU第二档已到期（{meituan_review}），请更新再平衡基准纳入D档")
        except ValueError:
            pass
        lines.append("")

    # ── A 档明细 ──
    if a_rows:
        lines.append("## A 档 — 现金/债券")
        lines.append("| 代码 | 名称 | 类型 | 市值 | 年化收益 | 备注 |")
        lines.append("|------|------|------|------|---------|------|")
        for r in a_rows:
            lines.append(f"| {r['code']} | {r['name']} | {r['type']} | {r['current_value']:,.0f} | {r['annual_rate']} | {r['notes']} |")
        lines.append("")

    # ── RSU 状态（如有美团实时价格） ──
    if d_rows and meituan_price > 0:
        rsu = d_rows[0]
        rsu_current = rsu["balance"] * meituan_price
        rsu_change = (meituan_price - capital.get("meituan_rsu_price", meituan_price)) / capital.get("meituan_rsu_price", meituan_price) if capital.get("meituan_rsu_price", 0) > 0 else 0
        lines.append("## D 档 — 美团 RSU")
        lines.append(f"| 项目 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 持有股数 | {rsu['balance']:,.0f} 股 |")
        lines.append(f"| 上次记录价 | HK${capital.get('meituan_rsu_price', 0):.2f} |")
        lines.append(f"| 实时股价 | HK${meituan_price:.2f} |")
        lines.append(f"| 当前市值 | HK${rsu_current:,.0f} |")
        lines.append(f"| 股价变动 | {_fmt_pct(rsu_change)} |")
        lines.append("")

    # ── 汇总（B+C） ──
    total_bc_value = total_c_value + total_etf_value
    lines.append("## B+C 账户概览")
    lines.append(f"| 项目 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| C 仓位市值 | {total_c_value:,.2f} |")
    if c_allocated > 0:
        deploy_rate = total_c_value / c_allocated
        lines.append(f"| C 仓位分配额 | {c_allocated:,.2f} |")
        lines.append(f"| C 仓位部署率 | {deploy_rate*100:.1f}% |")
    lines.append(f"| B 仓位 ETF 市值 | {total_etf_value:,.2f} |")
    lines.append(f"| 总市值 (B+C) | {total_bc_value:,.2f} |")
    if total_c_cost > 0:
        total_pnl = total_c_value - total_c_cost
        total_pnl_pct = total_pnl / total_c_cost
        lines.append(f"| C 仓位总盈亏 | {total_pnl:+,.2f} ({_fmt_pct(total_pnl_pct)}) |")
    if total_etf_cost > 0:
        total_etf_pnl = total_etf_value - total_etf_cost
        total_etf_pnl_pct = total_etf_pnl / total_etf_cost
        lines.append(f"| B 仓位总盈亏 | {total_etf_pnl:+,.2f} ({_fmt_pct(total_etf_pnl_pct)}) |")
    lines.append("")

    # ── B 仓位 ETF ──
    lines.append("## B 仓位 — ETF 组合")
    lines.append(f"| 代码 | 名称 | 成本价 | 现价 | 份额 | 市值 | 盈亏 | 盈亏比 | 目标比例 | 实际比例 | 偏离度 | 涨跌幅 |")
    lines.append(f"|------|------|--------|------|------|------|------|--------|----------|----------|--------|--------|")
    for e in etf_positions:
        pnl = e.get("pnl", 0)
        pnl_pct = _fmt_pct(e.get("pnl_pct", 0))
        ratio = e.get("current_ratio", 0)
        drift = e.get("drift", "N/A")
        change = _fmt_pct(e.get("change_pct")) if e.get("change_pct") is not None else "N/A"
        price_str = f"{e.get('price', 0):.3f}" if e.get("price") else "N/A"
        cost_str = f"{e.get('cost_price', 0):.3f}" if e.get("cost_price") else "N/A"
        shares_str = f"{e.get('shares', 0):.0f}"
        mv_str = f"{e.get('market_value', 0):,.2f}" if e.get("market_value") else "N/A"
        lines.append(f"| {e['code']} | {e['name']} | {cost_str} | {price_str} | {shares_str} | "
                     f"{mv_str} | {pnl:+,.2f} | {pnl_pct} | "
                     f"{_fmt_pct(e['target_ratio'])} | {_fmt_pct(ratio)} | {drift} | {change} |")
    lines.append("")

    # ── C 仓位 ──
    ts = thesis_scores or {}
    lines.append("## C 仓位 — 主动选股")
    lines.append(f"| 代码 | 名称 | 市场 | 行业 | 成本价 | 现价 | 股数 | 市值 | 盈亏 | 盈亏比 | 占C已投 | 占C分配 | thesis | 涨跌幅 |")
    lines.append(f"|------|------|------|------|--------|------|------|------|------|--------|----------|----------|--------|--------|")
    for p in sorted(c_positions, key=lambda x: x["market_value"], reverse=True):
        ratio_inv = p["market_value"] / total_c_value if total_c_value else 0
        ratio_alloc = f"{_fmt_pct(p['market_value'] / c_allocated)}" if c_allocated > 0 else "N/A"
        t = ts.get(p["code"], {})
        thesis_str = f"{t.get('rating','')} {t.get('score','')}" if t else "-"
        change_pct = p["quote"].get("change_pct") if p.get("quote") else None
        change = _fmt_pct(change_pct) if change_pct is not None else "N/A"
        lines.append(f"| {p['code']} | {p['name']} | {p.get('market','A')} | {p.get('industry','')} | "
                     f"{p['cost_price']:.3f} | {p['current_price']:.3f} | {p['shares']:.0f} | "
                     f"{p['market_value']:,.2f} | {p['pnl']:+,.2f} | "
                     f"{_fmt_pct(p['pnl_pct'])} | {_fmt_pct(ratio_inv)} | {ratio_alloc} | {thesis_str} | {change} |")
    lines.append("")

    # ── 告警状态 ──
    lines.append("## 告警状态")
    if not alerts:
        lines.append("✅ 无告警触发")
    else:
        for a in alerts:
            icon = {"critical": "🔴", "warning": "🟡", "info": "🟢"}.get(a["severity"], "⚪")
            lines.append(f"- {icon} **[{a['severity'].upper()}]** {a['message']}")
            code = a.get("code", "")
            if code and ts and code in ts and ts[code].get("alert_context"):
                lines.append(f"  > 💡 Thesis: {ts[code]['alert_context']}")
    lines.append("")

    # ── Thesis 同步检查 ──
    if stale_analyses:
        lines.append("## ⚠️ 待重新审视的分析")
        lines.append("> 以下 agenda_analysis 引用的 thesis 已更新，分析内容可能过期，建议重新审视策略。")
        lines.append("")
        lines.append("| 分析文件 | Thesis 代码 | Thesis 更新 | 分析日期 | 新评分 | Thesis 上下文 |")
        lines.append("|----------|-------------|-------------|----------|--------|-------------|")
        for s in stale_analyses:
            ctx = s.get("alert_context", "")[:60] + ("..." if len(s.get("alert_context", "")) > 60 else "")
            lines.append(
                f"| {s['agenda_file']} | {s['thesis_code']} | {s['thesis_updated']} "
                f"| {s['analysis_date']} | {s['thesis_score']} | {ctx} |"
            )
        lines.append("")

    # ── 合规检查 ──
    active_cfg = rules.get("active_position", {})
    pr = rules.get("portfolio_rules", {})
    holding_min = active_cfg.get("holding_count_min", 5)
    holding_max = active_cfg.get("holding_count_max", 8)
    single_max = pr.get("concentration", {}).get("single_stock_max", {}).get("threshold", 0.25)
    industry_limit = pr.get("sector_concentration", {}).get("single_sector_max", {}).get("threshold", 0.40)

    lines.append("## 合规检查")
    lines.append(f"> 注：仓位占比默认为占 C 仓位已投市值的百分比，\"占C分配\"为占 C 仓位分配额的百分比。")
    lines.append(f"- 持仓数量: {len(c_positions)} 只 (规则: {holding_min}-{holding_max} 只)")
    if total_c_value > 0:
        for p in c_positions:
            ratio = p["market_value"] / total_c_value
            ok = ratio <= single_max
            status = "✅" if ok else "❌"
            lines.append(f"- {status} {p['name']}({p['code']}) 仓位 {_fmt_pct(ratio)} (上限 {_fmt_pct(single_max)})")
        industry_map: dict[str, float] = {}
        for p in c_positions:
            ind = p.get("industry", "其他")
            industry_map[ind] = industry_map.get(ind, 0) + p["market_value"]
        lines.append("")
        lines.append("### 行业集中度")
        for ind, val in sorted(industry_map.items(), key=lambda x: x[1], reverse=True):
            ratio = val / total_c_value
            ok = ratio <= industry_limit
            status = "✅" if ok else "❌"
            lines.append(f"- {status} {ind}: {_fmt_pct(ratio)} (上限 {_fmt_pct(industry_limit)})")
        lines.append("")
        correlated_limit = pr.get("sector_concentration", {}).get("correlated_holdings_max", {}).get("threshold", 0.30)
        lines.append(f"- 关联性集中度: 待季度诊断执行（规则: 相关系数 ≥0.7 的标的合计 ≤{_fmt_pct(correlated_limit)}）")
    lines.append("")

    lines.append(f"---")
    lines.append(f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    return "\n".join(lines)


# ── Alert Monitor Trigger ───────────────────────────────────────────

def trigger_alert_monitor(alert: dict, force: bool = False):
    """调用 alert_monitor.py 处理告警。"""
    script = SCRIPTS_DIR / "alert_monitor.py"
    if not script.exists():
        return
    args = [sys.executable, str(script), alert["type"], alert.get("code", "")]
    if force:
        args.insert(3, "--force")
    try:
        subprocess.run(args, capture_output=True, timeout=30)
    except Exception:
        pass


def _alert_exists(date_str: str, alert_type: str, code: str) -> bool:
    """检查当天是否已有同 code+type 的告警文件。"""
    code_part = f"_{code}" if code else ""
    alert_file = ALERTS_DIR / f"{date_str}{code_part}_{alert_type}.md"
    return alert_file.exists()


# ── Startup Validation ──────────────────────────────────────────────

def _run_startup_validation():
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        from validate_config import check_rules_structure
        rules = load_rules()
        errors = check_rules_structure(rules)
        if errors:
            print("⚠ 配置自检发现问题：")
            for e in errors:
                print(f"  - {e}")
            print("  继续运行，但建议及时修复。\n")
    except ImportError:
        pass
    except Exception:
        pass


# ── Main ────────────────────────────────────────────────────────────

def main():
    date_str = datetime.now().strftime("%Y-%m-%d")

    # 0) 启动自检
    _run_startup_validation()

    # 1) 加载配置
    rules = load_rules()
    holdings = load_holdings()
    etf_configs = load_etf_config()
    capital = load_capital()
    thesis_scores = load_thesis_scores()
    cash_positions = load_cash_positions()

    # C 仓位分配额（直接使用 capital.yaml 的 target_c）
    c_allocated = capital.get("target_c", 0)

    if not holdings and all(e["shares"] == 0 for e in etf_configs):
        print("⚠ holdings.csv 和 core_etf.csv 均无有效数据，跳过。")
        return

    # 2) 拉取行情
    print(f"[{date_str}] 拉取行情...")
    etf_items = [(e["code"], "A") for e in etf_configs]
    c_items = [(h["code"], h.get("market", "A")) for h in holdings]
    all_items = etf_items + c_items

    # 如果 capital.yaml 有美团 RSU，添加 03690.HK
    if capital.get("meituan_rsu_shares", 0) > 0:
        all_items.append(("03690", "HK"))

    quotes = fetch_prices_batch(all_items)

    # 美团实时价格
    meituan_quote = quotes.get("03690")
    meituan_price = meituan_quote["price"] if meituan_quote else capital.get("meituan_rsu_price", 0)

    # 代码-名称交叉校验
    all_names = {e["code"]: e["name"] for e in etf_configs}
    all_names.update({h["code"]: h["name"] for h in holdings})
    for code, expected_name in all_names.items():
        q = quotes.get(code)
        if q and q.get("name"):
            api_name = q["name"]
            def _normalize(s: str) -> str:
                s = re.sub(r"\s+", "", s)
                s = re.sub(r"\d+", "", s)
                for suffix in ["华泰柏瑞", "南方", "博时", "广发", "华安",
                               "华夏", "易方达", "嘉实", "天弘", "富国",
                               "招商", "工银", "银华", "景顺长城"]:
                    s = s.replace(suffix, "")
                return s
            import re
            exp_norm = _normalize(expected_name)
            api_norm = _normalize(api_name)
            if exp_norm != api_norm:
                print(f"  ⚠ 代码校验失败: {code} 配置名称为「{expected_name}」，"
                      f"但行情返回为「{api_name}」，请检查配置文件中的代码是否正确！")

    etf_positions = []
    for e in etf_configs:
        quote = quotes.get(e["code"])
        if quote:
            e["price"] = quote["price"]
            e["change_pct"] = quote["change_pct"]
            if e["shares"] > 0:
                e["cost_total"] = e["shares"] * e["cost_price"]
                e["market_value"] = e["shares"] * quote["price"]
                e["pnl"] = e["market_value"] - e["cost_total"]
                e["pnl_pct"] = (quote["price"] - e["cost_price"]) / e["cost_price"] if e["cost_price"] else 0
            else:
                e["cost_total"] = 0
                e["market_value"] = 0
                e["pnl"] = 0
                e["pnl_pct"] = None
            print(f"  ETF {e['code']} {e['name']} ... ¥{quote['price']} ({_fmt_pct(quote['change_pct'])})")
        else:
            e["price"] = 0
            e["change_pct"] = None
            e["cost_total"] = e["shares"] * e["cost_price"]
            e["market_value"] = e["shares"] * e["cost_price"]
            e["pnl"] = 0
            e["pnl_pct"] = None
            print(f"  ETF {e['code']} {e['name']} ... 获取失败")
        etf_positions.append(e)

    total_etf_value = sum(e.get("market_value", 0) for e in etf_positions)
    for e in etf_positions:
        e["current_ratio"] = e["market_value"] / total_etf_value if total_etf_value > 0 else 0
        drift_raw = e["current_ratio"] - e["target_ratio"]
        e["drift_raw"] = drift_raw
        e["drift"] = _fmt_pct(drift_raw)

    c_positions = []
    for h in holdings:
        quote = quotes.get(h["code"])
        pos = calc_holding(h, quote)
        c_positions.append(pos)
        if quote:
            print(f"  C {h['code']} {h['name']} ... ¥{quote['price']} ({_fmt_pct(quote['change_pct'])})")
        else:
            print(f"  C {h['code']} {h['name']} ... 获取失败（将使用 holdings.csv 中的记录价）")

    # 3) 汇总
    total_c_cost = sum(p["cost_total"] for p in c_positions)
    total_c_value = sum(p["market_value"] for p in c_positions)
    total_etf_cost = sum(e.get("cost_total", 0) for e in etf_positions)

    # 4) 检查告警（含 RSU）
    alerts = check_alerts(rules, c_positions, total_c_value)
    alerts += check_etf_alerts(rules, etf_positions, total_etf_value)
    alerts += check_meituan_rsu_alerts(capital, meituan_price)

    # 5) Thesis-Analysis 同步检查
    stale_analyses = _check_thesis_analysis_sync(thesis_scores)
    if stale_analyses:
        for s in stale_analyses:
            alerts.append({
                "type": "thesis_stale_analysis",
                "severity": "warning",
                "code": s["thesis_code"],
                "message": f"thesis {s['thesis_code']} 已更新 ({s['thesis_updated']})，"
                f"{s['agenda_file']} 分析日期 {s['analysis_date']} 已过期，请重新审视策略",
            })

    # 6) 生成报告
    report = generate_report(date_str, rules, etf_positions, c_positions,
                             total_c_cost, total_c_value, total_etf_cost,
                             total_etf_value, alerts,
                             c_allocated=c_allocated, thesis_scores=thesis_scores,
                             cash_positions=cash_positions, capital=capital,
                             stale_analyses=stale_analyses,
                             meituan_price=meituan_price)
    report_path = REVIEWS_DIR / f"{date_str}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\n✅ 报告已写入 {report_path}")

    # 7) 处理告警（带去重）
    for a in alerts:
        print(f"  ⚠ [{a['severity'].upper()}] {a['message']}")
        if a["severity"] in ("critical", "warning"):
            if not _alert_exists(date_str, a["type"], a.get("code", "")):
                trigger_alert_monitor(a)
            else:
                print(f"    ⏭ 今日已触发，跳过重复告警")

    # 8) 追加时间序列
    append_portfolio_ts(date_str, etf_positions, c_positions, cash_positions, meituan_price)

    # 9) 汇总输出
    total_a = sum(r["current_value"] for r in cash_positions if r["code"] != "MTRSU")
    total_d = sum(r["current_value"] for r in cash_positions if r["code"] == "MTRSU")
    total_all = total_a + total_etf_value + total_c_value + total_d
    print(f"\n📊 总资产: {total_all:,.0f}（含D档美团RSU {total_d:,.0f}，排除再平衡基准）")
    print(f"📊 再平衡基准 (A+B+C): {total_a + total_etf_value + total_c_value:,.0f}")
    if c_positions:
        print(f"📊 C 仓位合计: {total_c_value:,.2f} | 盈亏: {total_c_value - total_c_cost:+,.2f}")
    if total_etf_value > 0:
        print(f"📊 B 仓位合计: {total_etf_value:,.2f} | 盈亏: {total_etf_value - total_etf_cost:+,.2f}")


if __name__ == "__main__":
    main()
