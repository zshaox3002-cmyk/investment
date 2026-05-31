"""Causal discoverer: AI-powered causal path discovery from holding events.

Usage::

    from investment.causal.discoverer import discover_causal_paths
    paths = discover_causal_paths(
        event="南山铝业单日下跌7%",
        holding_code="600219",
    )
"""
from __future__ import annotations

import json
from datetime import date as dt_date
from datetime import timedelta
from pathlib import Path

from investment.core.db import connect
from investment.core.settings import CAUSAL_PROMPTS_DIR, DB_PATH
from investment.core.llm import call_llm_with_schema
from .models import DiscovererOutput, ProposedPath, ProposedEdgeInPath, ProposedNode
from .repo import CausalRepo

_PROMPT_PATH = CAUSAL_PROMPTS_DIR / "causal-discoverer.md"
_PROMPT_TEMPLATE: str | None = None


def _load_template() -> str:
    global _PROMPT_TEMPLATE
    if _PROMPT_TEMPLATE is None:
        if _PROMPT_PATH.exists():
            _PROMPT_TEMPLATE = _PROMPT_PATH.read_text(encoding="utf-8")
        else:
            _PROMPT_TEMPLATE = _FALLBACK_TEMPLATE
    return _PROMPT_TEMPLATE


def discover_causal_paths(
    event: str,
    holding_code: str,
    lookback_days: int = 7,
    news_summaries: str = "",
    db_path: Path | None = None,
) -> list[ProposedPath]:
    """Discover causal paths for a holding event.

    Args:
        event: Description of the trigger event (e.g. "600219单日下跌7%")
        holding_code: Stock code
        lookback_days: Days of price history to include
        news_summaries: Manual news input (Phase 4 will auto-fetch)
        db_path: Optional DB path override

    Returns:
        List of proposed causal paths written to pending_edges.
    """
    db_path = db_path or DB_PATH

    # 1. Gather context data
    holding_name, price_change, price_context = _get_price_context(
        db_path, holding_code, lookback_days
    )
    if not holding_name:
        holding_name = holding_code

    subgraph = _get_subgraph_context(db_path, holding_name)

    # 2. Render prompt
    template = _load_template()
    prompt = _render_prompt(
        template,
        event_description=event,
        holding_code=holding_code,
        holding_name=holding_name,
        price_change=price_context or price_change,
        news_summaries=news_summaries or "暂无相关新闻摘要（Phase 4 将接入实时新闻源）",
        existing_graph_subgraph=subgraph or "（图谱中尚无该标的的因果链路，请从头构建）",
    )

    # 3. Call LLM with schema validation
    system_prompt = (
        "你是一位量化分析师兼宏观研究员，擅长从价格异动反向推理因果传导路径。"
        "请严格按 JSON schema 输出，不要添加任何解释文字。"
    )
    result = call_llm_with_schema(
        prompt,
        DiscovererOutput,
        system_prompt=system_prompt,
        max_retries=3,
    )

    # 4. Write to pending_edges (dedup)
    repo = CausalRepo(db_path)
    with repo.transaction():
        for path in result.paths:
            _write_path_to_pending(repo, path, event)

    return result.paths


def discover_auto(
    volatility_pct: float = 5.0,
    lookback_days: int = 3,
    db_path: Path | None = None,
) -> dict[str, list[ProposedPath]]:
    """Auto-scan: find all holdings with recent moves > volatility_pct.

    Returns {code: [paths]} mapping.
    """
    db_path = db_path or DB_PATH
    candidates = _find_volatile_holdings(db_path, volatility_pct, lookback_days)
    results: dict[str, list[ProposedPath]] = {}

    for code, name, change_pct in candidates:
        event = f"{code}-{name} 近{lookback_days}日波动 {change_pct * 100:+.1f}%"
        try:
            paths = discover_causal_paths(
                event=event,
                holding_code=code,
                lookback_days=lookback_days,
                db_path=db_path,
            )
            if paths:
                results[code] = paths
        except Exception:
            # Log and continue — one failure shouldn't block others
            continue

    return results


# ── Internal helpers ──────────────────────────────────────────────────────

def _get_price_context(
    db_path: Path, code: str, lookback_days: int
) -> tuple[str, str, str]:
    """Return (holding_name, latest_change, context_str) from quotes/holdings."""
    conn = connect(db_path)
    try:
        # Get instrument name
        row = conn.execute(
            "SELECT name FROM instruments WHERE code = ? AND active = 1", (code,)
        ).fetchone()
        holding_name = row["name"] if row else ""

        # Get recent quotes
        rows = conn.execute(
            """SELECT quote_date, close, change_pct FROM quotes q
               JOIN instruments i ON q.instrument_id = i.id
               WHERE i.code = ? AND i.active = 1
               ORDER BY quote_date DESC LIMIT ?""",
            (code, lookback_days + 1),
        ).fetchall()

        if not rows:
            return holding_name, "", "无近期价格数据"

        latest = rows[0]
        change_str = f"{latest['change_pct']:+.2f}%" if latest["change_pct"] else "N/A"

        lines = []
        for r in rows:
            chg = f"{r['change_pct']:+.2f}%" if r["change_pct"] else "—"
            lines.append(f"  {r['quote_date']}: close={r['close']:.2f} chg={chg}")
        context = "\n".join(lines)
        return holding_name, change_str, context
    finally:
        conn.close()


