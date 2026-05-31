"""Daily chain assessment engine.

Orchestrates: fetch signals → path search → compute impact → grade → LLM narrative → persist.

Usage::

    from investment.causal.assessor import assess_holdings
    results = assess_holdings(date="2026-05-27")
"""
from __future__ import annotations

import json
from datetime import date as dt_date
from pathlib import Path
from typing import Optional

from investment.core.db import connect
from investment.core.settings import DB_PATH, CAUSAL_PROMPTS_DIR
from investment.core.llm import call_llm_with_schema

from .models import AssessmentOutput, SignalImpactItem
from .repo import CausalRepo, _utcnow
from .path_engine import (
    find_paths,
    compute_path_impact,
    aggregate_multi_paths,
    grade_impact,
    CausalPath,
    PathImpact,
)


_NARRATOR_PROMPT_PATH = CAUSAL_PROMPTS_DIR / "assessment-narrator.md"


def _load_narrator_template() -> str:
    if _NARRATOR_PROMPT_PATH.exists():
        return _NARRATOR_PROMPT_PATH.read_text(encoding="utf-8")
    return _FALLBACK_NARRATOR_PROMPT


def _get_holding_context(conn, code: str, date: str) -> str:
    """Build a snapshot string for the holding: price change, fundamentals."""
    row = conn.execute(
        """SELECT i.code, i.name, q.close, q.change_pct
           FROM instruments i
           LEFT JOIN quotes q ON q.instrument_id = i.id
             AND q.quote_date = (SELECT MAX(quote_date) FROM quotes WHERE instrument_id = i.id AND quote_date <= ?)
           WHERE i.code = ? AND i.active = 1""",
        (date, code),
    ).fetchone()

    if not row:
        return f"代码：{code}（无当日行情数据）"

    pct_str = f"{row['change_pct']*100:+.2f}%" if row["change_pct"] else "N/A"
    return (
        f"- 代码：{row['code']}\n"
        f"- 名称：{row['name']}\n"
        f"- 当日收盘：{row['close'] or 'N/A'}\n"
        f"- 当日涨跌幅：{pct_str}\n"
    )


def _get_current_holdings(conn, date: str) -> list[dict]:
    """Get latest holdings with instrument codes, newest effective_date <= date."""
    rows = conn.execute(
        """SELECT DISTINCT i.code, i.name
           FROM holdings h
           JOIN instruments i ON h.instrument_id = i.id
           WHERE h.effective_date <= ?
             AND h.shares > 0
           ORDER BY i.code""",
        (date,),
    ).fetchall()
    return [{"code": r["code"], "name": r["name"]} for r in rows]


def _get_today_signals(conn, date: str, min_confidence: float = 0.5) -> list[dict]:
    """Fetch today's news_signals with confidence >= threshold."""
    rows = conn.execute(
        """SELECT signal_id, affected_node_ids, signal_strength, confidence,
                  title, summary
           FROM news_signals
           WHERE date = ? AND confidence >= ?
           ORDER BY signal_strength DESC""",
        (date, min_confidence),
    ).fetchall()
    return [dict(r) for r in rows]


def _find_holding_node(repo: CausalRepo, code: str):
    """Find the L3 causal node for a holding code (e.g. '600219')."""
    with repo.transaction():
        nodes = repo.list_nodes(layer="L3_holding")
    for n in nodes:
        if n.name.startswith(f"{code}-"):
            return n
    return None


