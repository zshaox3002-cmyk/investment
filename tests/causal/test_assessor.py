"""Phase 5 tests: path engine, impact assessment, grading, e2e pipeline."""
from __future__ import annotations

import json
import math
import sqlite3
import tempfile
from datetime import date as dt_date
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from investment.causal.path_engine import (
    find_paths,
    compute_path_impact,
    aggregate_multi_paths,
    grade_impact,
    CausalPath,
    PathEdge,
    PathImpact,
)
from investment.causal.repo import CausalRepo
from investment.core.sql_migrator import run_sql_migrations


# ── Fixtures ────────────────────────────────────────────────────────────────

def _create_core_tables(conn):
    """Create minimal instruments + holdings tables for assessor tests."""
    conn.execute("""CREATE TABLE IF NOT EXISTS instruments (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        code          TEXT NOT NULL,
        market        TEXT NOT NULL DEFAULT 'A',
        name          TEXT NOT NULL,
        asset_class   TEXT NOT NULL DEFAULT 'STOCK',
        tranche       TEXT NOT NULL DEFAULT 'B',
        price_tick    REAL NOT NULL DEFAULT 0.01,
        active        INTEGER NOT NULL DEFAULT 1,
        UNIQUE(code, market)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS holdings (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        instrument_id   INTEGER NOT NULL,
        effective_date  TEXT NOT NULL,
        shares          REAL NOT NULL CHECK(shares >= 0),
        cost_price      REAL NOT NULL CHECK(cost_price >= 0),
        added_date      TEXT,
        reason          TEXT,
        source          TEXT NOT NULL DEFAULT 'manual',
        UNIQUE(instrument_id, effective_date),
        FOREIGN KEY (instrument_id) REFERENCES instruments(id)
    )""")


@pytest.fixture
def db_path():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)
    run_sql_migrations(path)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def db_with_holdings(db_path):
    """DB with core tables (instruments/holdings) + causal tables."""
    conn = sqlite3.connect(str(db_path))
    try:
        _create_core_tables(conn)
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture
def repo(db_path):
    return CausalRepo(db_path)


@pytest.fixture
def nanshan_chain(repo):
    """Seed the Nanshan 6-node 5-edge causal chain."""
    with repo.transaction():
        repo.add_node("中东-军事冲突", "event", "L0_geopolitical", "地缘冲突", '["伊朗"]')
        repo.add_node("WTI油价", "macro", "L1_macro", "原油价格", '["原油","WTI"]')
        repo.add_node("美元指数DXY", "macro", "L1_macro", "美元指数", '["美元"]')
        repo.add_node("有色金属-铝", "sector", "L2_industry", "铝行业", '["铝"]')
        repo.add_node("能源-石油", "sector", "L2_industry", "石油行业", '["石油"]')
        repo.add_node("600219-南山铝业", "holding", "L3_holding", "南山铝业", '["南山铝业"]')
        # Edges: 中东冲突 → WTI油价 (strong positive)
        repo.add_edge("中东-军事冲突", "WTI油价", direction=1, d1=5, d2=4, d3=4, d4=5, d5=4)
        # 中东冲突 → 美元指数 (weak negative)
        repo.add_edge("中东-军事冲突", "美元指数DXY", direction=-1, d1=3, d2=2, d3=3, d4=3, d5=2)
        # WTI油价 → 能源-石油 (strong positive)
        repo.add_edge("WTI油价", "能源-石油", direction=1, d1=5, d2=4, d3=5, d4=3, d5=5)
        # WTI油价 → 有色金属-铝 (moderate positive)
        repo.add_edge("WTI油价", "有色金属-铝", direction=1, d1=4, d2=3, d3=3, d4=2, d5=2)
        # 有色金属-铝 → 600219-南山铝业 (moderate positive)
        repo.add_edge("有色金属-铝", "600219-南山铝业", direction=1, d1=4, d2=3, d3=4, d4=3, d5=3)
    return repo


# ── Path search ─────────────────────────────────────────────────────────────

