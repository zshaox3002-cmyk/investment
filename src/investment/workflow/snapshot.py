"""Daily snapshot workflow.

Replaces scripts/daily_snapshot.py.
Steps:
  1. Load config (rules, capital, holdings from DB)
  2. Fetch prices via Tencent API
  3. Write quotes to DB
  4. Update holdings effective_date row (upsert today's snapshot)
  5. Run all alert checks, write to DB
  6. Generate reviews/daily/YYYY-MM-DD.md
  7. Append to data/portfolio_ts.csv (legacy compat, kept for D2 verify)
"""
from __future__ import annotations

import csv
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from investment.core.db import connect, transaction
from investment.core.settings import (
    CAPITAL_PATH, CONFIG_DIR, DATA_DIR, REVIEWS_DIR, THESES_DIR,
)
from investment.pricing.cache import save_quotes
from investment.pricing.tencent import fetch_prices_batch
from investment.rules.checker import run_all_checks, write_alerts
from investment.rules.loader import load_capital, load_rules

DAILY_DIR = REVIEWS_DIR / "daily"
PORTFOLIO_TS = DATA_DIR / "portfolio_ts.csv"


# ── Data loading from DB ───────────────────────────────────────────────────

def _load_holdings_from_db(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT i.code, i.market, i.name, i.industry, i.tranche,
                  h.shares, h.cost_price, h.added_date, h.reason
           FROM holdings h
           JOIN instruments i ON i.id = h.instrument_id
           WHERE i.tranche = 'C' AND i.active = 1
             AND h.effective_date = (
               SELECT MAX(effective_date) FROM holdings h2
               WHERE h2.instrument_id = h.instrument_id
             )"""
    ).fetchall()
    return [dict(r) for r in rows]


def _load_etf_from_db(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT i.code, i.name, i.tranche,
                  h.shares, h.cost_price,
                  COALESCE(et.target_ratio, 0) AS target_ratio,
                  COALESCE(et.rebalance_freq, 'quarterly') AS rebalance_freq
           FROM instruments i
           LEFT JOIN holdings h ON h.instrument_id = i.id
             AND h.effective_date = (
               SELECT MAX(effective_date) FROM holdings h2
               WHERE h2.instrument_id = i.id
             )
           LEFT JOIN etf_targets et ON et.instrument_id = i.id
           WHERE i.tranche = 'B' AND i.active = 1"""
    ).fetchall()
    return [dict(r) for r in rows]


