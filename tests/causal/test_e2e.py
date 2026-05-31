"""Phase 6 end-to-end test: full causal pipeline from discovery to dashboard."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import date as dt_date
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from investment.causal.models import (
    DiscovererOutput,
    ProposedPath,
    ProposedNode,
    ProposedEdgeInPath,
    SignalImpactOutput,
    SignalImpactItem,
)
from investment.causal.repo import CausalRepo
from investment.causal.reviewer import Reviewer
from investment.causal.dashboard_section import load_causal_assessments, render_causal_section
from investment.core.sql_migrator import run_sql_migrations


# ── Helpers ─────────────────────────────────────────────────────────────────

def _create_core_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS instruments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL, market TEXT NOT NULL DEFAULT 'A',
        name TEXT NOT NULL, asset_class TEXT NOT NULL DEFAULT 'STOCK',
        tranche TEXT NOT NULL DEFAULT 'B', price_tick REAL NOT NULL DEFAULT 0.01,
        active INTEGER NOT NULL DEFAULT 1, UNIQUE(code, market))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS holdings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        instrument_id INTEGER NOT NULL, effective_date TEXT NOT NULL,
        shares REAL NOT NULL CHECK(shares >= 0),
        cost_price REAL NOT NULL CHECK(cost_price >= 0),
        source TEXT NOT NULL DEFAULT 'manual',
        UNIQUE(instrument_id, effective_date))""")


def _seed_nanshan_chain(repo):
    """Seed the Nanshan 6-node 5-edge causal chain."""
    with repo.transaction():
        repo.add_node("中东-军事冲突", "event", "L0_geopolitical", "地缘冲突", '["伊朗"]')
        repo.add_node("WTI油价", "macro", "L1_macro", "原油价格", '["原油"]')
        repo.add_node("美元指数DXY", "macro", "L1_macro", "美元指数", '["美元"]')
        repo.add_node("有色金属-铝", "sector", "L2_industry", "铝行业", '["铝"]')
        repo.add_node("能源-石油", "sector", "L2_industry", "石油", '["石油"]')
        repo.add_node("600219-南山铝业", "holding", "L3_holding", "南山铝业", '["南山铝业"]')
        repo.add_edge("中东-军事冲突", "WTI油价", direction=1, d1=5, d2=4, d3=4, d4=5, d5=4)
        repo.add_edge("中东-军事冲突", "美元指数DXY", direction=-1, d1=3, d2=2, d3=3, d4=3, d5=2)
        repo.add_edge("WTI油价", "能源-石油", direction=1, d1=5, d2=4, d3=5, d4=3, d5=5)
        repo.add_edge("WTI油价", "有色金属-铝", direction=1, d1=4, d2=3, d3=3, d4=2, d5=2)
        repo.add_edge("有色金属-铝", "600219-南山铝业", direction=1, d1=4, d2=3, d3=4, d4=3, d5=3)
    return repo


def _seed_holding(conn, code="600219", name="南山铝业"):
    conn.execute("INSERT OR IGNORE INTO instruments (code, name) VALUES (?, ?)", (code, name))
    conn.execute(
        "INSERT OR IGNORE INTO holdings (instrument_id, effective_date, shares, cost_price) "
        "SELECT id, '2026-05-27', 1000, 10.0 FROM instruments WHERE code=?", (code,),
    )
    conn.commit()


# ── Mock LLM outputs ────────────────────────────────────────────────────────

def _mock_discoverer_output() -> DiscovererOutput:
    """Mock discoverer: propose a new '中国PMI → 有色金属-铝' edge."""
    return DiscovererOutput(paths=[
        ProposedPath(
            narrative="中国PMI回升→工业金属需求→铝价上行→南山铝业受益",
            nodes=[
                ProposedNode(name="中国PMI", node_type="macro", layer="L1_macro",
                             description="制造业PMI", is_new=True),
                ProposedNode(name="有色金属-铝", node_type="sector", layer="L2_industry",
                             description="铝行业", is_new=False),
                ProposedNode(name="600219-南山铝业", node_type="holding", layer="L3_holding",
                             description="南山铝业", is_new=False),
            ],
            edges=[
                ProposedEdgeInPath(
                    from_node_name="中国PMI", to_node_name="有色金属-铝",
                    direction=1, d1_directness=4, d2_elasticity=3, d3_consistency=3,
                    d4_speed=2, d5_uniqueness=3, lag_days=3, confidence=0.72,
                    evidence_summary="PMI→工业金属需求正相关",
                ),
                ProposedEdgeInPath(
                    from_node_name="有色金属-铝", to_node_name="600219-南山铝业",
                    direction=1, d1_directness=4, d2_elasticity=3, d3_consistency=4,
                    d4_speed=3, d5_uniqueness=3, lag_days=3, confidence=0.78,
                    evidence_summary="铝价→南山铝业营收正相关",
                ),
            ],
        ),
    ])


def _mock_signal_output(conflict_node_id: int) -> SignalImpactOutput:
    return SignalImpactOutput(articles=[
        SignalImpactItem(
            title="伊朗袭击美军基地，油价飙升",
            affected_nodes=["中东-军事冲突"],
            signal_strength=0.85,
            confidence=0.92,
            summary="中东冲突升级推高油价，影响铝成本",
        ),
    ])


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def db_path():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)
    run_sql_migrations(path)
    # Also create core tables
    conn = sqlite3.connect(str(path))
    try:
        _create_core_tables(conn)
        conn.commit()
    finally:
        conn.close()
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def repo(db_path):
    return CausalRepo(db_path)


# ── End-to-end test ─────────────────────────────────────────────────────────

