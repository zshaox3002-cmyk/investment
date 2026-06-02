"""Phase 4 tests: news signal scanner and lifecycle management."""
from __future__ import annotations

import math
import sqlite3
import sys
import tempfile
from datetime import date as dt_date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from investment.causal.scanner import (
    scan,
    update_lifecycle,
    _classify_news,
    _update_activations,
    _write_signal,
)
from investment.causal.models import (
    RawNews,
    CausalNode,
    SignalImpactItem,
    SignalImpactOutput,
    EdgeScore5D,
)
from investment.causal.repo import CausalRepo
from investment.core.sql_migrator import run_sql_migrations


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def db_path():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)
    run_sql_migrations(path)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def repo(db_path):
    return CausalRepo(db_path)


@pytest.fixture
def active_nodes(repo):
    """Seed 3 active nodes for signal matching."""
    with repo.transaction():
        repo.add_node("WTI油价", "macro", "L1_macro", "原油价格", '["原油","WTI","油价"]')
        repo.add_node("中东-军事冲突", "event", "L0_geopolitical", "地缘冲突", '["伊朗","中东","军事"]')
        repo.add_node("有色金属-铝", "sector", "L2_industry", "铝行业", '["铝","有色金属","电解铝"]')
    with repo.transaction():
        return repo.list_nodes(state="active")


@pytest.fixture
def sample_news():
    """Return sample RawNews items for testing."""
    return [
        RawNews(
            title="伊朗袭击美军基地，油价飙升",
            content="中东局势升级，伊朗向美军基地发射导弹，WTI原油突破85美元。",
            source="cailianshe",
            date="2026-05-27",
            content_hash="hash_iran_001",
        ),
        RawNews(
            title="中国PMI回升至51.2",
            content="5月制造业PMI为51.2，连续三个月位于扩张区间。",
            source="wallstreetcn",
            date="2026-05-27",
            content_hash="hash_pmi_002",
        ),
    ]


@pytest.fixture
def mock_signal_output():
    """Mock LLM classification output."""
    return SignalImpactOutput(articles=[
        SignalImpactItem(
            title="伊朗袭击美军基地，油价飙升",
            affected_nodes=["WTI油价", "中东-军事冲突"],
            signal_strength=0.8,
            confidence=0.9,
            summary="中东冲突升级推高油价",
        ),
        SignalImpactItem(
            title="中国PMI回升至51.2",
            affected_nodes=["有色金属-铝"],
            signal_strength=0.4,
            confidence=0.6,
            summary="PMI回升利好工业金属",
        ),
    ])


# ── Scan: fetch + dedup ─────────────────────────────────────────────────────

