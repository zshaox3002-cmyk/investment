"""Position monitor — Phase 3.

Covers:
  - Load current holdings + alerts from DB
  - Compare against user_profile target allocation (from Phase 2)
  - Check four iron rules
  - Compute tranche deviation
  - Output human-readable position report with action directives
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from investment.core.db import connect
from investment.agent_tools.translator import (
    HumanAlert,
    fmt_cny, fmt_pct,
    translate_alerts,
    translate_deviation,
    translate_rule_path,
)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class HoldingSummary:
    code: str
    name: str
    tranche: str
    market_value: float
    cost_total: float
    pnl_pct: float
    price: float
    shares: float
    weight_in_tranche: float = 0.0   # filled after loading all holdings


@dataclass
class TrancheSummary:
    tranche: str
    market_value: float
    target_ratio: float    # from user_profile (0.0 if no profile)
    actual_ratio: float    # market_value / total_portfolio
    deviation_text: str


@dataclass
class RuleBreach:
    rule_name: str         # human-readable
    current_value: float
    threshold: float
    status: str
    action_required: str


@dataclass
class PositionReport:
    as_of: str
    total_portfolio_value: float
    holdings: list[HoldingSummary]
    tranches: list[TrancheSummary]
    alerts: list[HumanAlert]
    rule_breaches: list[RuleBreach]
    rebalance_needed: bool
    human_message: str
    has_profile: bool = False


# ── Iron rule descriptions ────────────────────────────────────────────────────

_IRON_RULES = {
    "single_stock_max": {
        "name": "单股仓位上限（25%）",
        "action": "运行 `inv trade decision CODE --type REDUCE` 启动减仓，将仓位降至 25% 以下",
    },
    "theme_concentration": {
        "name": "主题集中度上限（35%）",
        "action": "减持同一主题中表现最弱的持仓，增加行业分散度",
    },
    "active_position_total": {
        "name": "C 档主动选股总仓位（30%）",
        "action": "暂停新建仓，等待 C 档仓位自然回落至目标比例",
    },
    "drawdown_review": {
        "name": "单股回撤 15% 强制审查",
        "action": "在 7 天内更新该股票的 thesis 评分，若论点失效则触发减仓",
    },
    "portfolio_drawdown": {
        "name": "账户回撤 -20% 强制降仓",
        "action": "立即启动降仓计划，将 C 档仓位降至 50% 以下",
    },
}


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_holdings(conn, as_of: Optional[str] = None) -> list[dict]:
    """Load all holdings (B+C tranche) with latest prices."""
    date_filter = as_of or date.today().isoformat()
    rows = conn.execute(
        """SELECT v.code, v.name, v.tranche, v.market_value, v.cost_total,
                  v.pnl_pct, v.price, v.shares
           FROM v_portfolio_snapshot v
           WHERE v.tranche IN ('B', 'C')
           ORDER BY v.tranche, v.market_value DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def _load_cash(conn) -> float:
    """Load total A-tranche cash balance."""
    row = conn.execute(
        """SELECT SUM(cb.balance) AS total
           FROM cash_balances cb
           JOIN instruments i ON i.id = cb.instrument_id
           WHERE i.tranche = 'A'
             AND cb.effective_date = (
               SELECT MAX(effective_date) FROM cash_balances cb2
               WHERE cb2.instrument_id = cb.instrument_id
             )"""
    ).fetchone()
    return float(row["total"] or 0)


