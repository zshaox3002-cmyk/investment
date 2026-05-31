"""Trade review workflow.

inv review log --trade-id N --outcome win --errors ERR1,ERR2
inv review stats
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional

from investment.core.db import connect, transaction
from investment.core.settings import REVIEWS_DIR

REVIEW_DIR = REVIEWS_DIR / "trades"

ERROR_CODES = [
    "EMOTIONAL_AVERAGING_DOWN",
    "CHASE_HIGH",
    "PANIC_SELL",
    "IGNORE_THESIS_BREAK",
    "OVERSIZE_POSITION",
    "COOLING_PERIOD_VIOLATION",
    "MISSING_IC_MEMO",
    "STOP_LOSS_OVERRIDE",
    "OVERTRADE",
    "NARRATIVE_DRIFT",
    "CONFIRMATION_BIAS",
    "OTHER",
]

ERROR_LABELS = {
    "EMOTIONAL_AVERAGING_DOWN": "情绪化摊薄",
    "CHASE_HIGH": "追高买入",
    "PANIC_SELL": "恐慌卖出",
    "IGNORE_THESIS_BREAK": "忽视 thesis 证伪",
    "OVERSIZE_POSITION": "仓位过重",
    "COOLING_PERIOD_VIOLATION": "违反冷静期",
    "MISSING_IC_MEMO": "缺少 IC Memo",
    "STOP_LOSS_OVERRIDE": "覆盖止损规则",
    "OVERTRADE": "过度交易",
    "NARRATIVE_DRIFT": "叙事漂移",
    "CONFIRMATION_BIAS": "确认偏误",
    "OTHER": "其他",
}


def _render_review_md(
    trade: dict,
    instrument: dict,
    decision: Optional[dict],
    review: dict,
    errors: list[dict],
) -> str:
    lines = [
        f"# 复盘 · trade_{trade['id']} · {trade['trade_date']}",
        "",
        "## 事实",
        f"- 标的：{instrument['code']} {instrument['name']}",
        f"- 方向 / 股数 / 价格：{trade['side']} / {trade['shares']:.0f} / ¥{trade['price']:.3f}",
        f"- 成交额：¥{trade['amount']:,.0f}",
    ]
    if decision:
        lines.append(f"- 关联 decision：{decision['decision_no']}")
    lines += [
        f"- 交易日期：{trade['trade_date']}",
        f"- 已实现盈亏：{review.get('result_pnl', 'N/A')} ({review.get('result_pnl_pct', 'N/A')})",
        "",
        "## 结果",
        f"- 结果：{review['outcome']}",
        "",
        "## 错误归因",
    ]
    if errors:
        for e in errors:
            lines.append(f"- [{e['error_code']}] {ERROR_LABELS.get(e['error_code'], e['error_code'])} — 严重度: {e['severity']}")
            if e.get("detail"):
                lines.append(f"  > {e['detail']}")
    else:
        lines.append("- 无错误归因")

    lines += [
        "",
        "## 情绪记录",
        review.get("emotion_record") or "（未填写）",
        "",
        "## 规则违反",
        "是" if review.get("rule_breach") else "否",
        "",
        "## 改进 action",
        "- （待填写）",
        "",
        f"---",
        f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
    ]
    return "\n".join(lines)


def log_review(
    trade_id: int,
    outcome: str,
    error_codes: list[str],
    error_severities: Optional[list[str]] = None,
    error_details: Optional[list[str]] = None,
    emotion: str = "",
    result_pnl: Optional[float] = None,
    result_pnl_pct: Optional[float] = None,
    rule_breach: bool = False,
    db_path=None,
) -> int:
    """Record a trade review. Returns review id."""
    today = date.today().isoformat()
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    # Validate error codes
    invalid = [e for e in error_codes if e not in ERROR_CODES]
    if invalid:
        raise ValueError(f"Invalid error codes: {invalid}. Valid: {ERROR_CODES}")

    valid_outcomes = {"win", "loss", "break_even", "partial"}
    if outcome not in valid_outcomes:
        raise ValueError(f"Invalid outcome: {outcome}. Must be one of {valid_outcomes}")

    with transaction(db_path) as conn:
        # Get trade info
        trade = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        if not trade:
            raise ValueError(f"Trade {trade_id} not found")

        instrument = conn.execute(
            "SELECT * FROM instruments WHERE id=?", (trade["instrument_id"],)
        ).fetchone()

        decision = None
        if trade["decision_id"]:
            decision = conn.execute(
                "SELECT * FROM decisions WHERE id=?", (trade["decision_id"],)
            ).fetchone()

        # Build review markdown path
        md_path = REVIEW_DIR / f"trade_{trade_id}.md"
        rel_path = str(md_path.relative_to(md_path.parents[3]))

        review_data = {
            "outcome": outcome,
            "result_pnl": result_pnl,
            "result_pnl_pct": result_pnl_pct,
            "emotion_record": emotion or None,
            "rule_breach": rule_breach,
        }

        errors_data = []
        for i, code in enumerate(error_codes):
            sev = (error_severities or [])[i] if error_severities and i < len(error_severities) else "medium"
            detail = (error_details or [])[i] if error_details and i < len(error_details) else None
            errors_data.append({"error_code": code, "severity": sev, "detail": detail})

        # Write markdown
        md_content = _render_review_md(
            dict(trade), dict(instrument),
            dict(decision) if decision else None,
            review_data, errors_data,
        )
        md_path.write_text(md_content, encoding="utf-8")

        # Insert review
        conn.execute(
            """INSERT INTO trade_reviews
               (review_date, scope, trade_id, decision_id, result_pnl,
                result_pnl_pct, outcome, body_path, emotion_record, rule_breach)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (today, "trade", trade_id,
             trade["decision_id"] if decision else None,
             result_pnl, result_pnl_pct, outcome, rel_path,
             emotion or None, 1 if rule_breach else 0),
        )
        review_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Insert error codes
        for e in errors_data:
            conn.execute(
                """INSERT OR IGNORE INTO review_errors
                   (review_id, error_code, severity, detail)
                   VALUES (?,?,?,?)""",
                (review_id, e["error_code"], e["severity"], e["detail"]),
            )

    return review_id


def review_stats(months: int = 3, db_path=None) -> list[dict]:
    """Return error code frequency over last N months."""
    conn = connect(db_path)
    rows = conn.execute(
        """SELECT re.error_code, COUNT(*) AS count,
                  SUM(CASE re.severity WHEN 'critical' THEN 3
                                       WHEN 'high' THEN 2
                                       WHEN 'medium' THEN 1 ELSE 0 END) AS severity_score
           FROM review_errors re
           JOIN trade_reviews tr ON tr.id=re.review_id
           WHERE tr.review_date >= date('now', ?)
           GROUP BY re.error_code
           ORDER BY count DESC""",
        (f"-{months} months",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