class TestFindPaths:
    def test_find_direct_path(self, nanshan_chain, repo):
        """Find a 1-hop path between directly connected nodes."""
        with repo.transaction():
            wti = repo.get_node("WTI油价")
            energy = repo.get_node("能源-石油")
            paths = find_paths(repo, wti.node_id, energy.node_id, max_hops=6)

        assert len(paths) == 1
        assert paths[0].length == 1
        assert paths[0].node_sequence == ["WTI油价", "能源-石油"]

    def test_find_2hop_path(self, nanshan_chain, repo):
        """Find a 2-hop path."""
        with repo.transaction():
            conflict = repo.get_node("中东-军事冲突")
            energy = repo.get_node("能源-石油")
            paths = find_paths(repo, conflict.node_id, energy.node_id, max_hops=6)

        # 中东-军事冲突 → WTI油价 → 能源-石油
        assert len(paths) >= 1
        assert any(
            p.node_sequence == ["中东-军事冲突", "WTI油价", "能源-石油"]
            for p in paths
        )

    def test_find_3hop_path_to_holding(self, nanshan_chain, repo):
        """Find the full chain from geopolitical event to holding."""
        with repo.transaction():
            conflict = repo.get_node("中东-军事冲突")
            nanshan = repo.get_node("600219-南山铝业")
            paths = find_paths(repo, conflict.node_id, nanshan.node_id, max_hops=6)

        # 中东-军事冲突 → WTI油价 → 有色金属-铝 → 600219-南山铝业
        holding_paths = [p for p in paths if p.node_sequence[-1] == "600219-南山铝业"]
        assert len(holding_paths) >= 1

    def test_no_path_between_unrelated_nodes(self, nanshan_chain, repo):
        """Nodes with no connecting path should return empty list."""
        with repo.transaction():
            dxy = repo.get_node("美元指数DXY")
            energy = repo.get_node("能源-石油")
            paths = find_paths(repo, dxy.node_id, energy.node_id, max_hops=6)

        # 美元指数 → 能源-石油: no edge between them
        assert len(paths) == 0

    def test_max_hops_limit(self, nanshan_chain, repo):
        """max_hops=1 should not find paths longer than 1 hop."""
        with repo.transaction():
            conflict = repo.get_node("中东-军事冲突")
            nanshan = repo.get_node("600219-南山铝业")
            paths = find_paths(repo, conflict.node_id, nanshan.node_id, max_hops=1)

        assert len(paths) == 0  # needs 3 hops

    def test_cycle_detection(self, nanshan_chain, repo):
        """Graph with a cycle should not cause infinite loops."""
        # Add a back-edge to create a cycle
        with repo.transaction():
            repo.add_edge("有色金属-铝", "WTI油价", direction=1, d1=2, d2=2, d3=2, d4=2, d5=2)
            conflict = repo.get_node("中东-军事冲突")
            nanshan = repo.get_node("600219-南山铝业")
            paths = find_paths(repo, conflict.node_id, nanshan.node_id, max_hops=6)

        # Should still find paths without infinite looping
        holding_paths = [p for p in paths if p.node_sequence[-1] == "600219-南山铝业"]
        assert len(holding_paths) >= 1
        # Each path should have unique node sequences (no cycles)
        for p in paths:
            assert len(p.node_sequence) == len(set(p.node_sequence))

    def test_dormant_node_not_traversed(self, nanshan_chain, repo):
        """Paths should not traverse through dormant nodes."""
        with repo.transaction():
            repo.update_node_lifecycle("WTI油价", "dormant")
            conflict = repo.get_node("中东-军事冲突")
            nanshan = repo.get_node("600219-南山铝业")
            paths = find_paths(repo, conflict.node_id, nanshan.node_id, max_hops=6)

        # WTI油价 is dormant, no path through it
        holding_paths = [p for p in paths if p.node_sequence[-1] == "600219-南山铝业"]
        assert len(holding_paths) == 0


# ── Impact computation ──────────────────────────────────────────────────────

