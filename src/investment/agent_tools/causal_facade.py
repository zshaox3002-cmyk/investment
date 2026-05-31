"""Causal insight facade — Phase 6.

Wraps the existing causal engine so users get plain-language conclusions
without touching nodes, edges, or the approval workflow.

Key design decisions:
  - Anomaly detection: relative to benchmark (>2σ daily move vs 沪深300)
  - Scope classification: maps causal node layers to L1/L2/L3 scope
  - Credibility tiers: A/B act on it, C/D monitor/ignore
  - Confidence iteration: validation_status updated as evidence accumulates
  - User interface: zero causal graph operations required
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from investment.core.db import connect, transaction
from investment.agent_tools.translator import (
    fmt_pct, translate_causal_layer,
)


# ── Constants ─────────────────────────────────────────────────────────────────

# Impact level → credibility tier mapping
# L5/L4 = high impact → A/B tier; L3 = medium → B/C; L1/L2 = low → C/D
_IMPACT_TO_CREDIBILITY: dict[str, str] = {
    "L5": "A",
    "L4": "A",
    "L3": "B",
    "L2": "C",
    "L1": "D",
}

# Causal node layer → scope layer
_NODE_LAYER_TO_SCOPE: dict[str, str] = {
    "L0_geopolitical": "L1_macro",
    "L1_macro":        "L1_macro",
    "L2_industry":     "L2_sector",
    "L3_holding":      "L3_holding",
}

_CREDIBILITY_LABELS: dict[str, str] = {
    "A": "高可信（建议行动）",
    "B": "中可信（持续关注）",
    "C": "低可信（仅记录）",
    "D": "噪音（可忽略）",
}

_DIRECTION_LABELS: dict[str, str] = {
    "positive": "利好",
    "negative": "利空",
    "neutral":  "中性",
}

_VALIDATION_LABELS: dict[str, str] = {
    "open":      "待验证",
    "confirmed": "已确认",
    "refuted":   "已否定",
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class CausalInsight:
    assessment_id: int
    holding_code: str
    holding_name: str
    date: str
    impact_level: str
    direction: str
    scope_layer: str
    credibility_tier: str
    validation_status: str
    narrative: str          # LLM-generated narrative (may be empty)
    # Human-readable fields
    direction_label: str
    credibility_label: str
    scope_label: str
    validation_label: str
    action_required: str


@dataclass
class CausalInsightReport:
    as_of: str
    actionable: list[CausalInsight]    # A/B tier
    monitoring: list[CausalInsight]    # C tier
    anomalies_detected: list[str]      # codes with >2σ moves
    human_message: str
    total_signals: int = 0


# ── Anomaly detection ─────────────────────────────────────────────────────────

def detect_anomalies(conn, as_of: str, sigma_threshold: float = 2.0) -> list[str]:
    """Return codes of holdings with abnormal price moves vs recent history."""
    # Get today's change_pct for all holdings
    rows = conn.execute(
        """SELECT i.code, q.change_pct
           FROM quotes q
           JOIN instruments i ON i.id = q.instrument_id
           WHERE q.quote_date = ?
             AND i.tranche IN ('B','C') AND i.active = 1
             AND q.change_pct IS NOT NULL""",
        (as_of,),
    ).fetchall()

    anomalies: list[str] = []
    for row in rows:
        code = row["code"]
        today_pct = float(row["change_pct"])

        # Get recent history (last 20 days excluding today)
        hist = conn.execute(
            """SELECT change_pct FROM quotes q
               JOIN instruments i ON i.id = q.instrument_id
               WHERE i.code = ? AND q.quote_date < ?
                 AND q.change_pct IS NOT NULL
               ORDER BY q.quote_date DESC LIMIT 20""",
            (code, as_of),
        ).fetchall()

        if len(hist) < 5:
            # Not enough history — flag if move > 3%
            if abs(today_pct) > 0.03:
                anomalies.append(code)
            continue

        import numpy as np
        hist_vals = [float(r["change_pct"]) for r in hist]
        mean = float(np.mean(hist_vals))
        std = float(np.std(hist_vals, ddof=1))
        if std < 1e-6:
            continue
        z = (today_pct - mean) / std
        if abs(z) >= sigma_threshold:
            anomalies.append(code)

    return anomalies


# ── Assessment loading and enrichment ────────────────────────────────────────

def _load_assessments(conn, as_of: str) -> list[dict]:
    """Load today's chain_assessments with instrument names."""
    rows = conn.execute(
        """SELECT ca.assessment_id, ca.holding_code, ca.impact_level,
                  ca.direction, ca.narrative_md, ca.validation_status,
                  ca.revision_log, ca.scope_layer, ca.credibility_tier,
                  COALESCE(i.name, ca.holding_code) AS holding_name
           FROM chain_assessments ca
           LEFT JOIN instruments i ON i.code = ca.holding_code
           WHERE ca.date = ?
           ORDER BY
             CASE ca.credibility_tier WHEN 'A' THEN 0 WHEN 'B' THEN 1 WHEN 'C' THEN 2 ELSE 3 END,
             ca.impact_score DESC""",
        (as_of,),
    ).fetchall()
    return [dict(r) for r in rows]


