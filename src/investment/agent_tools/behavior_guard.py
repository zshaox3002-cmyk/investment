"""Behavior guard — Phase 7 Skill ⑨.

Detects behavioral biases in trading decisions:
  - FOMO_BUY: buying after a large run-up
  - PANIC_SELL: selling after a large drop
  - ANCHORING: fixating on a specific price
  - DISPOSITION_EFFECT: selling winners too early, holding losers too long
  - OVERTRADING: excessive trade frequency
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from investment.core.db import connect, transaction
from investment.agent_tools.translator import translate_error_code


# ── Bias detection ────────────────────────────────────────────────────────────

@dataclass
class BiasFlag:
    bias_type: str
    bias_label: str
    evidence: str
    severity: str
    action: str


def _detect_fomo_buy(code: str, conn) -> Optional[BiasFlag]:
    """Detect if buying after a large recent run-up (>10% in 5 days)."""
    rows = conn.execute(
        """SELECT q.change_pct FROM quotes q
           JOIN instruments i ON i.id = q.instrument_id
           WHERE i.code = ? AND q.change_pct IS NOT NULL
           ORDER BY q.quote_date DESC LIMIT 5""",
        (code,),
    ).fetchall()
    if len(rows) < 3:
        return None
    cumulative = sum(float(r["change_pct"]) for r in rows)
    if cumulative > 0.10:
        return BiasFlag(
            bias_type="FOMO_BUY",
            bias_label="追涨买入（FOMO）",
            evidence=f"{code} 近5日累计涨幅 {cumulative*100:.1f}%，在高位买入风险较大",
            severity="high",
            action="等待回调至合理估值区间再买入，或降低初始仓位至计划的50%",
        )
    return None


def _detect_panic_sell(code: str, conn) -> Optional[BiasFlag]:
    """Detect if selling after a large recent drop (>10% in 5 days)."""
    rows = conn.execute(
        """SELECT q.change_pct FROM quotes q
           JOIN instruments i ON i.id = q.instrument_id
           WHERE i.code = ? AND q.change_pct IS NOT NULL
           ORDER BY q.quote_date DESC LIMIT 5""",
        (code,),
    ).fetchall()
    if len(rows) < 3:
        return None
    cumulative = sum(float(r["change_pct"]) for r in rows)
    if cumulative < -0.10:
        return BiasFlag(
            bias_type="PANIC_SELL",
            bias_label="恐慌性卖出",
            evidence=f"{code} 近5日累计跌幅 {abs(cumulative)*100:.1f}%，在低位卖出可能是情绪驱动",
            severity="high",
            action="先检查 thesis 是否失效，若基本面未变则等待 24 小时冷静期再决策",
        )
    return None


def _detect_overtrading(conn, lookback_days: int = 30) -> Optional[BiasFlag]:
    """Detect excessive trade frequency."""
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) as n FROM trades WHERE trade_date >= ?", (cutoff,)
    ).fetchone()
    count = row["n"] if row else 0
    # More than 8 trades per month is considered high for a long-term investor
    if count > 8:
        return BiasFlag(
            bias_type="OVERTRADING",
            bias_label="过度交易",
            evidence=f"过去 {lookback_days} 天内交易 {count} 笔，频率偏高",
            severity="medium",
            action="回顾每笔交易是否都有充分的 thesis 支撑，减少无计划的短线操作",
        )
    return None


def _detect_disposition_effect(conn) -> Optional[BiasFlag]:
    """Detect disposition effect: holding losers, selling winners."""
    # Check if recent sells were all winners (pnl > 0) while holdings have losers
    recent_sells = conn.execute(
        """SELECT t.price, h.cost_price, i.code
           FROM trades t
           JOIN instruments i ON i.id = t.instrument_id
           LEFT JOIN holdings h ON h.instrument_id = t.instrument_id
             AND h.effective_date = (
               SELECT MAX(effective_date) FROM holdings h2
               WHERE h2.instrument_id = t.instrument_id
             )
           WHERE t.side = 'SELL'
           ORDER BY t.trade_date DESC LIMIT 5""",
    ).fetchall()

    if len(recent_sells) < 2:
        return None

    sold_winners = sum(
        1 for r in recent_sells
        if r["cost_price"] and float(r["price"]) > float(r["cost_price"])
    )
    if sold_winners == len(recent_sells) and len(recent_sells) >= 2:
        # Check if there are current losers being held
        losers = conn.execute(
            """SELECT COUNT(*) as n FROM v_portfolio_snapshot
               WHERE pnl_pct < -0.10 AND tranche = 'C'"""
        ).fetchone()
        if losers and losers["n"] > 0:
            return BiasFlag(
                bias_type="DISPOSITION_EFFECT",
                bias_label="处置效应",
                evidence=f"近期卖出的 {len(recent_sells)} 笔均为盈利持仓，同时持有 {losers['n']} 只亏损超10%的股票",
                severity="medium",
                action="重新评估亏损持仓的 thesis，若逻辑失效应优先止损，而非继续持有",
            )
    return None


# ── Decision journal ──────────────────────────────────────────────────────────

def log_decision(
    decision_type: str,
    stated_reason: str,
    related_code: Optional[str] = None,
    emotion_check: Optional[str] = None,
    db_path=None,
) -> tuple[int, list[BiasFlag]]:
    """Log a decision and detect biases. Returns (journal_id, detected_biases)."""
    conn = connect(db_path)
    biases: list[BiasFlag] = []

    if related_code:
        if decision_type.upper() == "BUY":
            b = _detect_fomo_buy(related_code, conn)
            if b:
                biases.append(b)
        elif decision_type.upper() == "SELL":
            b = _detect_panic_sell(related_code, conn)
            if b:
                biases.append(b)

    b_over = _detect_overtrading(conn)
    if b_over:
        biases.append(b_over)

    b_disp = _detect_disposition_effect(conn)
    if b_disp:
        biases.append(b_disp)

    conn.close()

    bias_flags_json = [{"type": b.bias_type, "severity": b.severity} for b in biases]
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    with transaction(db_path) as conn2:
        cur = conn2.execute(
            """INSERT INTO decision_journal
               (decision_date, related_code, decision_type, stated_reason,
                emotion_check, bias_flags, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (now[:10], related_code, decision_type.upper(), stated_reason,
             emotion_check, __import__("json").dumps(bias_flags_json, ensure_ascii=False),
             now),
        )
        journal_id = cur.lastrowid

        # Also flag in behavior_flags table
        for b in biases:
            conn2.execute(
                """INSERT INTO behavior_flags
                   (flag_date, bias_type, related_code, evidence, severity, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (now[:10], b.bias_type, related_code, b.evidence, b.severity, now),
            )

    return journal_id, biases


# ── Periodic behavior check ───────────────────────────────────────────────────

@dataclass
class BehaviorReport:
    as_of: str
    biases: list[BiasFlag]
    trade_count_30d: int
    avg_holding_days: float
    human_message: str


def run_behavior_check(lookback_days: int = 90, db_path=None) -> BehaviorReport:
    """Run a full behavior check without a specific trade context."""
    conn = connect(db_path)
    biases: list[BiasFlag] = []

    b_over = _detect_overtrading(conn, lookback_days=30)
    if b_over:
        biases.append(b_over)

    b_disp = _detect_disposition_effect(conn)
    if b_disp:
        biases.append(b_disp)

    # Trade count
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    row = conn.execute("SELECT COUNT(*) as n FROM trades WHERE trade_date >= ?", (cutoff,)).fetchone()
    trade_count = row["n"] if row else 0

    # Average holding days (for closed positions)
    avg_hold = 0.0
    rows = conn.execute(
        """SELECT t_buy.trade_date as buy_date, t_sell.trade_date as sell_date
           FROM trades t_buy
           JOIN trades t_sell ON t_buy.instrument_id = t_sell.instrument_id
           WHERE t_buy.side = 'BUY' AND t_sell.side = 'SELL'
             AND t_sell.trade_date > t_buy.trade_date
           LIMIT 20"""
    ).fetchall()
    if rows:
        diffs = []
        for r in rows:
            try:
                d = (date.fromisoformat(r["sell_date"]) - date.fromisoformat(r["buy_date"])).days
                diffs.append(d)
            except Exception:
                pass
        if diffs:
            avg_hold = sum(diffs) / len(diffs)

    conn.close()

    report = BehaviorReport(
        as_of=date.today().isoformat(),
        biases=biases,
        trade_count_30d=trade_count,
        avg_holding_days=avg_hold,
        human_message="",
    )
    report.human_message = _build_human_message(report)
    return report


def _build_human_message(report: BehaviorReport) -> str:
    lines = [f"## 行为检查 — {report.as_of}\n"]

    if report.biases:
        lines.append(f"### ⚠️ 检测到行为偏差（{len(report.biases)} 项）\n")
        for b in report.biases:
            lines.append(f"**{b.bias_label}**（严重程度：{b.severity}）")
            lines.append(f"表现：{b.evidence}")
            lines.append(f"所以你该做什么：{b.action}")
            lines.append("")
    else:
        lines.append("### ✅ 未检测到明显行为偏差\n")

    lines.append("### 交易频率分析")
    lines.append(f"过去 30 天：{report.trade_count_30d} 笔交易")
    if report.trade_count_30d > 8:
        lines.append("评价：偏高（建议每月不超过 8 笔）")
    elif report.trade_count_30d > 4:
        lines.append("评价：正常")
    else:
        lines.append("评价：低频（符合长期投资风格）")
    lines.append("")

    if report.avg_holding_days > 0:
        lines.append(f"### 平均持仓周期\n{report.avg_holding_days:.0f} 天")
        if report.avg_holding_days < 30:
            lines.append("（短线风格，注意交易成本侵蚀）")
        elif report.avg_holding_days < 180:
            lines.append("（中线风格）")
        else:
            lines.append("（长线风格，符合价值投资理念）")

    return "\n".join(lines)