class TestComputePathImpact:
    def test_single_edge_impact(self):
        """Impact through a single edge with max strength."""
        edge = PathEdge(from_name="A", to_name="B", strength=5.0, direction=1)
        path = CausalPath(edges=[edge])
        impact = compute_path_impact(path, signal_strength=0.8, alpha=0.85)

        # impact = 0.8 * (5/5) * 1 * 0.85^0 = 0.8
        assert impact == pytest.approx(0.8)

    def test_single_edge_negative_direction(self):
        """Negative direction flips the sign."""
        edge = PathEdge(from_name="A", to_name="B", strength=5.0, direction=-1)
        path = CausalPath(edges=[edge])
        impact = compute_path_impact(path, signal_strength=0.8, alpha=0.85)

        # impact = 0.8 * (5/5) * (-1) * 0.85^0 = -0.8
        assert impact == pytest.approx(-0.8)

    def test_two_hop_decay(self):
        """Two-hop path should decay by alpha."""
        e1 = PathEdge(from_name="A", to_name="B", strength=4.0, direction=1)
        e2 = PathEdge(from_name="B", to_name="C", strength=4.0, direction=1)
        path = CausalPath(edges=[e1, e2])
        impact = compute_path_impact(path, signal_strength=0.7, alpha=0.85)

        # impact = 0.7 * (4/5) * 1 * (4/5) * 1 * 0.85^1
        expected = 0.7 * 0.8 * 0.8 * 0.85
        assert impact == pytest.approx(expected)

    def test_three_hop_decay(self):
        """Three-hop path with mixed strengths."""
        e1 = PathEdge(from_name="A", to_name="B", strength=5.0, direction=1)
        e2 = PathEdge(from_name="B", to_name="C", strength=3.0, direction=1)
        e3 = PathEdge(from_name="C", to_name="D", strength=4.0, direction=1)
        path = CausalPath(edges=[e1, e2, e3])
        impact = compute_path_impact(path, signal_strength=0.6, alpha=0.85)

        # impact = 0.6 * 1.0 * 0.6 * 0.8 * 0.85^2
        expected = 0.6 * (5/5) * (3/5) * (4/5) * (0.85 ** 2)
        assert impact == pytest.approx(expected)

    def test_mixed_direction_path(self):
        """Path with both positive and negative edges."""
        e1 = PathEdge(from_name="A", to_name="B", strength=5.0, direction=1)
        e2 = PathEdge(from_name="B", to_name="C", strength=4.0, direction=-1)
        path = CausalPath(edges=[e1, e2])
        impact = compute_path_impact(path, signal_strength=0.9, alpha=0.85)

        # impact = 0.9 * 1.0 * (-0.8) * 0.85
        expected = 0.9 * (5/5) * (4/5) * (-1) * 0.85
        assert impact == pytest.approx(expected)
        assert impact < 0

    def test_empty_path(self):
        """Empty path should return zero impact."""
        path = CausalPath(edges=[])
        impact = compute_path_impact(path, signal_strength=0.5)
        assert impact == 0.0


# ── Aggregation ─────────────────────────────────────────────────────────────

class TestAggregateMultiPaths:
    def test_same_direction_adds(self):
        """Two positive paths should add."""
        p1 = PathImpact(path=CausalPath(edges=[]), impact=0.3)
        p2 = PathImpact(path=CausalPath(edges=[]), impact=0.2)
        total = aggregate_multi_paths([p1, p2])
        assert total == pytest.approx(0.5)

    def test_opposite_direction_cancels(self):
        """A positive and negative path partially cancel."""
        p1 = PathImpact(path=CausalPath(edges=[]), impact=0.4)
        p2 = PathImpact(path=CausalPath(edges=[]), impact=-0.15)
        total = aggregate_multi_paths([p1, p2])
        assert total == pytest.approx(0.25)

    def test_empty_list(self):
        """No paths → zero."""
        assert aggregate_multi_paths([]) == 0.0


# ── Grading ─────────────────────────────────────────────────────────────────