def _enrich_assessment(row: dict) -> CausalInsight:
    """Convert a raw DB row to a CausalInsight with human-readable fields."""
    impact = row.get("impact_level", "L1")
    direction = row.get("direction", "neutral")
    scope = row.get("scope_layer") or _NODE_LAYER_TO_SCOPE.get("L1_macro", "L1_macro")
    credibility = row.get("credibility_tier") or _IMPACT_TO_CREDIBILITY.get(impact, "C")
    validation = row.get("validation_status", "open")

    action = _build_action(credibility, direction, row["holding_code"])

    return CausalInsight(
        assessment_id=row["assessment_id"],
        holding_code=row["holding_code"],
        holding_name=row.get("holding_name", row["holding_code"]),
        date=row.get("date", ""),
        impact_level=impact,
        direction=direction,
        scope_layer=scope,
        credibility_tier=credibility,
        validation_status=validation,
        narrative=row.get("narrative_md", ""),
        direction_label=_DIRECTION_LABELS.get(direction, direction),
        credibility_label=_CREDIBILITY_LABELS.get(credibility, credibility),
        scope_label=translate_causal_layer(scope),
        validation_label=_VALIDATION_LABELS.get(validation, validation),
        action_required=action,
    )


def _build_action(credibility: str, direction: str, code: str) -> str:
    if credibility == "A":
        if direction == "negative":
            return (
                f"利空信号可信度高，建议审查 {code} 的持仓逻辑，"
                "若 thesis 受损则触发减仓决策"
            )
        elif direction == "positive":
            return f"利好信号可信度高，关注 {code} 是否有加仓机会"
        else:
            return f"重要信号，密切关注 {code} 后续走势"
    elif credibility == "B":
        return f"持续关注 {code} 相关事件发展，积累更多证据后再决策"
    else:
        return "记录在案，暂不需要操作"


# ── Confidence update ─────────────────────────────────────────────────────────

def update_validation_status(
    assessment_id: int,
    new_status: str,
    reason: str = "",
    db_path=None,
) -> bool:
    """Update validation_status and append to revision_log. Returns True on success."""
    if new_status not in ("open", "confirmed", "refuted"):
        return False

    with transaction(db_path) as conn:
        row = conn.execute(
            "SELECT validation_status, revision_log FROM chain_assessments WHERE assessment_id=?",
            (assessment_id,),
        ).fetchone()
        if not row:
            return False

        old_status = row["validation_status"]
        try:
            log = json.loads(row["revision_log"] or "[]")
        except (json.JSONDecodeError, TypeError):
            log = []

        log.append({
            "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "old_status": old_status,
            "new_status": new_status,
            "reason": reason,
        })

        conn.execute(
            "UPDATE chain_assessments SET validation_status=?, revision_log=? WHERE assessment_id=?",
            (new_status, json.dumps(log, ensure_ascii=False), assessment_id),
        )
    return True


