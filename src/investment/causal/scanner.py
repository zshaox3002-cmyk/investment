"""Daily news signal scanner.

Fetches news → deduplicates → LLM classifies → writes signals → updates activation scores.

Usage::

    from investment.causal.scanner import scan, update_lifecycle
    scan(date="2026-05-27")
    update_lifecycle()
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date as dt_date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

from investment.core.db import connect
from investment.core.settings import CONFIG_DIR, CAUSAL_PROMPTS_DIR, DB_PATH
from investment.core.llm import call_llm_with_schema

from .models import (
    RawNews, NewsSignal, NodeActivationLog,
    SignalImpactItem, SignalImpactOutput,
    CausalNode,
)
from .repo import CausalRepo, _utcnow
from .news_sources import CailiansheSource, WallstreetcnSource


_SOURCES_CONFIG_PATH = CONFIG_DIR / "causal_sources.yaml"
_PROMPT_PATH = CAUSAL_PROMPTS_DIR / "signal-impact-analyzer.md"


def _load_sources_config() -> dict:
    if _SOURCES_CONFIG_PATH.exists():
        return yaml.safe_load(_SOURCES_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return {}


def _load_prompt_template() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8")
    return _FALLBACK_SIGNAL_PROMPT


def _get_enabled_sources() -> list:
    cfg = _load_sources_config()
    sources_cfg = cfg.get("sources", {})
    batch_cfg = cfg.get("batch", {})
    max_articles = batch_cfg.get("max_articles_per_source", 50)

    sources = []
    for name, sc in sources_cfg.items():
        if not sc.get("enabled", False):
            continue
        source_config = {**sc, "max_articles_per_source": max_articles}
        if name == "cailianshe":
            sources.append(CailiansheSource(source_config))
        elif name == "wallstreetcn":
            sources.append(WallstreetcnSource(source_config))
    return sources


def scan(
    date: str | None = None,
    dry_run: bool = False,
    db_path: Path | None = None,
) -> dict:
    """Run the daily signal scan pipeline.

    Returns ``{fetched, deduped, classified, signals_written, nodes_updated}``.
    """
    db_path = db_path or DB_PATH
    target_date = date or dt_date.today().isoformat()
    cfg = _load_sources_config()
    batch_cfg = cfg.get("batch", {})
    activation_cfg = cfg.get("activation", {})
    min_confidence = batch_cfg.get("min_confidence", 0.5)
    max_nodes_per_article = batch_cfg.get("max_nodes_per_article", 5)

    # 1. Fetch news from all enabled sources
    all_news: list[RawNews] = []
    enabled_sources = _get_enabled_sources()
    for source in enabled_sources:
        try:
            articles = source.fetch(target_date)
            all_news.extend(articles)
        except Exception:
            continue

    result = {"fetched": len(all_news), "deduped": 0, "classified": 0,
              "signals_written": 0, "nodes_updated": 0}

    if not all_news:
        return result

    # 2. Dedup by content_hash against existing signals
    repo = CausalRepo(db_path)
    conn = connect(db_path)
    try:
        existing_hashes = set(
            r[0] for r in conn.execute(
                "SELECT raw_content_hash FROM news_signals WHERE date = ?", (target_date,)
            ).fetchall()
        )
    finally:
        conn.close()

    unique_news = [n for n in all_news if n.content_hash not in existing_hashes]
    result["deduped"] = len(unique_news)

    if not unique_news:
        return result

    # 3. Load active nodes
    with repo.transaction():
        active_nodes = repo.list_nodes(state="active")
    if not active_nodes:
        return result

    # 4. LLM classification
    try:
        signals = _classify_news(unique_news, active_nodes, min_confidence, max_nodes_per_article)
    except Exception:
        return result

    result["classified"] = len(signals)

    if dry_run:
        return result

    # 5. Write signals + update activation
    with repo.transaction():
        for sig in signals:
            _write_signal(repo, sig, target_date)

        result["signals_written"] = len(signals)

        # Update activation for affected nodes
        decay_rate = activation_cfg.get("decay_rate", 0.03)
        signal_weight_mult = activation_cfg.get("signal_weight_mult", 20)
        updated = _update_activations(
            repo, signals, target_date, decay_rate, signal_weight_mult,
        )
        result["nodes_updated"] = len(updated)

    return result


def update_lifecycle(db_path: Path | None = None) -> dict:
    """Apply activation decay and lifecycle state transitions.

    Returns ``{decayed, dormant, archived, reactivated}``.
    """
    db_path = db_path or DB_PATH
    cfg = _load_sources_config()
    activation_cfg = cfg.get("activation", {})
    dormant_after = activation_cfg.get("dormant_after_days", 30)
    archive_after = activation_cfg.get("archive_after_days", 180)
    decay_rate = activation_cfg.get("decay_rate", 0.03)

    repo = CausalRepo(db_path)
    result = {"decayed": 0, "dormant": 0, "archived": 0, "reactivated": 0}
    today = dt_date.today()

    with repo.transaction():
        all_nodes = repo.list_nodes()

        for node in all_nodes:
            if node.lifecycle_state == "archived":
                continue

            # Calculate days since last signal
            days_since = 999
            if node.last_signal_at:
                try:
                    last_date = dt_date.fromisoformat(node.last_signal_at[:10])
                    days_since = (today - last_date).days
                except (ValueError, TypeError):
                    pass

            # Apply decay
            old_score = node.activation_score or 0.0
            new_score = max(0.0, old_score * math.exp(-decay_rate * min(days_since, 365)))
            if abs(new_score - old_score) > 0.01:
                repo._conn.execute(
                    "UPDATE causal_nodes SET activation_score=?, updated_at=? WHERE node_id=?",
                    (new_score, _utcnow(), node.node_id),
                )
                # Log the decay
                repo._conn.execute(
                    """INSERT INTO node_activation_log (node_id, date, delta, new_score, reason)
                       VALUES (?, ?, ?, ?, 'daily_decay')""",
                    (node.node_id, today.isoformat(), new_score - old_score, new_score),
                )
                result["decayed"] += 1

            # Lifecycle transitions
            if node.lifecycle_state == "active" and days_since > dormant_after:
                repo.update_node_lifecycle(node.name, "dormant")
                result["dormant"] += 1
            elif node.lifecycle_state == "dormant":
                if days_since > archive_after:
                    repo.update_node_lifecycle(node.name, "archived")
                    result["archived"] += 1
                elif days_since <= dormant_after:
                    # Was dormant but got recent signal → reactivate
                    repo.update_node_lifecycle(node.name, "active")
                    result["reactivated"] += 1

    return result


# ── Internal helpers ──────────────────────────────────────────────────────

def _classify_news(
    news: list[RawNews],
    active_nodes: list[CausalNode],
    min_confidence: float,
    max_nodes: int,
) -> list[SignalImpactItem]:
    """Send news batch to LLM for node matching."""
    # Format news batch
    news_lines = []
    for i, n in enumerate(news):
        news_lines.append(f"[{i}] {n.title}")
        if n.content:
            news_lines.append(f"    {n.content[:200]}")

    # Format active nodes
    node_lines = []
    for node in active_nodes:
        keywords = node.keywords or "[]"
        node_lines.append(
            f"  - [{node.layer}] {node.name} ({node.node_type}) "
            f"keywords={keywords} activation={node.activation_score:.1f}"
        )

    template = _load_prompt_template()
    prompt = template.replace("{news_batch}", "\n".join(news_lines))
    prompt = prompt.replace("{active_nodes}", "\n".join(node_lines))

    result = call_llm_with_schema(
        prompt,
        SignalImpactOutput,
        system_prompt="你是宏观量化分析师，将新闻映射到因果图谱节点。严格按JSON输出。",
        max_retries=2,
    )

    # Filter by confidence
    return [s for s in result.articles if s.confidence >= min_confidence]


def _write_signal(repo: CausalRepo, item: SignalImpactItem, date: str) -> int:
    """Write a classified signal to news_signals. Returns signal_id."""
    # Map node names to node_ids
    node_ids = []
    for name in item.affected_nodes:
        node = repo.get_node(name)
        if node:
            node_ids.append(node.node_id)

    if not node_ids:
        return 0

    content_hash = hashlib.sha256(
        (item.title + date).encode("utf-8")
    ).hexdigest()

    cur = repo._conn.execute(
        """INSERT OR IGNORE INTO news_signals
           (date, source, title, summary, affected_node_ids,
            signal_strength, confidence, raw_content_hash)
           VALUES (?, 'llm', ?, ?, ?, ?, ?, ?)""",
        (
            date, item.title, item.summary,
            json.dumps(node_ids),
            item.signal_strength, item.confidence,
            content_hash,
        ),
    )
    return cur.lastrowid


def _update_activations(
    repo: CausalRepo,
    signals: list[SignalImpactItem],
    date: str,
    decay_rate: float,
    signal_weight_mult: float,
) -> dict[int, float]:
    """Update activation scores for all affected nodes. Returns {node_id: new_score}."""
    today = dt_date.today()
    updates: dict[int, tuple[float, float]] = {}  # node_id → (total_delta, signal_weight)

    for item in signals:
        for name in item.affected_nodes:
            node = repo.get_node(name)
            if not node:
                continue
            weight = abs(item.signal_strength) * signal_weight_mult
            if node.node_id in updates:
                prev_delta, prev_weight = updates[node.node_id]
                updates[node.node_id] = (prev_delta, prev_weight + weight)
            else:
                days_since = 0
                if node.last_signal_at:
                    try:
                        last_date = dt_date.fromisoformat(node.last_signal_at[:10])
                        days_since = (today - last_date).days
                    except (ValueError, TypeError):
                        pass

                old_score = node.activation_score or 0.0
                decayed = old_score * math.exp(-decay_rate * min(days_since, 365))
                new_score = max(0.0, decayed + weight)
                delta = new_score - old_score

                updates[node.node_id] = (delta, weight)
                repo._conn.execute(
                    "UPDATE causal_nodes SET activation_score=?, last_signal_at=?, "
                    "signal_count_30d=signal_count_30d+1, updated_at=? WHERE node_id=?",
                    (new_score, _utcnow(), _utcnow(), node.node_id),
                )
                repo._conn.execute(
                    """INSERT INTO node_activation_log (node_id, date, delta, new_score, reason)
                       VALUES (?, ?, ?, ?, 'signal_hit')""",
                    (node.node_id, date, delta, new_score),
                )

    return {nid: u[0] + u[1] for nid, u in updates.items()}


# ── Fallback prompt ───────────────────────────────────────────────────────

_FALLBACK_SIGNAL_PROMPT = """你是宏观量化分析师。将以下新闻映射到因果图谱节点。

## 新闻
{news_batch}

## 活跃节点
{active_nodes}

## 输出
JSON: {{"articles": [{{"title": "...", "affected_nodes": ["节点名"], "signal_strength": 0.5, "confidence": 0.7, "summary": "..."}}]}}

约束：每条新闻最多5个节点，confidence<0.5不入库。节点名称必须严格匹配。
"""