class TestGradeImpact:
    def test_l1_negligible(self):
        level, direction = grade_impact(0.02)
        assert level == "L1"
        assert direction == "positive"

    def test_l2_weak_boundary(self):
        level, _ = grade_impact(0.05)
        assert level == "L2"

    def test_l3_moderate_boundary(self):
        level, _ = grade_impact(0.15)
        assert level == "L3"

    def test_l4_significant_boundary(self):
        level, _ = grade_impact(0.30)
        assert level == "L4"

    def test_l5_severe(self):
        level, _ = grade_impact(0.55)
        assert level == "L5"

    def test_negative_direction(self):
        level, direction = grade_impact(-0.25)
        assert level == "L3"
        assert direction == "negative"

    def test_near_zero_neutral(self):
        level, direction = grade_impact(0.005)
        assert level == "L1"
        assert direction == "neutral"

    def test_exact_boundary_l2_l3(self):
        """0.15 is exactly the L3 boundary."""
        level, _ = grade_impact(0.15)
        assert level == "L3"

    def test_exact_boundary_l3_l4(self):
        """0.30 is exactly the L4 boundary."""
        level, _ = grade_impact(0.30)
        assert level == "L4"

    def test_exact_boundary_l4_l5(self):
        """0.50 is exactly the L5 boundary."""
        level, _ = grade_impact(0.50)
        assert level == "L5"


# ── End-to-end assessment ───────────────────────────────────────────────────