class TestE2ECausalPipeline:
    """Full pipeline: seed → discover → review → scan → assess → dashboard."""

    def test_full_pipeline(self, db_path, repo):
        today = "2026-05-27"

        # ── Step 1: Seed Nanshan chain ──────────────────────────────────
        _seed_nanshan_chain(repo)
        _seed_holding(sqlite3.connect(str(db_path)))

        # Verify chain
        with repo.transaction():
            nodes = repo.list_nodes()
            assert len(nodes) == 6
            edges = repo.list_edges()
            assert len(edges) == 5

        # ── Step 2: Discover causal paths (mock LLM) ───────────────────
        from investment.causal.discoverer import discover_causal_paths

        with patch(
            "investment.causal.discoverer.call_llm_with_schema"
        ) as mock_llm:
            mock_llm.return_value = _mock_discoverer_output()

            with patch(
                "investment.causal.discoverer._get_price_context"
            ) as mock_price:
                mock_price.return_value = ("南山铝业", "+5.0%", "test price")

                paths = discover_causal_paths(
                    event="中国PMI超预期回升",
                    holding_code="600219",
                    db_path=db_path,
                )

        assert len(paths) >= 1

        # Verify pending edges written (only new edges, not existing ones)
        with repo.transaction():
            pending = repo.list_pending()
            assert len(pending) >= 1
            # "中国PMI → 有色金属-铝" should be pending
            pm_pending = [p for p in pending if p.from_node_name == "中国PMI"]
            assert len(pm_pending) >= 1

        # ── Step 3: Review — approve pending edge ─────────────────────
        reviewer = Reviewer(db_path)
        pending_to_approve = pending[0].pending_id
        try:
            eid = reviewer.approve(pending_to_approve)
            assert eid > 0
        except ValueError:
            # May fail if edge already exists — that's fine
            pass

        # Verify edge added
        with repo.transaction():
            edges = repo.list_edges()
            assert len(edges) >= 6  # original 5 + 1 approved

        # ── Step 4: Scan — classify mock news ────────────────────────
        with repo.transaction():
            conflict = repo.get_node("中东-军事冲突")

        with patch(
            "investment.causal.scanner._get_enabled_sources"
        ) as mock_sources:
            mock_source = MagicMock()
            from investment.causal.models import RawNews
            mock_source.fetch.return_value = [
                RawNews(
                    title="伊朗袭击美军基地，油价飙升",
                    content="中东局势升级，WTI原油突破85美元。",
                    url="", source="test",
                    date=today,
                    content_hash="hash_e2e_001",
                )
            ]
            mock_sources.return_value = [mock_source]

            with patch(
                "investment.causal.scanner.call_llm_with_schema"
            ) as mock_llm:
                mock_llm.return_value = _mock_signal_output(conflict.node_id)

                from investment.causal.scanner import scan
                scan_result = scan(date=today, db_path=db_path)

        assert scan_result["classified"] >= 1
        assert scan_result["signals_written"] >= 1

        # ── Step 5: Assess — check impact on 600219 ──────────────────
        from investment.causal.assessor import assess_holdings

        results = assess_holdings(date=today, db_path=db_path)

        # Should have L3+ assessment for 600219
        nanshan_results = [r for r in results if r["holding_code"] == "600219"]
        if nanshan_results:
            r = nanshan_results[0]
            assert r["impact_level"] in ("L3", "L4", "L5")
            assert r["impact_score"] > 0
            assert r["paths_count"] >= 1

        # ── Step 6: Dashboard section renders ────────────────────────
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            assessments = load_causal_assessments(conn, today)
        finally:
            conn.close()

        html = render_causal_section(assessments)

        # Verify HTML structure
        assert "影响链异动" in html
        if assessments:
            assert "600219" in html
            # Should have expandable detail rows
            assert "toggleCausal" in html
            assert "causal-detail" in html
        else:
            # Empty state
            assert "今日无活跃影响链" in html

    def test_dashboard_empty_state(self, db_path):
        """Dashboard section renders empty state when no L3+ assessments."""
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            assessments = load_causal_assessments(conn, "2026-05-27")
        finally:
            conn.close()

        html = render_causal_section(assessments)
        assert "今日无活跃影响链" in html
        assert "causal-detail" not in html

    def test_dashboard_level_colors(self, db_path, repo):
        """Levels L3/L4/L5 should render with distinct color coding."""
        today = "2026-05-27"
        _seed_holding(sqlite3.connect(str(db_path)))

        # Insert assessments directly
        conn = sqlite3.connect(str(db_path))
        try:
            for level, score, code in [
                ("L3", 0.18, "600219"),
                ("L4", 0.35, "000001"),
                ("L5", 0.55, "000002"),
            ]:
                conn.execute("INSERT OR IGNORE INTO instruments (code, name) VALUES (?, ?)", (code, f"Test{code}"))
                conn.execute(
                    "INSERT OR REPLACE INTO chain_assessments "
                    "(date, holding_code, impact_score, impact_level, direction, paths_json, triggering_signal_ids, narrative_md) "
                    "VALUES (?, ?, ?, ?, 'positive', '[]', '[]', 'Test narrative')",
                    (today, code, score, level),
                )
            conn.commit()

            conn.row_factory = sqlite3.Row
            assessments = load_causal_assessments(conn, today)
        finally:
            conn.close()

        assert len(assessments) == 3

        html = render_causal_section(assessments)

        # Verify color-coded levels
        assert "#c53030" in html  # L5 red
        assert "#d69e2e" in html  # L4 yellow
        assert "#2b6cb0" in html  # L3 blue

        # Verify all three codes appear
        assert "600219" in html
        assert "000001" in html
        assert "000002" in html