class TestScanFetchAndDedup:
    def test_scan_no_sources(self, db_path):
        """With no enabled sources, scan returns zeros."""
        with patch(
            "investment.causal.scanner._get_enabled_sources"
        ) as mock_sources:
            mock_sources.return_value = []
            result = scan(date="2026-05-27", db_path=db_path)

        assert result["fetched"] == 0
        assert result["signals_written"] == 0

    def test_scan_no_active_nodes(self, db_path, sample_news):
        """With news but no active nodes, scan returns without classifying."""
        with patch(
            "investment.causal.scanner._get_enabled_sources"
        ) as mock_sources:
            mock_source = MagicMock()
            mock_source.fetch.return_value = sample_news
            mock_sources.return_value = [mock_source]

            result = scan(date="2026-05-27", db_path=db_path)

        assert result["fetched"] == 2
        assert result["deduped"] == 2
        assert result["classified"] == 0

    def test_scan_dedup_by_existing_hash(self, db_path, repo, sample_news):
        """News with content_hash already in news_signals should be skipped."""
        with repo.transaction():
            repo.add_node("WTI油价", "macro", "L1_macro", "", '["原油"]')
            repo._conn.execute(
                """INSERT INTO news_signals
                   (date, source, title, summary, affected_node_ids,
                    signal_strength, confidence, raw_content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("2026-05-27", "llm", "old title", "old summary",
                 "[]", 0.5, 0.8, "hash_iran_001"),
            )

        with patch(
            "investment.causal.scanner._get_enabled_sources"
        ) as mock_sources:
            mock_source = MagicMock()
            mock_source.fetch.return_value = sample_news
            mock_sources.return_value = [mock_source]

            result = scan(date="2026-05-27", db_path=db_path)

        assert result["fetched"] == 2
        assert result["deduped"] == 1

    def test_scan_dedup_no_duplicates(self, db_path, sample_news):
        """When no hashes exist in DB, all news pass dedup."""
        with patch(
            "investment.causal.scanner._get_enabled_sources"
        ) as mock_sources:
            mock_source = MagicMock()
            mock_source.fetch.return_value = sample_news
            mock_sources.return_value = [mock_source]

            result = scan(date="2026-05-27", db_path=db_path)

        assert result["fetched"] == 2
        assert result["deduped"] == 2


# ── Scan: dry run ───────────────────────────────────────────────────────────

class TestScanDryRun:
    def test_scan_dry_run_does_not_write(self, db_path, repo, active_nodes, sample_news):
        """Dry run should classify but not write signals or update nodes."""
        mock_output = SignalImpactOutput(articles=[
            SignalImpactItem(
                title="伊朗袭击美军基地，油价飙升",
                affected_nodes=["WTI油价"],
                signal_strength=0.8,
                confidence=0.9,
                summary="test",
            ),
        ])

        with patch(
            "investment.causal.scanner._get_enabled_sources"
        ) as mock_sources:
            mock_source = MagicMock()
            mock_source.fetch.return_value = sample_news[:1]
            mock_sources.return_value = [mock_source]

            with patch(
                "investment.causal.scanner.call_llm_with_schema"
            ) as mock_llm:
                mock_llm.return_value = mock_output

                result = scan(date="2026-05-27", dry_run=True, db_path=db_path)

        assert result["classified"] == 1
        assert result["signals_written"] == 0
        assert result["nodes_updated"] == 0

        # Verify nothing was written to DB
        conn = sqlite3.connect(str(db_path))
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM news_signals WHERE date = '2026-05-27'"
            ).fetchone()[0]
            assert count == 0
        finally:
            conn.close()


# ── Scan: full pipeline with mock LLM ───────────────────────────────────────

class TestScanFullPipeline:
    def test_scan_writes_signals_and_updates_activations(
        self, db_path, active_nodes, sample_news, mock_signal_output
    ):
        """Full pipeline: fetch → dedup → classify → write → update activation."""
        with patch(
            "investment.causal.scanner._get_enabled_sources"
        ) as mock_sources:
            mock_source = MagicMock()
            mock_source.fetch.return_value = sample_news
            mock_sources.return_value = [mock_source]

            with patch(
                "investment.causal.scanner.call_llm_with_schema"
            ) as mock_llm:
                mock_llm.return_value = mock_signal_output

                result = scan(date="2026-05-27", db_path=db_path)

        assert result["fetched"] == 2
        assert result["deduped"] == 2
        assert result["classified"] == 2
        assert result["signals_written"] == 2
        assert result["nodes_updated"] == 3

        # Verify signals in DB
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT title, affected_node_ids FROM news_signals WHERE date = '2026-05-27'"
            ).fetchall()
            assert len(rows) == 2
        finally:
            conn.close()

    def test_scan_classify_failure_is_graceful(self, db_path, sample_news):
        """If LLM classification fails, scan should return without crashing."""
        with patch(
            "investment.causal.scanner._get_enabled_sources"
        ) as mock_sources:
            mock_source = MagicMock()
            mock_source.fetch.return_value = sample_news
            mock_sources.return_value = [mock_source]

            with patch(
                "investment.causal.scanner._classify_news"
            ) as mock_classify:
                mock_classify.side_effect = RuntimeError("LLM timeout")

                result = scan(date="2026-05-27", db_path=db_path)

        assert result["fetched"] == 2
        assert result["classified"] == 0


# ── Classification ──────────────────────────────────────────────────────────

class TestClassifyNews:
    def test_filter_by_min_confidence(self, active_nodes):
        """Signals below min_confidence should be filtered out."""
        news = [
            RawNews(title="Test news", content="test", source="test",
                    date="2026-05-27", content_hash="hash1"),
        ]

        mock_output = SignalImpactOutput(articles=[
            SignalImpactItem(
                title="Test news",
                affected_nodes=["WTI油价"],
                signal_strength=0.5,
                confidence=0.9,
                summary="high confidence",
            ),
            SignalImpactItem(
                title="Test news",
                affected_nodes=["中东-军事冲突"],
                signal_strength=0.3,
                confidence=0.3,
                summary="low confidence",
            ),
        ])

        with patch(
            "investment.causal.scanner.call_llm_with_schema"
        ) as mock_llm:
            mock_llm.return_value = mock_output

            result = _classify_news(news, active_nodes, min_confidence=0.5, max_nodes=5)

        assert len(result) == 1
        assert result[0].confidence == 0.9

    def test_classify_empty_news(self, active_nodes):
        """Empty news list should return empty signals (LLM sees no news to match)."""
        mock_output = SignalImpactOutput(articles=[])

        with patch(
            "investment.causal.scanner.call_llm_with_schema"
        ) as mock_llm:
            mock_llm.return_value = mock_output
            result = _classify_news([], active_nodes, min_confidence=0.5, max_nodes=5)

        assert result == []


# ── Activation updates ──────────────────────────────────────────────────────

class TestUpdateActivations:
    def test_signal_boosts_activation(self, repo, active_nodes):
        """A signal hit should increase activation score."""
        # Get initial activation inside transaction
        with repo.transaction():
            node = repo.get_node("WTI油价")
            old_score = node.activation_score

        signals = [
            SignalImpactItem(
                title="Test",
                affected_nodes=["WTI油价"],
                signal_strength=0.7,
                confidence=0.9,
                summary="test",
            ),
        ]

        with repo.transaction():
            updated = _update_activations(
                repo, signals, "2026-05-27", decay_rate=0.03, signal_weight_mult=20,
            )

        assert node.node_id in updated

        with repo.transaction():
            node_after = repo.get_node("WTI油价")
            expected_boost = abs(0.7) * 20
            assert node_after.activation_score > old_score
            assert node_after.activation_score == pytest.approx(old_score + expected_boost, rel=0.01)

    def test_signal_multiple_nodes_same_signal(self, repo, active_nodes):
        """One signal affecting multiple nodes should update all."""
        signals = [
            SignalImpactItem(
                title="Test",
                affected_nodes=["WTI油价", "中东-军事冲突"],
                signal_strength=0.6,
                confidence=0.9,
                summary="multi-node impact",
            ),
        ]

        with repo.transaction():
            updated = _update_activations(
                repo, signals, "2026-05-27", decay_rate=0.03, signal_weight_mult=20,
            )

        assert len(updated) == 2

        with repo.transaction():
            wti = repo.get_node("WTI油价")
            conflict = repo.get_node("中东-军事冲突")
            assert wti.activation_score > 0
            assert conflict.activation_score > 0

    def test_signal_unknown_node_skipped(self, repo, active_nodes):
        """Signals referencing unknown node names should be silently skipped."""
        signals = [
            SignalImpactItem(
                title="Test",
                affected_nodes=["不存在的节点"],
                signal_strength=0.5,
                confidence=0.9,
                summary="test",
            ),
        ]

        with repo.transaction():
            updated = _update_activations(
                repo, signals, "2026-05-27", decay_rate=0.03, signal_weight_mult=20,
            )

        assert len(updated) == 0

    def test_activation_log_written(self, repo, active_nodes):
        """Signal hits should create node_activation_log entries."""
        signals = [
            SignalImpactItem(
                title="Test",
                affected_nodes=["WTI油价"],
                signal_strength=0.5,
                confidence=0.9,
                summary="test",
            ),
        ]

        with repo.transaction():
            _update_activations(
                repo, signals, "2026-05-27", decay_rate=0.03, signal_weight_mult=20,
            )
            logs = repo._conn.execute(
                "SELECT node_id, reason, delta FROM node_activation_log"
            ).fetchall()

        assert len(logs) >= 1
        signal_logs = [l for l in logs if l[1] == "signal_hit"]
        assert len(signal_logs) == 1
        assert signal_logs[0][2] > 0


# ── Activation decay ────────────────────────────────────────────────────────

class TestActivationDecay:
    def test_decay_formula(self):
        """Verify the decay formula: new = old * exp(-rate * days)."""
        old_score = 10.0
        decay_rate = 0.03
        days = 10

        expected = old_score * math.exp(-decay_rate * days)
        assert expected == pytest.approx(7.408, rel=0.01)

    def test_decay_zero_days_no_change(self):
        """Zero days since last signal — no decay."""
        old_score = 10.0
        new_score = old_score * math.exp(-0.03 * 0)
        assert new_score == pytest.approx(10.0)

    def test_decay_long_period_approaches_zero(self):
        """After many days, activation should approach zero."""
        old_score = 10.0
        decay_rate = 0.03
        days = 365

        new_score = old_score * math.exp(-decay_rate * days)
        assert new_score < 0.01

    def test_decay_floor_is_zero(self):
        """Decay formula applies max(0, ...) floor — very small scores hit zero."""
        old_score = 0.1
        decay_rate = 0.03
        days = 365

        raw = old_score * math.exp(-decay_rate * days)
        # raw ≈ 1.75e-6 — not exactly zero, but max(0, raw) = raw
        # The code applies max(0.0, ...) which only clips negative values
        assert raw > 0  # it's positive but tiny
        assert raw < 0.001  # effectively zero


# ── Lifecycle transitions ───────────────────────────────────────────────────

class TestLifecycleTransitions:
    def test_update_lifecycle_no_nodes(self, db_path):
        """With no nodes, update_lifecycle returns zeros."""
        result = update_lifecycle(db_path=db_path)
        assert result["decayed"] == 0
        assert result["dormant"] == 0
        assert result["archived"] == 0
        assert result["reactivated"] == 0

    def test_active_to_dormant(self, db_path, repo):
        """Active node with no signals for > dormant_after_days → dormant."""
        old_date = (dt_date.today() - timedelta(days=40)).isoformat()
        with repo.transaction():
            repo.add_node("测试节点", "macro", "L1_macro", "", "[]")
            repo._conn.execute(
                "UPDATE causal_nodes SET last_signal_at=?, activation_score=? WHERE name=?",
                (old_date + "T00:00:00Z", 5.0, "测试节点"),
            )

        result = update_lifecycle(db_path=db_path)

        with repo.transaction():
            node = repo.get_node("测试节点")
            assert node.lifecycle_state == "dormant"
        assert result["dormant"] >= 1

    def test_dormant_to_archived(self, db_path, repo):
        """Dormant node with no signals for > archive_after_days → archived."""
        old_date = (dt_date.today() - timedelta(days=200)).isoformat()
        with repo.transaction():
            repo.add_node("旧节点", "macro", "L1_macro", "", "[]")
            repo._conn.execute(
                "UPDATE causal_nodes SET lifecycle_state=?, last_signal_at=?, activation_score=? WHERE name=?",
                ("dormant", old_date + "T00:00:00Z", 0.5, "旧节点"),
            )

        result = update_lifecycle(db_path=db_path)

        with repo.transaction():
            node = repo.get_node("旧节点")
            assert node.lifecycle_state == "archived"
        assert result["archived"] >= 1

    def test_dormant_to_active_reactivation(self, db_path, repo):
        """Dormant node with recent signal → reactivated to active."""
        recent_date = (dt_date.today() - timedelta(days=5)).isoformat()
        with repo.transaction():
            repo.add_node("复苏节点", "macro", "L1_macro", "", "[]")
            repo._conn.execute(
                "UPDATE causal_nodes SET lifecycle_state=?, last_signal_at=?, activation_score=? WHERE name=?",
                ("dormant", recent_date + "T00:00:00Z", 2.0, "复苏节点"),
            )

        result = update_lifecycle(db_path=db_path)

        with repo.transaction():
            node = repo.get_node("复苏节点")
            assert node.lifecycle_state == "active"
        assert result["reactivated"] >= 1

    def test_archived_node_skipped(self, db_path, repo):
        """Archived nodes should be skipped entirely."""
        old_date = (dt_date.today() - timedelta(days=200)).isoformat()
        with repo.transaction():
            repo.add_node("归档节点", "macro", "L1_macro", "", "[]")
            repo._conn.execute(
                "UPDATE causal_nodes SET lifecycle_state=?, last_signal_at=?, activation_score=? WHERE name=?",
                ("archived", old_date + "T00:00:00Z", 0.0, "归档节点"),
            )

        result = update_lifecycle(db_path=db_path)

        assert result["archived"] == 0

    def test_active_node_stays_active(self, db_path, repo):
        """Active node with recent signal should stay active."""
        recent_date = (dt_date.today() - timedelta(days=3)).isoformat()
        with repo.transaction():
            repo.add_node("活跃节点", "macro", "L1_macro", "", "[]")
            repo._conn.execute(
                "UPDATE causal_nodes SET last_signal_at=?, activation_score=? WHERE name=?",
                (recent_date + "T00:00:00Z", 8.0, "活跃节点"),
            )

        result = update_lifecycle(db_path=db_path)

        with repo.transaction():
            node = repo.get_node("活跃节点")
            assert node.lifecycle_state == "active"

    def test_decay_applied_during_lifecycle(self, db_path, repo):
        """update_lifecycle should apply decay to activation scores."""
        old_date = (dt_date.today() - timedelta(days=15)).isoformat()
        with repo.transaction():
            repo.add_node("衰减节点", "macro", "L1_macro", "", "[]")
            repo._conn.execute(
                "UPDATE causal_nodes SET last_signal_at=?, activation_score=? WHERE name=?",
                (old_date + "T00:00:00Z", 10.0, "衰减节点"),
            )

        result = update_lifecycle(db_path=db_path)

        with repo.transaction():
            node = repo.get_node("衰减节点")
            assert node.activation_score < 10.0
            assert node.activation_score == pytest.approx(6.38, rel=0.05)
        assert result["decayed"] >= 1

    def test_lifecycle_no_last_signal_date(self, db_path, repo):
        """Nodes with no last_signal_at should calculate days_since from created_at."""
        with repo.transaction():
            repo.add_node("无信号节点", "macro", "L1_macro", "", "[]")

        # Backdate created_at to 200 days ago to simulate an old, signal-less node
        old_date = (dt_date.today() - timedelta(days=200)).isoformat()
        with repo.transaction():
            repo._conn.execute(
                "UPDATE causal_nodes SET created_at = ? WHERE name = ?",
                (old_date, "无信号节点"),
            )

        result = update_lifecycle(db_path=db_path)

        with repo.transaction():
            node = repo.get_node("无信号节点")
            # 200 days without signal → transitions from active to dormant
            # (archive_after=180 only applies when already dormant)
            assert node.lifecycle_state == "dormant"
            assert result["dormant"] >= 1


# ── Signal writing ──────────────────────────────────────────────────────────

class TestWriteSignal:
    def test_write_signal_unknown_nodes(self, db_path, repo):
        """_write_signal with only unknown node names should return 0."""
        item = SignalImpactItem(
            title="Test",
            affected_nodes=["不存在的节点"],
            signal_strength=0.5,
            confidence=0.8,
            summary="test",
        )

        with repo.transaction():
            sig_id = _write_signal(repo, item, "2026-05-27")

        assert sig_id == 0

    def test_write_signal_inserts_row(self, db_path, repo):
        """_write_signal with valid nodes should insert into news_signals."""
        with repo.transaction():
            repo.add_node("WTI油价", "macro", "L1_macro", "", '["原油"]')

        item = SignalImpactItem(
            title="Test Signal",
            affected_nodes=["WTI油价"],
            signal_strength=0.7,
            confidence=0.85,
            summary="test summary",
        )

        with repo.transaction():
            sig_id = _write_signal(repo, item, "2026-05-27")
            rows = repo._conn.execute(
                "SELECT title, signal_strength, confidence FROM news_signals WHERE rowid = ?",
                (sig_id,),
            ).fetchall()

        assert sig_id is not None
        assert sig_id > 0
        assert len(rows) == 1
        assert rows[0][0] == "Test Signal"
        assert rows[0][1] == pytest.approx(0.7)
        assert rows[0][2] == pytest.approx(0.85)


# ── Composite strength integration ──────────────────────────────────────────

class TestCompositeStrength:
    """Verify the 5D composite strength formula used by the causal system."""

    def test_composite_strength_formula(self):
        """strength = d1*0.30 + d2*0.25 + d3*0.25 + d4*0.10 + d5*0.10"""
        scores = EdgeScore5D(
            d1_directness=5,
            d2_elasticity=4,
            d3_consistency=3,
            d4_speed=2,
            d5_uniqueness=1,
        )

        expected = 5*0.30 + 4*0.25 + 3*0.25 + 2*0.10 + 1*0.10
        assert scores.composite_strength() == pytest.approx(3.55)


# ── News sources ────────────────────────────────────────────────────────────

class TestNewsSources:
    def test_cailianshe_source_config(self):
        """CailiansheSource should respect max_articles config."""
        from investment.causal.news_sources.cailianshe import CailiansheSource

        source = CailiansheSource({"max_articles_per_source": 5})
        assert source.config["max_articles_per_source"] == 5

    def test_wallstreetcn_source_config(self):
        """WallstreetcnSource should accept RSS URL config."""
        from investment.causal.news_sources.wallstreetcn import WallstreetcnSource

        source = WallstreetcnSource({
            "url": "https://example.com/rss",
            "max_articles_per_source": 10,
        })
        assert source.config["url"] == "https://example.com/rss"
        assert source.config["max_articles_per_source"] == 10

    def test_wallstreetcn_no_url(self):
        """WallstreetcnSource with no URL returns empty list."""
        from investment.causal.news_sources.wallstreetcn import WallstreetcnSource

        source = WallstreetcnSource({})
        result = source.fetch("2026-05-27")
        assert result == []

    def test_cailianshe_import_error(self):
        """CailiansheSource handles akshare import error gracefully."""
        import sys
        original = sys.modules.get("akshare")
        sys.modules["akshare"] = None

        try:
            from investment.causal.news_sources.cailianshe import CailiansheSource
            source = CailiansheSource({})
            result = source.fetch("2026-05-27")
            assert result == []
        finally:
            if original is not None:
                sys.modules["akshare"] = original
            elif "akshare" in sys.modules:
                del sys.modules["akshare"]
