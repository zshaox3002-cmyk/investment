"""Phase 2 tests: AI causal discoverer."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from investment.causal.discoverer import (
    discover_causal_paths,
    discover_auto,
    _render_prompt,
    _write_path_to_pending,
    _find_node_info,
)
from investment.causal.models import (
    DiscovererOutput,
    ProposedPath,
    ProposedNode,
    ProposedEdgeInPath,
    PendingEdge,
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
def seeded_repo(repo):
    """Repo with the Nanshan 6-node 5-edge chain seeded."""
    with repo.transaction():
        repo.add_node("中东-军事冲突", "event", "L0_geopolitical", "", '["伊朗","美国"]')
        repo.add_node("WTI油价", "macro", "L1_macro", "", '["原油","WTI"]')
        repo.add_node("美元指数DXY", "macro", "L1_macro", "", '["美元","DXY"]')
        repo.add_node("有色金属-铝", "sector", "L2_industry", "", '["铝","有色金属"]')
        repo.add_node("能源-石油", "sector", "L2_industry", "", '["石油","能源"]')
        repo.add_node("600219-南山铝业", "holding", "L3_holding", "", '["南山铝业","铝业"]')
        repo.add_edge("中东-军事冲突", "WTI油价", direction=1, d1=5, d2=4, d3=4, d4=5, d5=4)
        repo.add_edge("中东-军事冲突", "美元指数DXY", direction=-1, d1=3, d2=2, d3=3, d4=3, d5=2)
        repo.add_edge("WTI油价", "能源-石油", direction=1, d1=5, d2=4, d3=5, d4=3, d5=5)
        repo.add_edge("WTI油价", "有色金属-铝", direction=1, d1=4, d2=3, d3=3, d4=2, d5=2)
        repo.add_edge("有色金属-铝", "600219-南山铝业", direction=1, d1=4, d2=3, d3=4, d4=3, d5=3)
    return repo


# ── Mock LLM response helper ────────────────────────────────────────────────

def _mock_llm_response() -> DiscovererOutput:
    """Return a realistic discoverer output for testing."""
    return DiscovererOutput(paths=[
        ProposedPath(
            narrative="中东局势升级→油价上涨→铝成本上升→南山铝业利润受挤压",
            nodes=[
                ProposedNode(name="中东-军事冲突", node_type="event", layer="L0_geopolitical",
                             description="伊朗军事冲突升级", is_new=False),
                ProposedNode(name="WTI油价", node_type="macro", layer="L1_macro",
                             description="原油价格", is_new=False),
                ProposedNode(name="有色金属-铝", node_type="sector", layer="L2_industry",
                             description="铝行业", is_new=False),
                ProposedNode(name="600219-南山铝业", node_type="holding", layer="L3_holding",
                             description="南山铝业", is_new=False),
            ],
            edges=[
                ProposedEdgeInPath(
                    from_node_name="中东-军事冲突", to_node_name="WTI油价",
                    direction=1, d1_directness=5, d2_elasticity=4, d3_consistency=4,
                    d4_speed=5, d5_uniqueness=4, lag_days=1, confidence=0.88,
                    evidence_summary="历史数据：中东冲突70%概率推高油价",
                ),
                ProposedEdgeInPath(
                    from_node_name="WTI油价", to_node_name="有色金属-铝",
                    direction=1, d1_directness=4, d2_elasticity=3, d3_consistency=3,
                    d4_speed=2, d5_uniqueness=2, lag_days=7, confidence=0.65,
                    evidence_summary="油价→电解铝电力成本→铝价间接传导",
                ),
                ProposedEdgeInPath(
                    from_node_name="有色金属-铝", to_node_name="600219-南山铝业",
                    direction=1, d1_directness=4, d2_elasticity=3, d3_consistency=4,
                    d4_speed=3, d5_uniqueness=3, lag_days=3, confidence=0.78,
                    evidence_summary="铝价→南山铝业营收正相关",
                ),
            ],
        )
    ])


def _mock_llm_response_new_path() -> DiscovererOutput:
    """Return a path with new (not yet existing) nodes and edges."""
    return DiscovererOutput(paths=[
        ProposedPath(
            narrative="中国PMI回升→铝需求增加→铝价上涨→南山铝业受益",
            nodes=[
                ProposedNode(name="中国PMI", node_type="macro", layer="L1_macro",
                             description="中国制造业采购经理指数", is_new=True),
                ProposedNode(name="有色金属-铝", node_type="sector", layer="L2_industry",
                             description="铝行业", is_new=False),
                ProposedNode(name="600219-南山铝业", node_type="holding", layer="L3_holding",
                             description="南山铝业", is_new=False),
            ],
            edges=[
                ProposedEdgeInPath(
                    from_node_name="中国PMI", to_node_name="有色金属-铝",
                    direction=1, d1_directness=3, d2_elasticity=3, d3_consistency=3,
                    d4_speed=3, d5_uniqueness=2, lag_days=14, confidence=0.55,
                    evidence_summary="PMI回升→工业金属需求预期改善",
                ),
                ProposedEdgeInPath(
                    from_node_name="有色金属-铝", to_node_name="600219-南山铝业",
                    direction=1, d1_directness=4, d2_elasticity=3, d3_consistency=4,
                    d4_speed=3, d5_uniqueness=3, lag_days=3, confidence=0.78,
                    evidence_summary="铝价→南山铝业营收正相关",
                ),
            ],
        )
    ])


# ── Prompt rendering tests ──────────────────────────────────────────────────

class TestPromptRendering:
    def test_simple_substitution(self):
        result = _render_prompt("Hello {name}", name="World")
        assert result == "Hello World"

    def test_discoverer_template(self):
        template = "事件: {event}\n代码: {code}\n价格: {price}"
        result = _render_prompt(
            template,
            event="异动测试",
            code="600219",
            price="+9%",
        )
        assert "异动测试" in result
        assert "600219" in result
        assert "+9%" in result


# ── Pending edge CRUD ───────────────────────────────────────────────────────

class TestPendingEdgeCRUD:
    def test_add_and_list_pending(self, repo):
        with repo.transaction():
            pid = repo.add_pending_edge(
                "A节点", "B节点", direction=1,
                d1=4, d2=3, d3=4, d4=3, d5=3,
                confidence=0.75, evidence_summary="test",
                triggered_by_event="test event",
            )
            assert pid > 0

            pending = repo.list_pending()
            assert len(pending) == 1
            assert pending[0].from_node_name == "A节点"
            assert pending[0].confidence == 0.75

    def test_list_pending_filtered(self, repo):
        with repo.transaction():
            repo.add_pending_edge("A", "B", direction=1, confidence=0.8)
            repo.add_pending_edge("C", "D", direction=-1, confidence=0.6)

        with repo.transaction():
            all_pending = repo.list_pending()
            assert len(all_pending) == 2

    def test_get_pending(self, repo):
        with repo.transaction():
            pid = repo.add_pending_edge("X", "Y", direction=1, confidence=0.9)
            p = repo.get_pending(pid)
            assert p is not None
            assert p.from_node_name == "X"

            p = repo.get_pending(99999)
            assert p is None

    def test_pending_edge_exists_dedup(self, repo):
        with repo.transaction():
            repo.add_pending_edge("N1", "N2", direction=1, confidence=0.7)
            assert repo.pending_edge_exists("N1", "N2") is True
            assert repo.pending_edge_exists("N1", "N3") is False


# ── Write path to pending ───────────────────────────────────────────────────

class TestWritePathToPending:
    def test_writes_all_edges(self, seeded_repo):
        """Only the new edge written; existing edge deduped by pending_edge_exists."""
        path = _mock_llm_response_new_path().paths[0]
        with seeded_repo.transaction():
            # Edge "有色金属-铝→600219-南山铝业" already in causal_edges → deduped
            count = _write_path_to_pending(seeded_repo, path, "test event")
            assert count == 1  # only the new "中国PMI→有色金属-铝" edge

            pending = seeded_repo.list_pending()
            assert len(pending) == 1
            assert pending[0].from_node_name == "中国PMI"

    def test_skips_existing_pending(self, seeded_repo):
        path = _mock_llm_response().paths[0]
        with seeded_repo.transaction():
            _write_path_to_pending(seeded_repo, path, "first run")
            count = _write_path_to_pending(seeded_repo, path, "second run")
            assert count == 0  # all already pending

    def test_proposed_node_info(self, seeded_repo):
        """New nodes should have proposed type/layer; existing nodes should not."""
        path = _mock_llm_response_new_path().paths[0]
        with seeded_repo.transaction():
            _write_path_to_pending(seeded_repo, path, "test")
            pending = seeded_repo.list_pending()
            # First edge has new source node "中国PMI"
            first_edge = [p for p in pending if p.from_node_name == "中国PMI"][0]
            assert first_edge.from_node_proposed_type == "macro"
            assert first_edge.from_node_proposed_layer == "L1_macro"


# ── Find node info helper ───────────────────────────────────────────────────

class TestFindNodeInfo:
    def test_finds_node_in_list(self):
        nodes = [
            ProposedNode(name="N1", node_type="event", layer="L0_geopolitical", is_new=True),
            ProposedNode(name="N2", node_type="macro", layer="L1_macro", is_new=False),
        ]
        t, l = _find_node_info(nodes, "N1")
        assert t == "event"
        assert l == "L0_geopolitical"

    def test_returns_none_for_missing(self):
        t, l = _find_node_info([], "N1")
        assert t is None
        assert l is None


# ── Discover with mock LLM ──────────────────────────────────────────────────

class TestDiscoverWithMockLLM:
    def test_discover_writes_to_pending(self, seeded_repo, db_path):
        """End-to-end: call discover with mock LLM, verify pending edges written."""
        mock_output = _mock_llm_response_new_path()  # edges not already in graph
        with patch(
            "investment.causal.discoverer.call_llm_with_schema"
        ) as mock_llm:
            mock_llm.return_value = mock_output

            with patch(
                "investment.causal.discoverer._get_price_context"
            ) as mock_price:
                mock_price.return_value = ("南山铝业", "+9.0%", "2026-05-26: close=10.50 chg=+9.0%")

                paths = discover_causal_paths(
                    event="南山铝业单日上涨9%",
                    holding_code="600219",
                    db_path=db_path,
                )

        assert len(paths) == 1
        assert len(paths[0].edges) == 2

        # Verify pending edges in DB (only the new "中国PMI→有色金属-铝" edge;
        # "有色金属-铝→600219-南山铝业" already exists in causal_edges → deduped)
        repo = CausalRepo(db_path)
        with repo.transaction():
            pending = repo.list_pending()
            assert len(pending) == 1
            assert pending[0].from_node_name == "中国PMI"

    def test_discover_empty_result(self, seeded_repo, db_path):
        """LLM returns no paths — should handle gracefully."""
        with patch(
            "investment.causal.discoverer.call_llm_with_schema"
        ) as mock_llm:
            mock_llm.return_value = DiscovererOutput(paths=[])

            with patch(
                "investment.causal.discoverer._get_price_context"
            ) as mock_price:
                mock_price.return_value = ("测试", "0%", "")

                paths = discover_causal_paths(
                    event="nothing happened",
                    holding_code="600219",
                    db_path=db_path,
                )

        assert len(paths) == 0


# ── LLM validation failure → retry ─────────────────────────────────────────

class TestLLMRetryLogic:
    def test_schema_failure_triggers_retry(self):
        """call_llm_with_schema should retry on validation failure."""
        from investment.core.llm import call_llm_with_schema

        from pydantic import BaseModel

        class TestSchema(BaseModel):
            name: str
            value: int

        call_count = [0]

        def mock_create(*args, **kwargs):
            call_count[0] += 1
            resp = MagicMock()
            if call_count[0] < 3:
                resp.content = [MagicMock(text='{"name": "test"}')]
            else:
                resp.content = [MagicMock(text='{"name": "test", "value": 42}')]
            return resp

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = mock_create

        with patch(
            "investment.core.llm._get_client", return_value=mock_client
        ):
            result = call_llm_with_schema("prompt", TestSchema, max_retries=3)

        assert result.name == "test"
        assert result.value == 42
        assert call_count[0] == 3  # 2 failures + 1 success


# ── Discoverer model validation ────────────────────────────────────────────

class TestDiscovererModels:
    def test_discoverer_output_valid(self):
        data = _mock_llm_response()
        assert len(data.paths) == 1
        assert len(data.paths[0].nodes) == 4
        assert data.paths[0].nodes[0].name == "中东-军事冲突"

    def test_discoverer_output_empty(self):
        data = DiscovererOutput(paths=[])
        assert len(data.paths) == 0

    def test_proposed_node_validation(self):
        with pytest.raises(Exception):
            ProposedNode(name="bad", node_type="invalid", layer="L0_geopolitical")

    def test_proposed_edge_validation(self):
        with pytest.raises(Exception):
            ProposedEdgeInPath(
                from_node_name="A", to_node_name="B",
                direction=2,  # invalid
                d1_directness=5, d2_elasticity=4, d3_consistency=4,
                d4_speed=5, d5_uniqueness=4,
                confidence=0.5,
            )

    def test_discoverer_output_json_roundtrip(self):
        """Verify the output can be serialized/deserialized for LLM."""
        data = _mock_llm_response()
        js = data.model_dump_json()
        parsed = DiscovererOutput.model_validate_json(js)
        assert len(parsed.paths) == 1
        assert parsed.paths[0].edges[0].confidence == 0.88

    def test_discoverer_output_from_llm_format(self):
        """Parse a realistic LLM JSON response."""
        llm_json = {
            "paths": [{
                "narrative": "测试传导路径",
                "nodes": [
                    {"name": "中美-贸易摩擦", "node_type": "event", "layer": "L0_geopolitical",
                     "description": "test", "is_new": True},
                ],
                "edges": [{
                    "from_node_name": "中美-贸易摩擦", "to_node_name": "人民币汇率USDCNY",
                    "direction": -1,
                    "d1_directness": 4, "d2_elasticity": 3, "d3_consistency": 3,
                    "d4_speed": 3, "d5_uniqueness": 2,
                    "lag_days": 5, "confidence": 0.60,
                    "evidence_summary": "贸易摩擦→人民币贬值预期",
                    "evidence_urls": "[]",
                }],
            }],
        }
        result = DiscovererOutput.model_validate(llm_json)
        assert len(result.paths) == 1
        assert result.paths[0].nodes[0].name == "中美-贸易摩擦"
        assert result.paths[0].edges[0].direction == -1


# ── Existing graph integration ──────────────────────────────────────────────

class TestSubgraphContext:
    def test_subgraph_for_seeded_graph(self, seeded_repo):
        with seeded_repo.transaction():
            sg = seeded_repo.get_subgraph("600219-南山铝业", hops=2)
            # 2 hops: 600219 → 有色金属-铝 → WTI油价 (3 nodes, 2 edges on that path)
            assert len(sg["nodes"]) == 3
            assert len(sg["edges"]) == 2
            node_names = {n.name for n in sg["nodes"]}
            assert "600219-南山铝业" in node_names
            assert "有色金属-铝" in node_names
            assert "WTI油价" in node_names

    def test_subgraph_empty_for_unknown_holding(self, repo):
        with repo.transaction():
            sg = repo.get_subgraph("nonexistent", hops=2)
            assert len(sg["nodes"]) == 0
            assert len(sg["edges"]) == 0