class TestAssessHoldings:
    def test_no_signals_returns_empty(self, db_path, nanshan_chain):
        """With no news signals, assess_holdings returns empty list."""
        from investment.causal.assessor import assess_holdings

        results = assess_holdings(date="2026-05-27", db_path=db_path)
        assert results == []

    def test_no_holding_node_skipped(self, db_with_holdings, repo):
        """Holding without a causal node should be skipped."""
        from investment.causal.assessor import assess_holdings

        # Create signals but no holding node
        with repo.transaction():
            repo.add_node("WTI油价", "macro", "L1_macro", "", '["原油"]')
            repo._conn.execute(
                """INSERT INTO news_signals
                   (date, source, title, summary, affected_node_ids,
                    signal_strength, confidence, raw_content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("2026-05-27", "llm", "test", "test",
                 json.dumps([1]), 0.8, 0.9, "hash_test"),
            )

        results = assess_holdings(date="2026-05-27", db_path=db_with_holdings)
        # No holdings in this temp DB → empty
        assert results == []

    def test_signal_to_holding_path(self, db_with_holdings, nanshan_chain):
        """Signal hitting geopolitical node → impact assessment for holding."""
        from investment.causal.assessor import assess_holdings

        # Insert a signal that hits "中东-军事冲突"
        with nanshan_chain.transaction():
            conflict = nanshan_chain.get_node("中东-军事冲突")
            nanshan_chain._conn.execute(
                """INSERT INTO news_signals
                   (date, source, title, summary, affected_node_ids,
                    signal_strength, confidence, raw_content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("2026-05-27", "llm", "伊朗冲突升级", "中东局势紧张",
                 json.dumps([conflict.node_id]), 0.8, 0.9, "hash_conflict"),
            )

        # Create holdings table + instrument for 600219
        conn = sqlite3.connect(str(db_with_holdings))
        try:
            conn.execute("INSERT OR IGNORE INTO instruments (code, name) VALUES ('600219', '南山铝业')")
            conn.execute(
                "INSERT OR IGNORE INTO holdings (instrument_id, effective_date, shares, cost_price) "
                "SELECT id, '2026-05-27', 1000, 10.0 FROM instruments WHERE code='600219'"
            )
            conn.commit()
        finally:
            conn.close()

        results = assess_holdings(date="2026-05-27", db_path=db_with_holdings)

        # Should find at least one assessment (L3+ = persisted)
        assert len(results) >= 1
        r = results[0]
        assert r["holding_code"] == "600219"
        assert r["paths_count"] >= 1
        # Impact should be positive (conflict → oil up → aluminum up → 600219)
        assert r["impact_score"] > 0

    def test_signal_below_confidence_filtered(self, db_with_holdings, nanshan_chain):
        """Signals with confidence < 0.5 should be ignored."""
        from investment.causal.assessor import assess_holdings

        with nanshan_chain.transaction():
            conflict = nanshan_chain.get_node("中东-军事冲突")
            nanshan_chain._conn.execute(
                """INSERT INTO news_signals
                   (date, source, title, summary, affected_node_ids,
                    signal_strength, confidence, raw_content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("2026-05-27", "llm", "low conf signal", "test",
                 json.dumps([conflict.node_id]), 0.9, 0.3, "hash_low"),
            )

        conn = sqlite3.connect(str(db_with_holdings))
        try:
            conn.execute("INSERT OR IGNORE INTO instruments (code, name) VALUES ('600219', '南山铝业')")
            conn.execute(
                "INSERT OR IGNORE INTO holdings (instrument_id, effective_date, shares, cost_price) "
                "SELECT id, '2026-05-27', 1000, 10.0 FROM instruments WHERE code='600219'"
            )
            conn.commit()
        finally:
            conn.close()

        results = assess_holdings(date="2026-05-27", db_path=db_with_holdings, min_confidence=0.5)
        assert results == []

    def test_assessment_persisted_to_db(self, db_with_holdings, nanshan_chain):
        """L3+ assessments should be written to chain_assessments table."""
        from investment.causal.assessor import assess_holdings

        with nanshan_chain.transaction():
            conflict = nanshan_chain.get_node("中东-军事冲突")
            nanshan_chain._conn.execute(
                """INSERT INTO news_signals
                   (date, source, title, summary, affected_node_ids,
                    signal_strength, confidence, raw_content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("2026-05-27", "llm", "strong signal", "test",
                 json.dumps([conflict.node_id]), 0.9, 0.9, "hash_strong"),
            )

        conn = sqlite3.connect(str(db_with_holdings))
        try:
            conn.execute("INSERT OR IGNORE INTO instruments (code, name) VALUES ('600219', '南山铝业')")
            conn.execute(
                "INSERT OR IGNORE INTO holdings (instrument_id, effective_date, shares, cost_price) "
                "SELECT id, '2026-05-27', 1000, 10.0 FROM instruments WHERE code='600219'"
            )
            conn.commit()
        finally:
            conn.close()

        results = assess_holdings(date="2026-05-27", db_path=db_with_holdings)

        # Verify DB persistence
        conn = sqlite3.connect(str(db_with_holdings))
        try:
            rows = conn.execute(
                "SELECT holding_code, impact_level, direction FROM chain_assessments WHERE date='2026-05-27'"
            ).fetchall()
            assert len(rows) >= 1
            assert rows[0][0] == "600219"
        finally:
            conn.close()

    def test_idempotent_rerun(self, db_with_holdings, nanshan_chain):
        """Re-running assessment on same date should replace (INSERT OR REPLACE)."""
        from investment.causal.assessor import assess_holdings

        with nanshan_chain.transaction():
            conflict = nanshan_chain.get_node("中东-军事冲突")
            nanshan_chain._conn.execute(
                """INSERT INTO news_signals
                   (date, source, title, summary, affected_node_ids,
                    signal_strength, confidence, raw_content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("2026-05-27", "llm", "test", "test",
                 json.dumps([conflict.node_id]), 0.8, 0.9, "hash_idem"),
            )

        conn = sqlite3.connect(str(db_with_holdings))
        try:
            conn.execute("INSERT OR IGNORE INTO instruments (code, name) VALUES ('600219', '南山铝业')")
            conn.execute(
                "INSERT OR IGNORE INTO holdings (instrument_id, effective_date, shares, cost_price) "
                "SELECT id, '2026-05-27', 1000, 10.0 FROM instruments WHERE code='600219'"
            )
            conn.commit()
        finally:
            conn.close()

        # Run twice
        r1 = assess_holdings(date="2026-05-27", db_path=db_with_holdings)
        r2 = assess_holdings(date="2026-05-27", db_path=db_with_holdings)

        assert len(r1) == len(r2)

        # Only one row per holding per date
        conn = sqlite3.connect(str(db_with_holdings))
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM chain_assessments WHERE date='2026-05-27' AND holding_code='600219'"
            ).fetchone()[0]
            assert count == 1
        finally:
            conn.close()


# ── PathEdge / CausalPath dataclasses ────────────────────────────────────────

class TestPathDataclasses:
    def test_node_sequence(self):
        e1 = PathEdge(from_name="A", to_name="B", strength=4.0, direction=1)
        e2 = PathEdge(from_name="B", to_name="C", strength=3.0, direction=1)
        path = CausalPath(edges=[e1, e2])
        assert path.node_sequence == ["A", "B", "C"]
        assert path.length == 2

    def test_empty_path_sequence(self):
        path = CausalPath(edges=[])
        assert path.node_sequence == []
        assert path.length == 0
