"""Position health engine — Phase 4.

Synthesises v_portfolio_snapshot + position_monitor + risk_engine risk
contribution + thesis scores + recent alert counts into a per-instrument
health score and label, then writes to position_health.

Health labels:
  healthy  — score >= 70, no active alerts, drawdown < 10%
  watch    — score 50–69 or drawdown 10–15%
  review   — score 30–49 or drawdown 15–20% or thesis_score < 2
  act      — score < 30 or drawdown > 20% or critical alert
  unknown  — missing data but some data available
  insufficient_data — no price / holdings data at all

Score components (0–100):
  40 pts — P&L / drawdown health
  25 pts — thesis score (scaled to 0–25)
  20 pts — risk contribution (inverse: low contrib = high score)
  15 pts — alert penalty (deducted)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional
import json

from investment.core.db import connect, transaction


@dataclass
class PositionHealthRecord:
    calc_date: str
    instrument_id: int
    code: str
    name: str
    tranche: str

    health_score: Optional[float]          # 0–100
    health_label: str                      # healthy/watch/review/act/unknown/insufficient_data

    pnl_pct: Optional[float]
    drawdown_pct: Optional[float]          # negative decimal (peak-to-current from cost)
    weight_total: Optional[float]          # % of total portfolio
    weight_tranche: Optional[float]        # % within tranche
    risk_contrib_pct: Optional[float]
    thesis_score: Optional[float]          # 0–5
    alert_count: int
    suggested_action: Optional[str]
    evidence: dict = field(default_factory=dict)
    insufficient_data: bool = False


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_snapshot(conn) -> list[dict]:
    """All holdings from v_portfolio_snapshot (B+C+D)."""
    rows = conn.execute(
        "SELECT * FROM v_portfolio_snapshot ORDER BY market_value DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def _load_total_value(snapshot: list[dict]) -> float:
    return sum(r["market_value"] for r in snapshot if r["market_value"])


def _load_tranche_values(snapshot: list[dict]) -> dict[str, float]:
    tv: dict[str, float] = {}
    for r in snapshot:
        t = r["tranche"]
        tv[t] = tv.get(t, 0.0) + (r["market_value"] or 0.0)
    return tv


def _load_risk_contribs(conn, today: str) -> dict[int, float]:
    """instrument_id → risk_contrib_pct from latest risk_contribution row."""
    rows = conn.execute(
        """SELECT rc.instrument_id, rc.risk_contrib_pct
           FROM risk_contribution rc
           WHERE rc.calc_date = (
             SELECT MAX(calc_date) FROM risk_contribution
           )"""
    ).fetchall()
    return {r["instrument_id"]: float(r["risk_contrib_pct"]) for r in rows}


def _load_thesis_scores(conn) -> dict[int, float]:
    """instrument_id → current_score from theses."""
    rows = conn.execute(
        "SELECT instrument_id, current_score FROM theses WHERE current_score IS NOT NULL"
    ).fetchall()
    return {r["instrument_id"]: float(r["current_score"]) for r in rows}


def _load_alert_counts(conn, today: str, lookback_days: int = 30) -> dict[int, int]:
    """instrument_id → alert count in last 30 days."""
    cutoff = (date.fromisoformat(today) - timedelta(days=lookback_days)).isoformat()
    rows = conn.execute(
        """SELECT instrument_id, COUNT(*) AS cnt
           FROM alerts
           WHERE alert_date >= ? AND instrument_id IS NOT NULL
             AND (acknowledged IS NULL OR acknowledged = 0)
           GROUP BY instrument_id""",
        (cutoff,),
    ).fetchall()
    return {r["instrument_id"]: r["cnt"] for r in rows}


def _load_peak_prices(conn) -> dict[int, float]:
    """instrument_id → max close price (52-week high proxy for drawdown)."""
    rows = conn.execute(
        """SELECT instrument_id, MAX(close) AS peak
           FROM quotes
           WHERE quote_date >= date('now', '-365 days')
           GROUP BY instrument_id"""
    ).fetchall()
    return {r["instrument_id"]: float(r["peak"]) for r in rows}


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score_pnl(pnl_pct: float) -> float:
    """0–40 pts based on P&L."""
    if pnl_pct >= 0.20:
        return 40.0
    if pnl_pct >= 0.10:
        return 35.0
    if pnl_pct >= 0.00:
        return 28.0
    if pnl_pct >= -0.05:
        return 22.0
    if pnl_pct >= -0.10:
        return 15.0
    if pnl_pct >= -0.15:
        return 8.0
    if pnl_pct >= -0.20:
        return 3.0
    return 0.0


def _score_drawdown(drawdown_pct: Optional[float]) -> float:
    """Penalty deducted from pnl score. 0 = no extra penalty."""
    if drawdown_pct is None:
        return 0.0
    dd = abs(drawdown_pct)
    if dd < 0.10:
        return 0.0
    if dd < 0.15:
        return 5.0
    if dd < 0.20:
        return 10.0
    return 15.0


def _score_thesis(thesis_score: Optional[float]) -> float:
    """0–25 pts, scaled from 0–5 thesis score."""
    if thesis_score is None:
        return 12.5   # neutral when unknown
    return min(thesis_score / 5.0 * 25.0, 25.0)


def _score_risk_contrib(risk_contrib_pct: Optional[float]) -> float:
    """0–20 pts. Lower contribution = higher score."""
    if risk_contrib_pct is None:
        return 10.0   # neutral
    pct = risk_contrib_pct * 100  # e.g. 0.35 → 35%
    if pct <= 10:
        return 20.0
    if pct <= 20:
        return 15.0
    if pct <= 30:
        return 10.0
    if pct <= 40:
        return 5.0
    return 0.0


def _alert_penalty(alert_count: int) -> float:
    """Up to 15 pts penalty from alerts."""
    return min(alert_count * 5.0, 15.0)


def _label(score: float, alert_count: int, drawdown_pct: Optional[float]) -> str:
    dd = abs(drawdown_pct or 0.0)
    if score < 30 or dd > 0.20 or alert_count >= 3:
        return "act"
    if score < 50 or dd > 0.15 or (drawdown_pct is not None and dd > 0.10):
        return "review" if score < 50 else "watch"
    if score >= 70 and alert_count == 0 and dd < 0.10:
        return "healthy"
    return "watch"


def _suggested_action(label: str, code: str, drawdown_pct: Optional[float], thesis_score: Optional[float]) -> str:
    if label == "act":
        return f"紧急：立即检查 {code}，考虑减仓或止损"
    if label == "review":
        return f"审查：更新 {code} 论点评分，决定是否持有"
    if label == "watch":
        return f"观察：{code} 维持监控，下次月度复盘时重评"
    return f"{code} 状态健康，维持当前策略"


# ── Main computation ──────────────────────────────────────────────────────────

def compute_position_health(db_path=None) -> list[PositionHealthRecord]:
    """Compute health records for all current holdings."""
    today = date.today().isoformat()
    conn = connect(db_path)

    snapshot   = _load_snapshot(conn)
    total_val  = _load_total_value(snapshot)
    tranche_vals = _load_tranche_values(snapshot)
    risk_contribs = _load_risk_contribs(conn, today)
    thesis_scores = _load_thesis_scores(conn)
    alert_counts  = _load_alert_counts(conn, today)
    peak_prices   = _load_peak_prices(conn)
    conn.close()

    records: list[PositionHealthRecord] = []

    for row in snapshot:
        iid  = row["id"]
        code = row["code"]
        mv   = row["market_value"] or 0.0

        if mv <= 0:
            records.append(PositionHealthRecord(
                calc_date=today, instrument_id=iid, code=code,
                name=row["name"], tranche=row["tranche"],
                health_score=None, health_label="insufficient_data",
                pnl_pct=None, drawdown_pct=None,
                weight_total=None, weight_tranche=None,
                risk_contrib_pct=None, thesis_score=None, alert_count=0,
                suggested_action=None, insufficient_data=True,
            ))
            continue

        pnl_pct  = row["pnl_pct"]
        cost_prc = row["cost_price"] or 0.0
        cur_prc  = row["price"] or 0.0
        peak_prc = peak_prices.get(iid, cur_prc)

        # Drawdown from 52w peak
        drawdown_pct = (cur_prc / peak_prc - 1.0) if peak_prc > 0 else None

        weight_total   = mv / total_val if total_val > 0 else None
        tranche_total  = tranche_vals.get(row["tranche"], 0.0)
        weight_tranche = mv / tranche_total if tranche_total > 0 else None

        rc            = risk_contribs.get(iid)
        ts            = thesis_scores.get(iid)
        ac            = alert_counts.get(iid, 0)

        # Compute score
        base   = _score_pnl(pnl_pct or 0.0)
        dd_pen = _score_drawdown(drawdown_pct)
        t_pts  = _score_thesis(ts)
        r_pts  = _score_risk_contrib(rc)
        a_pen  = _alert_penalty(ac)
        score  = max(0.0, min(100.0, base - dd_pen + t_pts + r_pts - a_pen))

        label  = _label(score, ac, drawdown_pct)
        action = _suggested_action(label, code, drawdown_pct, ts)

        evidence = {
            "pnl_score": base, "dd_penalty": dd_pen,
            "thesis_score_pts": t_pts, "risk_contrib_pts": r_pts,
            "alert_penalty": a_pen,
        }

        records.append(PositionHealthRecord(
            calc_date=today, instrument_id=iid, code=code,
            name=row["name"], tranche=row["tranche"],
            health_score=round(score, 1),
            health_label=label,
            pnl_pct=pnl_pct, drawdown_pct=drawdown_pct,
            weight_total=weight_total, weight_tranche=weight_tranche,
            risk_contrib_pct=rc, thesis_score=ts, alert_count=ac,
            suggested_action=action, evidence=evidence,
        ))

    return records


def save_position_health(records: list[PositionHealthRecord], db_path=None) -> int:
    """Upsert all records into position_health. Returns count written."""
    if not records:
        return 0
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    count = 0
    with transaction(db_path) as conn:
        for r in records:
            conn.execute(
                """INSERT INTO position_health
                   (calc_date, instrument_id, health_score, health_label,
                    pnl_pct, drawdown_pct, weight_total, weight_tranche,
                    risk_contrib_pct, thesis_score, alert_count,
                    suggested_action, evidence_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(calc_date, instrument_id) DO UPDATE SET
                     health_score=excluded.health_score,
                     health_label=excluded.health_label,
                     pnl_pct=excluded.pnl_pct,
                     drawdown_pct=excluded.drawdown_pct,
                     weight_total=excluded.weight_total,
                     weight_tranche=excluded.weight_tranche,
                     risk_contrib_pct=excluded.risk_contrib_pct,
                     thesis_score=excluded.thesis_score,
                     alert_count=excluded.alert_count,
                     suggested_action=excluded.suggested_action,
                     evidence_json=excluded.evidence_json""",
                (r.calc_date, r.instrument_id, r.health_score, r.health_label,
                 r.pnl_pct, r.drawdown_pct, r.weight_total, r.weight_tranche,
                 r.risk_contrib_pct, r.thesis_score, r.alert_count,
                 r.suggested_action,
                 json.dumps(r.evidence, ensure_ascii=False), now),
            )
            count += 1
    return count


def run_position_health(db_path=None) -> list[PositionHealthRecord]:
    """Compute and persist position health. Returns records."""
    records = compute_position_health(db_path)
    save_position_health(records, db_path)
    return records


# ── Human message ─────────────────────────────────────────────────────────────

def build_health_summary(records: list[PositionHealthRecord]) -> str:
    if not records:
        return "暂无持仓健康数据。"

    lines = [f"## 持仓健康度 — {records[0].calc_date}\n"]
    lines.append("| 股票 | 健康标签 | 评分 | 盈亏% | 回撤% | 占总仓% |")
    lines.append("|------|---------|------|-------|-------|---------|")

    label_order = {"act": 0, "review": 1, "watch": 2,
                   "healthy": 3, "unknown": 4, "insufficient_data": 5}
    for r in sorted(records, key=lambda x: label_order.get(x.health_label, 9)):
        pnl   = f"{r.pnl_pct*100:+.1f}%" if r.pnl_pct is not None else "N/A"
        dd    = f"{r.drawdown_pct*100:+.1f}%" if r.drawdown_pct is not None else "N/A"
        wt    = f"{r.weight_total*100:.1f}%" if r.weight_total is not None else "N/A"
        score = f"{r.health_score:.0f}" if r.health_score is not None else "N/A"
        lines.append(f"| {r.name}（{r.code}）| {r.health_label} | {score} | {pnl} | {dd} | {wt} |")

    act_count = sum(1 for r in records if r.health_label == "act")
    if act_count:
        lines.append(f"\n⚠ {act_count} 个持仓需要立即处理。")
        lines.append("所以你该做什么：优先处理标记为 'act' 的持仓，检查是否需要减仓或止损。")

    return "\n".join(lines)
