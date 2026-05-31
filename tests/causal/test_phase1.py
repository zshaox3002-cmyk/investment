"""Phase 1 tests: schema migration, models, and CRUD operations."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from investment.causal.models import CausalNode, CausalEdge, EdgeScore5D
from investment.causal.repo import CausalRepo
from investment.core.sql_migrator import run_sql_migrations


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def db_path():
    """Isolated temporary DB with causal schema applied."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)
    run_sql_migrations(path)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def raw_db_path():
    """Empty temporary DB without pre-applied migrations."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def repo(db_path):
    return CausalRepo(db_path)


# ── Migration idempotency ───────────────────────────────────────────────────

def test_migration_idempotent(raw_db_path):
    """Running migrations twice should not error."""
    result1 = run_sql_migrations(raw_db_path)
    assert result1["09_causal_schema.sql"] == "applied"

    result2 = run_sql_migrations(raw_db_path)
    assert result2["09_causal_schema.sql"] == "skipped"


def test_migration_force_reapply(raw_db_path):
    """--force should re-apply even if already applied."""
    run_sql_migrations(raw_db_path)
    result = run_sql_migrations(raw_db_path, force=True)
    assert result["09_causal_schema.sql"] == "force_reapplied"


# ── EdgeScore5D composite strength ──────────────────────────────────────────

class TestCompositeStrength:
    def test_all_max(self):
        s = EdgeScore5D(d1_directness=5, d2_elasticity=5, d3_consistency=5, d4_speed=5, d5_uniqueness=5)
        assert s.composite_strength() == pytest.approx(5.0)

    def test_all_min(self):
        s = EdgeScore5D(d1_directness=1, d2_elasticity=1, d3_consistency=1, d4_speed=1, d5_uniqueness=1)
        assert s.composite_strength() == pytest.approx(1.0)

    def test_all_mid(self):
        s = EdgeScore5D(d1_directness=3, d2_elasticity=3, d3_consistency=3, d4_speed=3, d5_uniqueness=3)
        assert s.composite_strength() == pytest.approx(3.0)

    def test_formula_weights(self):
        """Verify the weighted formula: d1*0.30 + d2*0.25 + d3*0.25 + d4*0.10 + d5*0.10."""
        s = EdgeScore5D(d1_directness=5, d2_elasticity=4, d3_consistency=4, d4_speed=5, d5_uniqueness=4)
        expected = 5 * 0.30 + 4 * 0.25 + 4 * 0.25 + 5 * 0.10 + 4 * 0.10
        assert s.composite_strength() == pytest.approx(expected)

    def test_validation_range(self):
        """Scores must be 1-5."""
        with pytest.raises(Exception):
            EdgeScore5D(d1_directness=0, d2_elasticity=3, d3_consistency=3, d4_speed=3, d5_uniqueness=3)
        with pytest.raises(Exception):
            EdgeScore5D(d1_directness=6, d2_elasticity=3, d3_consistency=3, d4_speed=3, d5_uniqueness=3)


# ── Node CRUD ───────────────────────────────────────────────────────────────

class TestNodeCRUD:
    def test_add_and_get_node(self, repo):
        with repo.transaction():
            nid = repo.add_node("测试节点", "event", "L0_geopolitical", "测试描述", '["关键词1","关键词2"]')
            assert nid > 0

            node = repo.get_node("测试节点")
            assert node is not None
            assert node.name == "测试节点"
            assert node.node_type == "event"
            assert node.layer == "L0_geopolitical"
            assert node.description == "测试描述"
            assert "关键词1" in node.keywords

    def test_get_nonexistent_node(self, repo):
        with repo.transaction():
            node = repo.get_node("不存在的节点")
            assert node is None

    def test_list_nodes_all(self, repo):
        with repo.transaction():
            repo.add_node("节点A", "event", "L0_geopolitical", "", "[]")
            repo.add_node("节点B", "macro", "L1_macro", "", "[]")
            repo.add_node("节点C", "holding", "L3_holding", "", "[]")

        with repo.transaction():
            nodes = repo.list_nodes()
            assert len(nodes) >= 3

    def test_list_nodes_filter_layer(self, repo):
        with repo.transaction():
            repo.add_node("宏观节点", "macro", "L1_macro", "", "[]")
            repo.add_node("持仓节点", "holding", "L3_holding", "", "[]")

        with repo.transaction():
            nodes = repo.list_nodes(layer="L1_macro")
            assert all(n.layer == "L1_macro" for n in nodes)
            assert len(nodes) >= 1

    def test_list_nodes_filter_state(self, repo):
        with repo.transaction():
            repo.add_node("活跃节点", "event", "L0_geopolitical", "", "[]")

        with repo.transaction():
            nodes = repo.list_nodes(state="active")
            assert all(n.lifecycle_state == "active" for n in nodes)

    def test_update_lifecycle(self, repo):
        with repo.transaction():
            repo.add_node("休眠节点", "event", "L0_geopolitical", "", "[]")
            ok = repo.update_node_lifecycle("休眠节点", "dormant")
            assert ok

            node = repo.get_node("休眠节点")
            assert node.lifecycle_state == "dormant"

    def test_update_lifecycle_nonexistent(self, repo):
        with repo.transaction():
            ok = repo.update_node_lifecycle("不存在", "archived")
            assert not ok

    def test_node_name_uniqueness(self, repo):
        with repo.transaction():
            repo.add_node("唯一节点", "event", "L0_geopolitical", "", "[]")
            with pytest.raises(Exception):
                repo.add_node("唯一节点", "macro", "L1_macro", "", "[]")


# ── Edge CRUD ───────────────────────────────────────────────────────────────

class TestEdgeCRUD:
    def _seed_nodes(self, repo):
        repo.add_node("伊朗-美国冲突强度", "event", "L0_geopolitical", "", '["伊朗","美国","制裁"]')
        repo.add_node("WTI油价", "macro", "L1_macro", "", '["原油","WTI"]')
        repo.add_node("有色金属-铝", "sector", "L2_industry", "", '["铝","有色金属"]')
        repo.add_node("600219-南山铝业", "holding", "L3_holding", "", '["南山铝业","铝业"]')

    def test_add_and_list_edge(self, repo):
        with repo.transaction():
            self._seed_nodes(repo)
            eid = repo.add_edge(
                "伊朗-美国冲突强度", "WTI油价",
                direction=1, d1=5, d2=4, d3=4, d4=5, d5=4,
                lag_days=1, evidence_summary="中东冲突推高油价",
            )
            assert eid > 0

            edges = repo.list_edges()
            assert len(edges) == 1
            e = edges[0]
            assert e.from_name == "伊朗-美国冲突强度"
            assert e.to_name == "WTI油价"
            assert e.direction == 1
            assert e.strength is not None
            assert 1.0 <= e.strength <= 5.0

    def test_add_edge_nonexistent_node(self, repo):
        with repo.transaction():
            repo.add_node("存在节点", "event", "L0_geopolitical", "", "[]")
            with pytest.raises(ValueError, match="Node not found"):
                repo.add_edge("存在节点", "不存在节点", direction=1)

    def test_edge_uniqueness(self, repo):
        with repo.transaction():
            self._seed_nodes(repo)
            repo.add_edge("伊朗-美国冲突强度", "WTI油价", direction=1)
            with pytest.raises(ValueError, match="already exists"):
                repo.add_edge("伊朗-美国冲突强度", "WTI油价", direction=-1)

    def test_list_edges_filter(self, repo):
        with repo.transaction():
            self._seed_nodes(repo)
            repo.add_edge("伊朗-美国冲突强度", "WTI油价", direction=1, d1=5, d2=4, d3=4, d4=5, d5=4)
            repo.add_edge("WTI油价", "有色金属-铝", direction=1, d1=4, d2=3, d3=3, d4=3, d5=3)

        with repo.transaction():
            edges = repo.list_edges(from_name="伊朗-美国冲突强度")
            assert len(edges) == 1
            assert edges[0].to_name == "WTI油价"

            edges = repo.list_edges(to_name="有色金属-铝")
            assert len(edges) == 1
            assert edges[0].from_name == "WTI油价"

    def test_get_edge(self, repo):
        with repo.transaction():
            self._seed_nodes(repo)
            eid = repo.add_edge(
                "伊朗-美国冲突强度", "WTI油价",
                direction=1, d1=5, d2=4, d3=4, d4=5, d5=4,
            )
            edge = repo.get_edge(eid)
            assert edge is not None
            assert edge.edge_id == eid
            assert edge.from_name == "伊朗-美国冲突强度"

    def test_get_edge_nonexistent(self, repo):
        with repo.transaction():
            edge = repo.get_edge(99999)
            assert edge is None

    def test_edge_composite_strength_computed(self, repo):
        """Edge should have computed strength on insert."""
        with repo.transaction():
            self._seed_nodes(repo)
            eid = repo.add_edge(
                "伊朗-美国冲突强度", "WTI油价",
                direction=1, d1=5, d2=4, d3=4, d4=5, d5=4,
            )
            edge = repo.get_edge(eid)
            # Expected: 5*0.30 + 4*0.25 + 4*0.25 + 5*0.10 + 4*0.10 = 1.5+1.0+1.0+0.5+0.4 = 4.4
            assert edge.strength == pytest.approx(4.4)


# ── Nanshan case: 6 nodes, 5 edges ─────────────────────────────────────────

def test_nanshan_full_chain(repo):
    """End-to-end: 南山铝业 causal chain (6 nodes, 5 edges)."""
    with repo.transaction():
        # Nodes L0 → L1 → L2 → L3
        repo.add_node("中东-军事冲突", "event", "L0_geopolitical",
                       "伊朗与美国/以色列的军事紧张局势", '["伊朗","美国","中东","军事","冲突"]')
        repo.add_node("WTI油价", "macro", "L1_macro",
                       "西德克萨斯中质原油价格", '["原油","WTI","能源","油价"]')
        repo.add_node("美元指数DXY", "macro", "L1_macro",
                       "美元指数", '["美元","DXY","汇率","美联储"]')
        repo.add_node("有色金属-铝", "sector", "L2_industry",
                       "铝行业景气度", '["铝","有色金属","电解铝","氧化铝"]')
        repo.add_node("能源-石油", "sector", "L2_industry",
                       "石油行业景气度", '["石油","能源","开采"]')
        repo.add_node("600219-南山铝业", "holding", "L3_holding",
                       "南山铝业，铝加工龙头", '["南山铝业","铝业","600219"]')

        # Edges
        e1 = repo.add_edge("中东-军事冲突", "WTI油价",
                           direction=1, d1=5, d2=4, d3=4, d4=5, d5=4, lag_days=1,
                           evidence_summary="中东地缘冲突→原油供应担忧→油价上涨")
        e2 = repo.add_edge("中东-军事冲突", "美元指数DXY",
                           direction=-1, d1=3, d2=2, d3=3, d4=3, d5=2, lag_days=3,
                           evidence_summary="地缘风险→避险情绪→美元走强（但效果不确定）")
        e3 = repo.add_edge("WTI油价", "能源-石油",
                           direction=1, d1=5, d2=4, d3=5, d4=3, d5=5, lag_days=0,
                           evidence_summary="油价→石油行业成本/利润直接正相关")
        e4 = repo.add_edge("WTI油价", "有色金属-铝",
                           direction=1, d1=4, d2=3, d3=3, d4=2, d5=2, lag_days=7,
                           evidence_summary="油价→电解铝电力成本→铝价间接传导")
        e5 = repo.add_edge("有色金属-铝", "600219-南山铝业",
                           direction=1, d1=4, d2=3, d3=4, d4=3, d5=3, lag_days=3,
                           evidence_summary="铝价→南山铝业营收直接正相关")

    # Verify
    with repo.transaction():
        nodes = repo.list_nodes()
        assert len(nodes) == 6

        edges = repo.list_edges()
        assert len(edges) == 5

        # Check individual edges
        assert repo.get_edge(e1).strength is not None
        assert repo.get_edge(e2).direction == -1
        assert repo.get_edge(e5).to_name == "600219-南山铝业"


# ── Edge model auto-computes strength ────────────────────────────────────────

def test_edge_model_computes_strength_on_construction():
    edge = CausalEdge(
        from_node_id=1, to_node_id=2, direction=1,
        d1_directness=5, d2_elasticity=4, d3_consistency=4, d4_speed=5, d5_uniqueness=4,
    )
    assert edge.strength == pytest.approx(4.4)


def test_edge_model_defaults_missing_scores():
    """Missing 5D scores should default to 3."""
    edge = CausalEdge(from_node_id=1, to_node_id=2, direction=1)
    assert edge.strength == pytest.approx(3.0)