def _get_subgraph_context(db_path: Path, holding_name: str) -> str:
    """Return a text representation of the existing subgraph."""
    repo = CausalRepo(db_path)
    with repo.transaction():
        sg = repo.get_subgraph(holding_name, hops=2)
    if not sg["nodes"]:
        return ""

    lines = ["## 现有图谱节点"]
    for n in sg["nodes"]:
        lines.append(f"  - [{n.layer}] {n.name} ({n.node_type}): {n.description or '—'}")
    lines.append("## 现有图谱边")
    for e in sg["edges"]:
        lines.append(
            f"  - {e.from_name} → {e.to_name} "
            f"(dir={e.direction:+d}, strength={e.strength:.2f}" if e.strength else "(dir=..., strength=...)" +
            f", lag={e.lag_days}d)"
        )
    return "\n".join(lines)


def _render_prompt(template: str, **kwargs) -> str:
    """Simple placeholder substitution: {key} → value."""
    result = template
    for key, value in kwargs.items():
        result = result.replace("{" + key + "}", str(value))
    return result


def _write_path_to_pending(repo: CausalRepo, path: ProposedPath, event: str) -> int:
    """Write all edges from a proposed path to pending_edges. Returns count written."""
    count = 0
    for edge in path.edges:
        if repo.pending_edge_exists(edge.from_node_name, edge.to_node_name):
            continue

        # Determine proposed type/layer for nodes
        from_type, from_layer = _find_node_info(path.nodes, edge.from_node_name)
        to_type, to_layer = _find_node_info(path.nodes, edge.to_node_name)

        repo.add_pending_edge(
            from_node_name=edge.from_node_name,
            to_node_name=edge.to_node_name,
            direction=edge.direction,
            from_node_proposed_type=from_type if not repo.node_exists(edge.from_node_name) else None,
            from_node_proposed_layer=from_layer if not repo.node_exists(edge.from_node_name) else None,
            to_node_proposed_type=to_type if not repo.node_exists(edge.to_node_name) else None,
            to_node_proposed_layer=to_layer if not repo.node_exists(edge.to_node_name) else None,
            d1=edge.d1_directness,
            d2=edge.d2_elasticity,
            d3=edge.d3_consistency,
            d4=edge.d4_speed,
            d5=edge.d5_uniqueness,
            lag_days=edge.lag_days,
            confidence=edge.confidence,
            evidence_summary=edge.evidence_summary,
            evidence_urls=edge.evidence_urls,
            triggered_by_event=event,
        )
        count += 1
    return count


def _find_node_info(nodes: list[ProposedNode], name: str) -> tuple[str | None, str | None]:
    for n in nodes:
        if n.name == name:
            return n.node_type, n.layer
    return None, None


def _find_volatile_holdings(
    db_path: Path, threshold_pct: float, lookback_days: int
) -> list[tuple[str, str, float]]:
    """Find holdings with recent absolute change > threshold_pct."""
    conn = connect(db_path)
    try:
        cutoff = (dt_date.today() - timedelta(days=lookback_days)).isoformat()
        rows = conn.execute(
            """SELECT i.code, i.name,
                      MAX(ABS(q.change_pct)) AS max_change
               FROM quotes q
               JOIN instruments i ON q.instrument_id = i.id
               WHERE i.active = 1 AND i.asset_class = 'STOCK'
                 AND q.quote_date >= ?
                 AND i.tranche IN ('C', 'D')
               GROUP BY i.id
               HAVING MAX(ABS(q.change_pct)) > ?""",
            (cutoff, threshold_pct / 100.0),
        ).fetchall()
        return [(r["code"], r["name"], r["max_change"]) for r in rows]
    finally:
        conn.close()


# ── Fallback prompt template (when file not found) ────────────────────────

_FALLBACK_TEMPLATE = """# Causal Discoverer

## 角色
你是量化分析师兼宏观研究员，从价格异动反向推理因果传导路径。

## 输入
事件: {event_description}
持仓: {holding_code} {holding_name}
价格变动: {price_change}
新闻: {news_summaries}
现有图谱: {existing_graph_subgraph}

## 约束
路径2-7跳，L0/L1→L3。节点命名:
- L0: <地区>-<事件类型>
- L1: <指标名>
- L2: <行业>-<子方向>
- L3: <代码>-<简称>

## 输出格式
严格JSON: {"paths": [{"narrative": "...", "nodes": [...], "edges": [...]}]}
"""
