#!/usr/bin/env python3
"""
rebalance_check.py — 仓位偏离检查

用法:
    python scripts/rebalance_check.py           # 拉取实时行情后检查
    python scripts/rebalance_check.py --no-fetch # 使用 CSV 记录价格（离线）

职责:
  1. 检查 B/C 仓位实际分配 vs 目标比例
  2. 检查 B 档 ETF 内部各品种偏离度
  3. 检查 C 档个股集中度 + 行业集中度
  4. 输出再平衡建议，写入 reviews/rebalance/
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    ROOT_DIR, load_rules, load_holdings, load_etf_config, load_capital,
    fetch_price, calc_holding, _fmt_pct,
)

REBALANCE_DIR = ROOT_DIR / "reviews" / "rebalance"
REBALANCE_DIR.mkdir(parents=True, exist_ok=True)


# ── Build positions ───────────────────────────────────────────────────

def build_positions(holdings, etf_configs, do_fetch=True):
    c_positions = []
    for h in holdings:
        quote = fetch_price(h["code"], h.get("market", "A")) if do_fetch else None
        c_positions.append(calc_holding(h, quote))

    etf_positions = []
    for raw in etf_configs:
        e = {k: v for k, v in raw.items()}
        quote = fetch_price(e["code"]) if do_fetch else None
        price = quote["price"] if quote else (e["cost_price"] if e["cost_price"] else 0)
        e["price"] = price
        e["change_pct"] = quote["change_pct"] if quote else None
        if e["shares"] > 0 and price > 0:
            e["market_value"] = e["shares"] * price
            e["cost_total"] = e["shares"] * e["cost_price"]
            e["pnl"] = e["market_value"] - e["cost_total"]
            e["pnl_pct"] = (price - e["cost_price"]) / e["cost_price"] if e["cost_price"] else 0
        else:
            e["market_value"] = 0
            e["cost_total"] = 0
            e["pnl"] = 0
            e["pnl_pct"] = None
        etf_positions.append(e)

    return c_positions, etf_positions


# ── Analysis ──────────────────────────────────────────────────────────

def analyse(rules, c_positions, etf_positions, capital):
    rebalance_base = capital.get("rebalance_base_capital", 0)
    target_b = capital.get("target_b", 0)
    target_c = capital.get("target_c", 0)

    # 兼容旧字段：若新字段不存在，从 rules.yaml 动态计算比例
    if not rebalance_base:
        total_bc = capital.get("total_bc_capital", 0)
        c_target_of_total = rules.get("portfolio_rules", {}).get("active_position_total", {}).get("target", 0.25)
        b_target_of_total = c_target_of_total * 2  # B 档为 C 档的 2 倍
        bc_sum = b_target_of_total + c_target_of_total
        b_target_pct = b_target_of_total / bc_sum if bc_sum > 0 else 2/3
        c_target_pct = c_target_of_total / bc_sum if bc_sum > 0 else 1/3
        target_b = total_bc * b_target_pct
        target_c = total_bc * c_target_pct
        rebalance_base = total_bc

    total_c = sum(p["market_value"] for p in c_positions)
    total_b = sum(e["market_value"] for e in etf_positions)
    total_bc_actual = total_b + total_c

    b_actual_pct = total_b / rebalance_base if rebalance_base > 0 else 0
    c_actual_pct = total_c / rebalance_base if rebalance_base > 0 else 0
    c_target_of_total = rules.get("portfolio_rules", {}).get("active_position_total", {}).get("target", 0.25)
    b_target_pct = target_b / rebalance_base if rebalance_base > 0 else c_target_of_total * 2
    c_target_pct = target_c / rebalance_base if rebalance_base > 0 else c_target_of_total

    # ETF internal drift
    total_etf = sum(e["market_value"] for e in etf_positions)
    drift_threshold = rules.get("monitoring", {}).get("etf_drift_threshold", 0.05)
    for e in etf_positions:
        e["actual_ratio"] = e["market_value"] / total_etf if total_etf > 0 else 0
        e["drift"] = e["actual_ratio"] - e["target_ratio"]

    # C concentration
    single_max = (rules.get("portfolio_rules", {})
                  .get("concentration", {})
                  .get("single_stock_max", {})
                  .get("threshold", 0.25))
    industry_max = (rules.get("portfolio_rules", {})
                    .get("sector_concentration", {})
                    .get("single_sector_max", {})
                    .get("threshold", 0.40))
    for p in c_positions:
        p["c_ratio"] = p["market_value"] / total_c if total_c > 0 else 0

    industry_map: dict[str, float] = {}
    for p in c_positions:
        ind = p.get("industry", "其他")
        industry_map[ind] = industry_map.get(ind, 0) + p["market_value"]

    return {
        "total_b": total_b, "total_c": total_c, "total_bc": total_bc_actual,
        "rebalance_base": rebalance_base,
        "target_b": target_b, "target_c": target_c,
        "b_actual_pct": b_actual_pct, "c_actual_pct": c_actual_pct,
        "b_target_pct": b_target_pct, "c_target_pct": c_target_pct,
        "drift_threshold": drift_threshold,
        "single_max": single_max, "industry_max": industry_max,
        "industry_map": industry_map,
    }


# ── Report ────────────────────────────────────────────────────────────

def generate_report(c_positions, etf_positions, stats):
    date_str = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# 仓位偏离检查 — {date_str}", ""]

    # ── B/C 分配 ──
    b_drift = stats["b_actual_pct"] - stats["b_target_pct"]
    c_drift = stats["c_actual_pct"] - stats["c_target_pct"]

    def bc_action(drift):
        if abs(drift) < 0.03:
            return "✅ 正常"
        return ("⬆ 超配，减仓后资金转入" if drift > 0 else "⬇ 低配，可补充")

    b_drift_sign = "+" if b_drift >= 0 else ""
    c_drift_sign = "+" if c_drift >= 0 else ""
    lines += [
        "## B/C 仓位分配",
        f"| 档位 | 目标 | 实际市值 | 实际占比 | 偏离 | 建议 |",
        f"|------|------|---------|---------|------|------|",
        f"| B 档 ETF | {_fmt_pct(stats['b_target_pct'])} | ¥{stats['total_b']:,.0f} "
        f"| {_fmt_pct(stats['b_actual_pct'])} | {b_drift_sign}{_fmt_pct(b_drift)} | {bc_action(b_drift)} |",
        f"| C 档个股 | {_fmt_pct(stats['c_target_pct'])} | ¥{stats['total_c']:,.0f} "
        f"| {_fmt_pct(stats['c_actual_pct'])} | {c_drift_sign}{_fmt_pct(c_drift)} | {bc_action(c_drift)} |",
        "",
    ]

    # ── ETF 内部偏离 ──
    lines += [
        "## B 档 ETF 内部偏离",
        f"| 代码 | 名称 | 目标 | 实际 | 偏离 | 市值 | 建议 |",
        f"|------|------|------|------|------|------|------|",
    ]
    for e in sorted(etf_positions, key=lambda x: x["target_ratio"], reverse=True):
        drift = e["drift"]
        if abs(drift) > stats["drift_threshold"]:
            action = "⬆ 超配" if drift > 0 else "⬇ 低配，补充"
        else:
            action = "✅"
        drift_sign = "+" if drift >= 0 else ""
        lines.append(
            f"| {e['code']} | {e['name']} | {_fmt_pct(e['target_ratio'])} "
            f"| {_fmt_pct(e['actual_ratio'])} | {drift_sign}{_fmt_pct(drift)} "
            f"| ¥{e['market_value']:,.0f} | {action} |"
        )
    lines.append("")

    # ── C 个股集中度 ──
    lines += [
        "## C 档个股集中度",
        f"| 代码 | 名称 | 仓位 | 上限 | 状态 |",
        f"|------|------|------|------|------|",
    ]
    for p in sorted(c_positions, key=lambda x: x["market_value"], reverse=True):
        ratio = p["c_ratio"]
        ok = ratio <= stats["single_max"]
        status = "✅" if ok else f"❌ 超 {_fmt_pct(ratio - stats['single_max'])}"
        lines.append(
            f"| {p['code']} | {p['name']} | {_fmt_pct(ratio)} "
            f"| {_fmt_pct(stats['single_max'])} | {status} |"
        )
    lines.append("")

    # ── 行业集中度 ──
    lines += [
        "## C 档行业集中度",
        f"| 行业 | 仓位 | 上限 | 状态 |",
        f"|------|------|------|------|",
    ]
    total_c = stats["total_c"]
    for ind, val in sorted(stats["industry_map"].items(), key=lambda x: x[1], reverse=True):
        ratio = val / total_c if total_c > 0 else 0
        ok = ratio <= stats["industry_max"]
        status = "✅" if ok else f"❌ 超 {_fmt_pct(ratio - stats['industry_max'])}"
        lines.append(f"| {ind} | {_fmt_pct(ratio)} | {_fmt_pct(stats['industry_max'])} | {status} |")
    lines.append("")

    lines += ["---", f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"]
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────

def main():
    do_fetch = "--no-fetch" not in sys.argv

    print("加载配置...")
    rules = load_rules()
    holdings = load_holdings()
    etf_configs = load_etf_config()
    capital = load_capital()

    if do_fetch:
        print("拉取行情...")
    else:
        print("离线模式，使用 CSV 记录价格")

    c_positions, etf_positions = build_positions(holdings, etf_configs, do_fetch)
    stats = analyse(rules, c_positions, etf_positions, capital)
    report = generate_report(c_positions, etf_positions, stats)

    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = REBALANCE_DIR / f"{date_str}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\n✅ 报告已写入 {out_path}\n")
    print(report)


if __name__ == "__main__":
    main()