def assess_holdings(
    date: str | None = None,
    holding_code: str | None = None,
    db_path: Path | None = None,
    min_confidence: float = 0.5,
) -> list[dict]:
    """Run daily chain assessment for all holdings (or a single one).

    Returns a list of assessment result dicts with keys:
    {holding_code, impact_score, impact_level, direction, paths_count, narrative_md}.
    Only L3+ assessments are persisted.
    """
    db_path = db_path or DB_PATH
    target_date = date or dt_date.today().isoformat()
    conn = connect(db_path)
    repo = CausalRepo(db_path)

    # 1. Get signals for today
    try:
        signals = _get_today_signals(conn, target_date, min_confidence)
    finally:
        conn.close()

    if not signals:
        return []

    # 2. Get holdings
    conn = connect(db_path)
    try:
        holdings = _get_current_holdings(conn, target_date)
    finally:
        conn.close()

    if holding_code:
        holdings = [h for h in holdings if h["code"] == holding_code]
    if not holdings:
        return []

    # 3. For each holding, assess impact
    results: list[dict] = []
    for h in holdings:
        code = h["code"]
        holding_node = _find_holding_node(repo, code)
        if not holding_node:
            continue

        # 4. Collect all paths from signal-affected nodes → holding node
        all_path_impacts: list[PathImpact] = []
        triggering_signal_ids: list[int] = []
        path_details: list[dict] = []

        with repo.transaction():
            for sig in signals:
                affected_ids = json.loads(sig["affected_node_ids"] or "[]")
                for affected_id in affected_ids:
                    paths = find_paths(
                        repo, affected_id, holding_node.node_id, max_hops=6,
                    )
                    for path in paths:
                        impact = compute_path_impact(path, sig["signal_strength"])
                        all_path_impacts.append(PathImpact(path=path, impact=impact))
                        path_details.append({
                            "node_sequence": path.node_sequence,
                            "edge_strengths": [e.strength for e in path.edges],
                            "impact_contribution": impact,
                            "signal_title": sig["title"],
                            "signal_strength": sig["signal_strength"],
                        })
                if affected_ids:
                    triggering_signal_ids.append(sig["signal_id"])

        if not all_path_impacts:
            continue

        # 5. Compute aggregate score + grade
        total_impact = aggregate_multi_paths(all_path_impacts)
        level, direction = grade_impact(total_impact)

        # 6. LLM narrative (L3+ only)
        narrative_full: dict = {}
        narrative_md = ""
        if level in ("L3", "L4", "L5"):
            try:
                ctx_conn = connect(db_path)
                try:
                    holding_ctx = _get_holding_context(ctx_conn, code, target_date)
                finally:
                    ctx_conn.close()
                narrative_full = _generate_narrative(
                    holding_code=code,
                    holding_name=h["name"],
                    impact_score=total_impact,
                    impact_level=level,
                    direction=direction,
                    path_details=path_details,
                    signal_summaries=[s["summary"] for s in signals if s["summary"]],
                    holding_context=holding_ctx,
                )
                narrative_md = narrative_full.get("narrative_md", "")
            except Exception:
                narrative_md = (
                    f"**{code} {h['name']}**\n\n"
                    f"影响等级：{level} | 方向：{direction} | 综合得分：{total_impact:.3f}\n\n"
                    f"共发现 {len(all_path_impacts)} 条传导路径。建议观察。"
                )
                narrative_full = {}

        # 7. Persist (L3+)
        if level in ("L3", "L4", "L5"):
            conn2 = connect(db_path)
            try:
                conn2.execute(
                    """INSERT OR REPLACE INTO chain_assessments
                       (date, holding_code, impact_score, impact_level, direction,
                        paths_json, triggering_signal_ids, narrative_md,
                        divergence_warning, framework_dominant, timeframe_short, timeframe_medium)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        target_date, code, total_impact, level, direction,
                        json.dumps(path_details, ensure_ascii=False),
                        json.dumps(triggering_signal_ids),
                        narrative_md,
                        narrative_full.get("divergence_warning", ""),
                        narrative_full.get("framework_dominant", ""),
                        narrative_full.get("timeframe_short", ""),
                        narrative_full.get("timeframe_medium", ""),
                    ),
                )
                conn2.commit()
            finally:
                conn2.close()

        results.append({
            "holding_code": code,
            "holding_name": h["name"],
            "impact_score": total_impact,
            "impact_level": level,
            "direction": direction,
            "paths_count": len(all_path_impacts),
            "narrative_md": narrative_md,
            "path_details": path_details,
            "triggering_signal_ids": triggering_signal_ids,
        })

    return results


def _generate_narrative(
    holding_code: str,
    holding_name: str,
    impact_score: float,
    impact_level: str,
    direction: str,
    path_details: list[dict],
    signal_summaries: list[str],
    holding_context: str = "",
) -> dict:
    """Call LLM to generate a narrative. Returns full AssessmentOutput as dict."""
    path_lines = []
    for i, pd in enumerate(path_details):
        seq = " → ".join(pd["node_sequence"])
        path_lines.append(
            f"路径{i+1}: {seq} | 贡献={pd['impact_contribution']:.3f} | "
            f"信号={pd.get('signal_title', '?')[:40]}"
        )

    signal_text = "\n".join(f"  - {s}" for s in signal_summaries[:5])

    direction_desc = {"positive": "上涨/利好", "negative": "下跌/利空", "neutral": "无方向"}
    template = _load_narrator_template()
    prompt = template.replace("{holding_code}", holding_code)
    prompt = prompt.replace("{holding_name}", holding_name)
    prompt = prompt.replace("{impact_score}", f"{impact_score:.3f}")
    prompt = prompt.replace("{impact_level}", impact_level)
    prompt = prompt.replace("{direction}", direction)
    prompt = prompt.replace("{path_details}", "\n".join(path_lines))
    prompt = prompt.replace("{signal_summaries}", signal_text)
    prompt = prompt.replace("{holding_context}", holding_context or f"代码：{holding_code} 名称：{holding_name}")
    prompt = prompt.replace("{direction_desc}", direction_desc.get(direction, "未知"))

    try:
        result = call_llm_with_schema(
            prompt,
            AssessmentOutput,
            system_prompt="你是宏观策略师兼行业分析师，生成持仓影响评估叙述。",
            max_retries=2,
        )
        return result.model_dump()
    except Exception:
        raise


# ── Fallback prompt ───────────────────────────────────────────────────────

_FALLBACK_NARRATOR_PROMPT = """你是宏观量化分析师。基于今天的新闻信号和因果图谱，为以下持仓生成影响评估。

## 持仓信息
- 股票代码：{holding_code}
- 股票名称：{holding_name}
- 综合影响得分：{impact_score}
- 影响等级：{impact_level}
- 方向：{direction}

## 触发路径
{path_details}

## 相关新闻
{signal_summaries}

## 输出
JSON: {{
  "narrative_md": "500字以内的中文叙述",
  "direction": "positive|negative|neutral",
  "impact_level": "L1-L5",
  "key_nodes": ["关键节点"],
  "suggested_action": "建议观察|建议加入复盘清单|建议更新 thesis"
}}

硬约束：
- 严禁输出买卖建议
- 只允许输出：建议观察、建议加入复盘清单、建议更新 thesis
"""