def _load_alerts(conn, as_of: Optional[str] = None) -> list[dict]:
    """Load today's (or specified date's) alerts."""
    target_date = as_of or date.today().isoformat()
    rows = conn.execute(
        """SELECT a.alert_type, a.severity, a.message,
                  COALESCE(i.code, '') AS code,
                  COALESCE(i.name, '') AS name
           FROM alerts a
           LEFT JOIN instruments i ON i.id = a.instrument_id
           WHERE a.alert_date = ?
             AND (a.acknowledged IS NULL OR a.acknowledged = 0)
           ORDER BY
             CASE a.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
             a.id""",
        (target_date,),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_rule_breaches(conn) -> list[dict]:
    """Load active rule breaches."""
    rows = conn.execute(
        """SELECT rb.rule_path, rb.current_value, rb.threshold, rb.status,
                  COALESCE(i.code, '') AS code,
                  COALESCE(i.name, '') AS name
           FROM rule_breaches rb
           LEFT JOIN instruments i ON i.id = rb.instrument_id
           WHERE rb.status IN ('active', 'remediating')
           ORDER BY rb.detected_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def _load_user_profile(conn) -> Optional[dict]:
    """Load the latest user profile."""
    row = conn.execute(
        "SELECT * FROM user_profile ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


# ── Deviation calculation ─────────────────────────────────────────────────────

def _compute_tranches(
    holdings: list[dict],
    cash_value: float,
    profile: Optional[dict],
) -> tuple[list[TrancheSummary], float]:
    """Compute per-tranche summaries and total portfolio value."""
    b_value = sum(h["market_value"] for h in holdings if h["tranche"] == "B")
    c_value = sum(h["market_value"] for h in holdings if h["tranche"] == "C")
    a_value = cash_value
    total = a_value + b_value + c_value
    if total <= 0:
        total = 1.0  # avoid division by zero

    # Target ratios: from profile if available, else system defaults
    if profile:
        a_target = profile["a_ratio"]
        b_target = profile["b_ratio"]
        c_target = profile["c_ratio"]
    else:
        a_target = b_target = c_target = None

    tranches: list[TrancheSummary] = []
    for tranche, value, target in [
        ("A", a_value, a_target),
        ("B", b_value, b_target),
        ("C", c_value, c_target),
    ]:
        actual = value / total
        if target is not None:
            dev_text = translate_deviation(
                tranche, actual, target,
                value, total * target,
            )
        else:
            dev_text = f"{tranche} 档：{fmt_pct(actual)}（未设置目标配置）"
        tranches.append(TrancheSummary(
            tranche=tranche,
            market_value=value,
            target_ratio=target or 0.0,
            actual_ratio=actual,
            deviation_text=dev_text,
        ))
    return tranches, total


def _rebalance_needed(tranches: list[TrancheSummary], threshold: float = 0.05) -> bool:
    """Return True if any tranche deviates from target by more than threshold."""
    return any(
        t.target_ratio > 0 and abs(t.actual_ratio - t.target_ratio) > threshold
        for t in tranches
    )


# ── Rule breach translation ───────────────────────────────────────────────────

def _translate_breaches(raw_breaches: list[dict]) -> list[RuleBreach]:
    result: list[RuleBreach] = []
    for rb in raw_breaches:
        rule_info = _IRON_RULES.get(rb["rule_path"], {})
        name = rule_info.get("name") or translate_rule_path(rb["rule_path"])
        action = rule_info.get("action", "查看详情后决定是否需要操作")
        result.append(RuleBreach(
            rule_name=name,
            current_value=rb["current_value"],
            threshold=rb["threshold"],
            status=rb["status"],
            action_required=action,
        ))
    return result


# ── Human message builder ─────────────────────────────────────────────────────

def _build_human_message(report: PositionReport) -> str:
    lines: list[str] = [f"## 仓位巡检 — {report.as_of}\n"]

    # Core conclusions
    critical = [a for a in report.alerts if "紧急" in a.severity_label]
    warnings = [a for a in report.alerts if "警告" in a.severity_label]

    if critical:
        lines.append(f"### ⚠️ 紧急告警（{len(critical)} 条）")
        for a in critical:
            lines.append(a.to_text())
            lines.append("")
    elif warnings:
        lines.append(f"### 🟡 警告（{len(warnings)} 条）")
        for a in warnings[:3]:  # show top 3
            lines.append(a.to_text())
            lines.append("")
    else:
        lines.append("### ✅ 无告警，持仓状态正常\n")

    # Tranche deviation
    lines.append("### 档位配置")
    for t in report.tranches:
        lines.append(f"- {t.deviation_text}")
    lines.append("")

    # Rule breaches
    if report.rule_breaches:
        lines.append(f"### 规则违反（{len(report.rule_breaches)} 条）")
        for rb in report.rule_breaches:
            lines.append(
                f"- **{rb.rule_name}**：当前 {fmt_pct(rb.current_value)}，"
                f"阈值 {fmt_pct(rb.threshold)}，状态：{rb.status}"
            )
            lines.append(f"  所以你该做什么：{rb.action_required}")
        lines.append("")

    # Holdings table
    if report.holdings:
        lines.append("### 持仓明细")
        lines.append("| 股票 | 档位 | 市值 | 盈亏 | 仓位占比 |")
        lines.append("|------|------|------|------|---------|")
        for h in sorted(report.holdings, key=lambda x: -x.market_value):
            pnl_str = f"{h.pnl_pct * 100:+.1f}%"
            lines.append(
                f"| {h.name}（{h.code}）| {h.tranche} 档 | "
                f"{fmt_cny(h.market_value)} | {pnl_str} | "
                f"{fmt_pct(h.weight_in_tranche)} |"
            )
        lines.append("")

    # Rebalance
    if report.rebalance_needed:
        lines.append(
            "### 再平衡建议\n"
            "当前配置偏离目标超过 5%，建议在下次操作窗口进行再平衡。\n"
            "所以你该做什么：查看上方档位配置，将偏离最大的档位调整至目标比例。"
        )
    elif report.has_profile:
        lines.append("### 再平衡\n当前配置偏离在 5% 以内，无需再平衡。")

    return "\n".join(lines)


# ── Main entry point ──────────────────────────────────────────────────────────

def run_position_monitor(as_of: Optional[str] = None, db_path=None) -> PositionReport:
    """Run full position monitoring: load data → compute → translate → report."""
    conn = connect(db_path)
    target_date = as_of or date.today().isoformat()

    holdings_raw = _load_holdings(conn, target_date)
    cash_value = _load_cash(conn)
    alerts_raw = _load_alerts(conn, target_date)
    breaches_raw = _load_rule_breaches(conn)
    profile = _load_user_profile(conn)
    conn.close()

    # Build holding summaries
    c_total = sum(h["market_value"] for h in holdings_raw if h["tranche"] == "C")
    holdings: list[HoldingSummary] = []
    for h in holdings_raw:
        tranche_total = (
            c_total if h["tranche"] == "C"
            else sum(x["market_value"] for x in holdings_raw if x["tranche"] == h["tranche"])
        )
        weight = h["market_value"] / tranche_total if tranche_total > 0 else 0.0
        holdings.append(HoldingSummary(
            code=h["code"], name=h["name"], tranche=h["tranche"],
            market_value=h["market_value"], cost_total=h["cost_total"],
            pnl_pct=h["pnl_pct"], price=h["price"], shares=h["shares"],
            weight_in_tranche=weight,
        ))

    tranches, total = _compute_tranches(holdings_raw, cash_value, profile)
    human_alerts = translate_alerts(alerts_raw)
    rule_breaches = _translate_breaches(breaches_raw)
    rebalance = _rebalance_needed(tranches)

    report = PositionReport(
        as_of=target_date,
        total_portfolio_value=total,
        holdings=holdings,
        tranches=tranches,
        alerts=human_alerts,
        rule_breaches=rule_breaches,
        rebalance_needed=rebalance,
        human_message="",
        has_profile=profile is not None,
    )
    report.human_message = _build_human_message(report)
    return report