def _load_cash_from_db(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT i.code, i.name, i.asset_class,
                  cb.balance, cb.annual_rate, cb.status, cb.notes
           FROM cash_balances cb
           JOIN instruments i ON i.id = cb.instrument_id
           WHERE cb.effective_date = (
             SELECT MAX(effective_date) FROM cash_balances cb2
             WHERE cb2.instrument_id = cb.instrument_id
           )"""
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # Normalise annual_rate to string like "3.50%"
        rate = d.get("annual_rate")
        if rate is not None:
            d["annual_rate"] = f"{rate * 100:.2f}%"
        else:
            d["annual_rate"] = "0.00%"
        d["type"] = d.pop("asset_class", "CASH")
        d["current_value"] = d["balance"]
        result.append(d)
    return result


def _load_thesis_scores_from_db(conn) -> dict[str, dict]:
    rows = conn.execute(
        """SELECT i.code, t.current_score, t.rating, t.action, t.alert_context, t.updated_at
           FROM theses t JOIN instruments i ON i.id = t.instrument_id"""
    ).fetchall()
    return {
        r["code"]: {
            "score": r["current_score"],
            "rating": r["rating"] or "",
            "action": r["action"] or "",
            "alert_context": r["alert_context"] or "",
            "updated": r["updated_at"],
        }
        for r in rows
    }


# ── Position calculation ───────────────────────────────────────────────────

def _calc_holding(holding: dict, quote: Optional[dict]) -> dict:
    shares = holding["shares"] or 0
    cost = holding["cost_price"] or 0
    price = quote["price"] if quote else cost
    market_value = shares * price
    cost_total = shares * cost
    pnl = market_value - cost_total
    pnl_pct = (price - cost) / cost if cost else 0.0
    return {
        **holding,
        "current_price": price,
        "market_value": market_value,
        "cost_total": cost_total,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "quote": quote,
    }


def _fmt_pct(v: Optional[float]) -> str:
    return f"{v * 100:.2f}%" if v is not None else "N/A"


# ── Update holdings snapshot in DB ────────────────────────────────────────

def _upsert_holdings_today(conn, holdings: list[dict], today: str) -> None:
    """Write today's holdings snapshot (shares + cost unchanged, just new effective_date)."""
    for h in holdings:
        iid = conn.execute(
            "SELECT id FROM instruments WHERE code=? AND market=?",
            (h["code"], h["market"]),
        ).fetchone()
        if not iid:
            continue
        conn.execute(
            """INSERT OR IGNORE INTO holdings
               (instrument_id, effective_date, shares, cost_price, added_date, reason, source)
               SELECT instrument_id, ?, shares, cost_price, added_date, reason, 'snapshot'
               FROM holdings
               WHERE instrument_id = ?
               ORDER BY effective_date DESC LIMIT 1""",
            (today, iid["id"]),
        )


# ── Report generation ──────────────────────────────────────────────────────

def _generate_report(
    date_str: str,
    rules: dict,
    capital: dict,
    etf_positions: list[dict],
    c_positions: list[dict],
    cash_positions: list[dict],
    alerts: list[dict],
    thesis_scores: dict,
    meituan_price: float = 0,
) -> str:
    lines = [f"# 每日快照 — {date_str}", ""]

    a_rows = [r for r in cash_positions if r["code"] != "MTRSU"]
    d_rows = [r for r in cash_positions if r["code"] == "MTRSU"]
    total_a = sum(r["current_value"] for r in a_rows)
    total_d = sum(r["current_value"] for r in d_rows)
    total_c = sum(p["market_value"] for p in c_positions)
    total_b = sum(e.get("market_value", 0) for e in etf_positions)
    total_all = total_a + total_b + total_c + total_d

    rebalance_base = capital.get("rebalance_base_capital", 0)
    target_a = capital.get("target_a", 0)
    target_b = capital.get("target_b", 0)
    target_c = capital.get("target_c", 0)

    # ── 总资产概览 ──
    lines += ["## 总资产概览",
              "| 档位 | 市值 | 占总资产 | 目标 | 状态 |",
              "|------|------|---------|------|------|"]
    if total_all > 0:
        lines.append(f"| A档（现金/债券） | {total_a:,.0f} | {total_a/total_all*100:.2f}% | {target_a:,.0f} | {'✅' if total_a >= target_a * 0.9 else '🔴'} |")
        lines.append(f"| B档（核心ETF） | {total_b:,.0f} | {total_b/total_all*100:.2f}% | {target_b:,.0f} | {'✅' if total_b >= target_b * 0.9 else '🔴'} |")
        lines.append(f"| C档（主动选股） | {total_c:,.0f} | {total_c/total_all*100:.2f}% | {target_c:,.0f} | {'✅' if total_c <= target_c * 1.1 else '🟡'} |")
        lines.append(f"| D档（美团RSU）⚠排除再平衡基准 | {total_d:,.0f} | {total_d/total_all*100:.2f}% | — | 按计划减持 |")
        lines.append(f"| **合计** | **{total_all:,.0f}** | **100%** | — | — |")
    lines.append("")
    if rebalance_base > 0:
        lines.append(f"> 再平衡基准（排除D档）：¥{rebalance_base:,.0f}")
        meituan_review = capital.get("meituan_rsu_review_date", "2027-11-01")
        try:
            review_date = datetime.strptime(meituan_review, "%Y-%m-%d").date()
            days = (review_date - date.today()).days
            if days > 0:
                lines.append(f"> 美团RSU第二档解锁倒计时: **{days} 天**（{meituan_review}）")
        except ValueError:
            pass
        lines.append("")

    # ── A 档 ──
    if a_rows:
        lines += ["## A 档 — 现金/债券",
                  "| 代码 | 名称 | 类型 | 市值 | 年化收益 | 备注 |",
                  "|------|------|------|------|---------|------|"]
        for r in a_rows:
            lines.append(f"| {r['code']} | {r['name']} | {r['type']} | {r['current_value']:,.0f} | {r['annual_rate']} | {r.get('notes','')} |")
        lines.append("")

    # ── D 档 RSU ──
    if d_rows and meituan_price > 0:
        rsu = d_rows[0]
        rsu_current = rsu["balance"] * meituan_price
        rsu_prev_price = capital.get("meituan_rsu_price", meituan_price)
        rsu_change = (meituan_price - rsu_prev_price) / rsu_prev_price if rsu_prev_price > 0 else 0
        lines += ["## D 档 — 美团 RSU",
                  "| 项目 | 数值 |", "|------|------|",
                  f"| 持有股数 | {rsu['balance']:,.0f} 股 |",
                  f"| 上次记录价 | HK${rsu_prev_price:.2f} |",
                  f"| 实时股价 | HK${meituan_price:.2f} |",
                  f"| 当前市值 | HK${rsu_current:,.0f} |",
                  f"| 股价变动 | {_fmt_pct(rsu_change)} |", ""]

    # ── B+C 概览 ──
    c_allocated = target_c
    total_bc = total_b + total_c
    total_c_cost = sum(p["cost_total"] for p in c_positions)
    total_b_cost = sum(e.get("cost_total", 0) for e in etf_positions)
    lines += ["## B+C 账户概览", "| 项目 | 数值 |", "|------|------|",
              f"| C 仓位市值 | {total_c:,.2f} |"]
    if c_allocated > 0:
        lines.append(f"| C 仓位分配额 | {c_allocated:,.2f} |")
        lines.append(f"| C 仓位部署率 | {total_c/c_allocated*100:.1f}% |")
    lines += [f"| B 仓位 ETF 市值 | {total_b:,.2f} |",
              f"| 总市值 (B+C) | {total_bc:,.2f} |"]
    if total_c_cost > 0:
        pnl = total_c - total_c_cost
        lines.append(f"| C 仓位总盈亏 | {pnl:+,.2f} ({_fmt_pct(pnl/total_c_cost)}) |")
    if total_b_cost > 0:
        pnl = total_b - total_b_cost
        lines.append(f"| B 仓位总盈亏 | {pnl:+,.2f} ({_fmt_pct(pnl/total_b_cost)}) |")
    lines.append("")

    # ── B 仓位 ETF ──
    lines += ["## B 仓位 — ETF 组合",
              "| 代码 | 名称 | 成本价 | 现价 | 份额 | 市值 | 盈亏 | 盈亏比 | 目标比例 | 实际比例 | 偏离度 | 涨跌幅 |",
              "|------|------|--------|------|------|------|------|--------|----------|----------|--------|--------|"]
    for e in etf_positions:
        price_s = f"{e.get('price',0):.3f}" if e.get("price") else "N/A"
        cost_s = f"{e.get('cost_price',0):.3f}" if e.get("cost_price") else "N/A"
        mv_s = f"{e.get('market_value',0):,.2f}" if e.get("market_value") else "N/A"
        shares_val = e.get("shares") or 0
        lines.append(
            f"| {e['code']} | {e['name']} | {cost_s} | {price_s} | {shares_val:.0f} | "
            f"{mv_s} | {e.get('pnl',0):+,.2f} | {_fmt_pct(e.get('pnl_pct'))} | "
            f"{_fmt_pct(e['target_ratio'])} | {_fmt_pct(e.get('current_ratio'))} | "
            f"{_fmt_pct(e.get('drift_raw'))} | {_fmt_pct(e.get('change_pct'))} |"
        )
    lines.append("")

    # ── C 仓位 ──
    lines += ["## C 仓位 — 主动选股",
              "| 代码 | 名称 | 市场 | 行业 | 成本价 | 现价 | 股数 | 市值 | 盈亏 | 盈亏比 | 占C已投 | 占C分配 | thesis | 涨跌幅 |",
              "|------|------|------|------|--------|------|------|------|------|--------|----------|----------|--------|--------|"]
    for p in sorted(c_positions, key=lambda x: x["market_value"], reverse=True):
        ratio_inv = p["market_value"] / total_c if total_c else 0
        ratio_alloc = _fmt_pct(p["market_value"] / c_allocated) if c_allocated > 0 else "N/A"
        t = thesis_scores.get(p["code"], {})
        thesis_s = f"{t.get('rating','')} {t.get('score','')}" if t else "-"
        change = _fmt_pct(p["quote"].get("change_pct") if p.get("quote") else None)
        lines.append(
            f"| {p['code']} | {p['name']} | {p.get('market','A')} | {p.get('industry','')} | "
            f"{p['cost_price']:.3f} | {p['current_price']:.3f} | {p['shares']:.0f} | "
            f"{p['market_value']:,.2f} | {p['pnl']:+,.2f} | "
            f"{_fmt_pct(p['pnl_pct'])} | {_fmt_pct(ratio_inv)} | {ratio_alloc} | {thesis_s} | {change} |"
        )
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
            if code and code in thesis_scores and thesis_scores[code].get("alert_context"):
                lines.append(f"  > 💡 Thesis: {thesis_scores[code]['alert_context']}")
    lines.append("")

    # ── 合规检查 ──
    pr = rules.get("portfolio_rules", {})
    single_max = pr.get("concentration", {}).get("single_stock_max", {}).get("threshold", 0.25)
    industry_limit = pr.get("sector_concentration", {}).get("single_sector_max", {}).get("threshold", 0.40)
    active_cfg = rules.get("active_position", {})
    holding_min = active_cfg.get("holding_count_min", 5)
    holding_max = active_cfg.get("holding_count_max", 8)

    lines += ["## 合规检查",
              f"> 注：仓位占比默认为占 C 仓位已投市值的百分比，\"占C分配\"为占 C 仓位分配额的百分比。",
              f"- 持仓数量: {len(c_positions)} 只 (规则: {holding_min}-{holding_max} 只)"]
    if total_c > 0:
        for p in c_positions:
            ratio = p["market_value"] / total_c
            ok = ratio <= single_max
            lines.append(f"- {'✅' if ok else '❌'} {p['name']}({p['code']}) 仓位 {_fmt_pct(ratio)} (上限 {_fmt_pct(single_max)})")
        industry_map: dict[str, float] = {}
        for p in c_positions:
            ind = p.get("industry", "其他")
            industry_map[ind] = industry_map.get(ind, 0) + p["market_value"]
        lines += ["", "### 行业集中度"]
        for ind, val in sorted(industry_map.items(), key=lambda x: x[1], reverse=True):
            ratio = val / total_c
            lines.append(f"- {'✅' if ratio <= industry_limit else '❌'} {ind}: {_fmt_pct(ratio)} (上限 {_fmt_pct(industry_limit)})")
    lines += ["", "---",
              f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"]
    return "\n".join(lines)


# ── Legacy portfolio_ts.csv append ────────────────────────────────────────

def _append_portfolio_ts(
    date_str: str,
    etf_positions: list[dict],
    c_positions: list[dict],
    cash_positions: list[dict],
    meituan_price: float = 0,
) -> None:
    headers = ["date", "code", "type", "name", "shares", "price", "market_value", "pnl_pct"]
    file_exists = PORTFOLIO_TS.exists()
    with open(PORTFOLIO_TS, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(headers)
        for e in etf_positions:
            w.writerow([date_str, e["code"], "B", e["name"],
                        e.get("shares", 0),
                        f"{e.get('price',0):.3f}" if e.get("price") else "",
                        f"{e.get('market_value',0):.2f}" if e.get("market_value") else "",
                        f"{e.get('pnl_pct',0)*100:.2f}" if e.get("pnl_pct") is not None else ""])
        for p in c_positions:
            w.writerow([date_str, p["code"], "C", p["name"],
                        p.get("shares", 0),
                        f"{p.get('current_price',0):.3f}",
                        f"{p.get('market_value',0):.2f}",
                        f"{p.get('pnl_pct',0)*100:.2f}"])
        for r in cash_positions:
            pos_type = "D" if r.get("code") == "MTRSU" else "A"
            if r.get("code") == "MTRSU" and meituan_price > 0:
                shares = r.get("balance", 0)
                mv = shares * meituan_price
                price_s = f"{meituan_price:.3f}"
            else:
                shares = r.get("balance", 0)
                price_s = ""
                mv = r.get("current_value", 0)
            w.writerow([date_str, r["code"], pos_type, r["name"],
                        shares, price_s, f"{mv:.2f}", ""])


# ── Main entry point ───────────────────────────────────────────────────────

def run(db_path=None, date_str: Optional[str] = None) -> dict:
    """Run daily snapshot. Returns summary dict."""
    today = date_str or date.today().isoformat()
    DAILY_DIR.mkdir(parents=True, exist_ok=True)

    rules = load_rules()
    capital = load_capital()

    conn = connect(db_path)
    holdings = _load_holdings_from_db(conn)
    etf_configs = _load_etf_from_db(conn)
    cash_positions = _load_cash_from_db(conn)
    thesis_scores = _load_thesis_scores_from_db(conn)
    conn.close()

    if not holdings and all((e.get("shares") or 0) == 0 for e in etf_configs):
        print("⚠ 无有效持仓数据，跳过。")
        return {}

    # ── Fetch prices ──
    print(f"[{today}] 拉取行情...")
    etf_items = [(e["code"], "A") for e in etf_configs]
    c_items = [(h["code"], h.get("market", "A")) for h in holdings]
    all_items = etf_items + c_items
    if capital.get("meituan_rsu_shares", 0) > 0:
        all_items.append(("03690", "HK"))

    quotes = fetch_prices_batch(all_items)

    meituan_quote = quotes.get("03690")
    meituan_price = meituan_quote["price"] if meituan_quote else capital.get("meituan_rsu_price", 0)

    # ── Save quotes to DB ──
    saved = save_quotes(quotes, all_items, today, db_path)
    print(f"  行情写入 DB: {saved} 条")

    # ── Build ETF positions ──
    etf_positions = []
    for e in etf_configs:
        q = quotes.get(e["code"])
        shares = e.get("shares") or 0
        cost = e.get("cost_price") or 0
        if q:
            price = q["price"]
            e["price"] = price
            e["change_pct"] = q.get("change_pct")
            print(f"  ETF {e['code']} {e['name']} ... ¥{price} ({_fmt_pct(q.get('change_pct'))})")
        else:
            price = cost
            e["price"] = 0
            e["change_pct"] = None
            print(f"  ETF {e['code']} {e['name']} ... 获取失败")
        e["market_value"] = shares * price
        e["cost_total"] = shares * cost
        e["pnl"] = e["market_value"] - e["cost_total"]
        e["pnl_pct"] = (price - cost) / cost if cost and shares > 0 else None
        etf_positions.append(e)

    total_b = sum(e.get("market_value", 0) for e in etf_positions)
    for e in etf_positions:
        e["current_ratio"] = e["market_value"] / total_b if total_b > 0 else 0
        e["drift_raw"] = e["current_ratio"] - e["target_ratio"]

    # ── Build C positions ──
    c_positions = []
    for h in holdings:
        q = quotes.get(h["code"])
        pos = _calc_holding(h, q)
        c_positions.append(pos)
        if q:
            print(f"  C {h['code']} {h['name']} ... ¥{q['price']} ({_fmt_pct(q.get('change_pct'))})")
        else:
            print(f"  C {h['code']} {h['name']} ... 获取失败（使用成本价）")

    total_c = sum(p["market_value"] for p in c_positions)

    # ── Update RSU current_value with live price ──
    for r in cash_positions:
        if r["code"] == "MTRSU" and meituan_price > 0:
            r["current_value"] = r["balance"] * meituan_price

    # ── Run checks ──
    conn = connect(db_path)
    alerts = run_all_checks(rules, capital, c_positions, etf_positions, total_c, meituan_price, conn)
    conn.close()

    # ── Write alerts to DB ──
    n_alerts = write_alerts(alerts, today, db_path)
    print(f"  告警写入 DB: {n_alerts} 条新告警")

    # ── Update holdings snapshot ──
    with transaction(db_path) as conn:
        _upsert_holdings_today(conn, holdings, today)

    # ── Generate report ──
    report = _generate_report(
        today, rules, capital, etf_positions, c_positions,
        cash_positions, alerts, thesis_scores, meituan_price,
    )
    report_path = DAILY_DIR / f"{today}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\n✅ 报告已写入 {report_path}")

    # ── Legacy portfolio_ts.csv ──
    _append_portfolio_ts(today, etf_positions, c_positions, cash_positions, meituan_price)

    # ── Summary ──
    total_a = sum(r["current_value"] for r in cash_positions if r["code"] != "MTRSU")
    # D-tranche RSU: balance is shares, current_value needs live price
    d_rows_raw = [r for r in cash_positions if r["code"] == "MTRSU"]
    if d_rows_raw and meituan_price > 0:
        total_d = d_rows_raw[0]["balance"] * meituan_price
        # Update current_value for report rendering
        d_rows_raw[0]["current_value"] = total_d
    else:
        total_d = sum(r["current_value"] for r in d_rows_raw)
    total_all = total_a + total_b + total_c + total_d
    print(f"\n📊 总资产: {total_all:,.0f}（含D档美团RSU {total_d:,.0f}）")
    print(f"📊 再平衡基准 (A+B+C): {total_a + total_b + total_c:,.0f}")
    if c_positions:
        total_c_cost = sum(p["cost_total"] for p in c_positions)
        print(f"📊 C 仓位合计: {total_c:,.2f} | 盈亏: {total_c - total_c_cost:+,.2f}")
    if total_b > 0:
        total_b_cost = sum(e.get("cost_total", 0) for e in etf_positions)
        print(f"📊 B 仓位合计: {total_b:,.2f} | 盈亏: {total_b - total_b_cost:+,.2f}")

    return {
        "date": today,
        "total_all": total_all,
        "total_c": total_c,
        "total_b": total_b,
        "alerts": len(alerts),
        "report_path": str(report_path),
    }