def backfill_credibility_tiers(db_path=None) -> int:
    """Backfill credibility_tier and scope_layer for existing assessments."""
    updated = 0
    with transaction(db_path) as conn:
        rows = conn.execute(
            """SELECT ca.assessment_id, ca.impact_level,
                      (SELECT layer FROM causal_nodes cn
                       WHERE cn.name LIKE ca.holding_code || '-%'
                       LIMIT 1) AS node_layer
               FROM chain_assessments ca
               WHERE ca.credibility_tier = 'C'"""
        ).fetchall()

        for row in rows:
            impact = row["impact_level"] or "L1"
            tier = _IMPACT_TO_CREDIBILITY.get(impact, "C")
            node_layer = row["node_layer"] or "L3_holding"
            scope = _NODE_LAYER_TO_SCOPE.get(node_layer, "L3_holding")
            conn.execute(
                "UPDATE chain_assessments SET credibility_tier=?, scope_layer=? WHERE assessment_id=?",
                (tier, scope, row["assessment_id"]),
            )
            updated += 1
    return updated


# ── Human message builder ─────────────────────────────────────────────────────

def _build_human_message(report: CausalInsightReport) -> str:
    lines = [f"## 因果归因报告 — {report.as_of}\n"]

    # Anomalies
    if report.anomalies_detected:
        lines.append(
            f"### 异动检测\n"
            f"以下持仓今日出现异常波动：**{'、'.join(report.anomalies_detected)}**\n"
            "已自动触发因果路径分析。\n"
        )

    # Actionable insights (A/B tier)
    if report.actionable:
        lines.append(f"### ⚡ 需要关注的信号（{len(report.actionable)} 条）\n")
        for ins in report.actionable:
            lines.append(
                f"**{ins.holding_name}（{ins.holding_code}）** — "
                f"{ins.direction_label} · {ins.scope_label} · {ins.credibility_label}"
            )
            if ins.narrative:
                # Show first 200 chars of narrative
                snippet = ins.narrative[:200].replace("\n", " ")
                if len(ins.narrative) > 200:
                    snippet += "..."
                lines.append(f"> {snippet}")
            lines.append(f"所以你该做什么：{ins.action_required}")
            lines.append(f"验证状态：{ins.validation_label}（ID: {ins.assessment_id}）")
            lines.append("")
    else:
        lines.append("### ✅ 无高可信信号\n今日无需关注的因果信号。\n")

    # Monitoring signals (C tier, summarised)
    if report.monitoring:
        lines.append(f"### 📋 监控中的信号（{len(report.monitoring)} 条，暂不需要操作）")
        for ins in report.monitoring[:3]:
            lines.append(
                f"- {ins.holding_name}（{ins.holding_code}）：{ins.direction_label} · {ins.scope_label}"
            )
        if len(report.monitoring) > 3:
            lines.append(f"- ...还有 {len(report.monitoring) - 3} 条")
        lines.append("")

    # How to update confidence
    if report.actionable:
        lines.append(
            "### 如何更新置信度\n"
            "当后续事件证实或否定上述信号时，运行：\n"
            "```\n"
            "inv causal validate <assessment_id> --status confirmed|refuted --reason '理由'\n"
            "```\n"
            "置信度会自动更新，影响后续评估权重。"
        )

    return "\n".join(lines)


# ── Main entry point ──────────────────────────────────────────────────────────

def run_causal_insight(
    as_of: Optional[str] = None,
    holding_code: Optional[str] = None,
    db_path=None,
) -> CausalInsightReport:
    """Load today's causal assessments, enrich with scope/credibility, return human report.

    This is the zero-operation user interface: no graph editing required.
    """
    today = as_of or date.today().isoformat()
    conn = connect(db_path)

    # Detect anomalies
    anomalies = detect_anomalies(conn, today)

    # Load assessments
    raw = _load_assessments(conn, today)
    conn.close()

    # Filter by holding_code if specified
    if holding_code:
        raw = [r for r in raw if r["holding_code"] == holding_code]

    # Enrich
    insights = [_enrich_assessment(r) for r in raw]

    # Backfill credibility for any assessments that still have default 'C'
    backfill_credibility_tiers(db_path)

    # Split by tier
    actionable = [i for i in insights if i.credibility_tier in ("A", "B")]
    monitoring = [i for i in insights if i.credibility_tier == "C"]

    report = CausalInsightReport(
        as_of=today,
        actionable=actionable,
        monitoring=monitoring,
        anomalies_detected=anomalies,
        human_message="",
        total_signals=len(insights),
    )
    report.human_message = _build_human_message(report)
    return report
