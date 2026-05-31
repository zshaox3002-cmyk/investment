"""Phase 3 tests: human review workflow."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from investment.causal.reviewer import Reviewer
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
def reviewer(db_path):
    return Reviewer(db_path)


@pytest.fixture
def pending_id(repo):
    """Seed a pending edge and return its ID."""
    with repo.transaction():
        # Pre-create one node so it's an "existing" node
        repo.add_node("WTI油价", "macro", "L1_macro", "", '["原油"]')
        pid = repo.add_pending_edge(
            from_node_name="中东-军事冲突",
            to_node_name="WTI油价",
            direction=1,
            from_node_proposed_type="event",
            from_node_proposed_layer="L0_geopolitical",
            d1=5, d2=4, d3=4, d4=5, d5=4,
            lag_days=1,
            confidence=0.88,
            evidence_summary="中东冲突推高油价",
            triggered_by_event="test event",
        )
    return pid


@pytest.fixture
def pending_with_new_nodes(repo):
    """Seed a pending edge where both endpoints are new nodes."""
    with repo.transaction():
        pid = repo.add_pending_edge(
            from_node_name="中国PMI",
            to_node_name="工业金属需求",
            direction=1,
            from_node_proposed_type="macro",
            from_node_proposed_layer="L1_macro",
            to_node_proposed_type="sector",
            to_node_proposed_layer="L2_industry",
            d1=4, d2=3, d3=3, d4=3, d5=2,
            confidence=0.65,
            evidence_summary="PMI回升→工业金属需求增加",
            triggered_by_event="test",
        )
    return pid


# ── Approve ─────────────────────────────────────────────────────────────────

class TestApprove:
    def test_approve_creates_nodes_and_edge(self, reviewer, repo, pending_id):
        eid = reviewer.approve(pending_id)
        assert eid > 0

        with repo.transaction():
            edge = repo.get_edge(eid)
            assert edge is not None
            assert edge.from_name == "中东-军事冲突"
            assert edge.to_name == "WTI油价"

            node = repo.get_node("中东-军事冲突")
            assert node is not None
            assert node.node_type == "event"
            assert node.layer == "L0_geopolitical"

    def test_approve_updates_pending_status(self, reviewer, repo, pending_id):
        reviewer.approve(pending_id)
        with repo.transaction():
            p = repo.get_pending(pending_id)
            assert p is not None
            assert p.status == "approved"
            assert p.decided_at is not None

    def test_approve_writes_review_log(self, reviewer, pending_id):
        reviewer.approve(pending_id)
        logs = reviewer.get_review_log(pending_id)
        assert len(logs) == 1
        assert logs[0].action == "approve"

    def test_approve_already_approved(self, reviewer, pending_id):
        reviewer.approve(pending_id)
        with pytest.raises(ValueError, match="already approved"):
            reviewer.approve(pending_id)

    def test_approve_not_found(self, reviewer):
        with pytest.raises(ValueError, match="not found"):
            reviewer.approve(99999)

    def test_approve_missing_proposed_type(self, repo, reviewer):
        """Edge with no proposed type for a new node should fail."""
        with repo.transaction():
            repo.add_node("WTI油价", "macro", "L1_macro", "", "[]")
            pid = repo.add_pending_edge(
                "NoTypeNode", "WTI油价", direction=1,
            )
        with pytest.raises(ValueError, match="missing proposed type"):
            reviewer.approve(pid)

    def test_approve_both_nodes_new(self, reviewer, repo, pending_with_new_nodes):
        eid = reviewer.approve(pending_with_new_nodes)
        assert eid > 0

        with repo.transaction():
            n1 = repo.get_node("中国PMI")
            n2 = repo.get_node("工业金属需求")
            assert n1 is not None
            assert n2 is not None
            assert repo.get_edge(eid) is not None

    def test_approve_edge_already_exists(self, reviewer, repo, pending_id):
        """Can't approve if edge already in causal_edges."""
        reviewer.approve(pending_id)  # first approval creates the edge

        # Create another pending for same pair
        with repo.transaction():
            pid2 = repo.add_pending_edge(
                "中东-军事冲突", "WTI油价", direction=1,
                confidence=0.9,
            )

        with pytest.raises(ValueError, match="already exists in causal_edges"):
            reviewer.approve(pid2)


# ── Reject ─────────────────────────────────────────────────────────────────

class TestReject:
    def test_reject_updates_status(self, reviewer, repo, pending_id):
        reviewer.reject(pending_id, reason="不成立")
        with repo.transaction():
            p = repo.get_pending(pending_id)
            assert p.status == "rejected"

    def test_reject_writes_log(self, reviewer, pending_id):
        reviewer.reject(pending_id, reason="证据不足")
        logs = reviewer.get_review_log(pending_id)
        assert len(logs) == 1
        assert logs[0].action == "reject"
        assert "证据不足" in logs[0].reason

    def test_reject_already_approved(self, reviewer, pending_id):
        reviewer.approve(pending_id)
        with pytest.raises(ValueError, match="already approved"):
            reviewer.reject(pending_id)

    def test_reject_not_found(self, reviewer):
        with pytest.raises(ValueError, match="not found"):
            reviewer.reject(99999)


# ── Modify ─────────────────────────────────────────────────────────────────

class TestModify:
    def test_modify_changes_scores(self, reviewer, repo, pending_id):
        eid = reviewer.modify(
            pending_id, d1=3, d2=2,
            reason="弹性不如预期",
        )
        assert eid > 0

        with repo.transaction():
            edge = repo.get_edge(eid)
            assert edge.d1_directness == 3
            assert edge.d2_elasticity == 2

            p = repo.get_pending(pending_id)
            assert p.status == "modified"

    def test_modify_writes_log_with_diff(self, reviewer, pending_id):
        reviewer.modify(pending_id, d1=3, reason="调整")
        logs = reviewer.get_review_log(pending_id)
        assert len(logs) >= 1
        assert logs[0].action == "modify"
        assert "d1_directness" in logs[0].modifications_json

    def test_modify_no_fields_approves_as_is(self, reviewer, repo, pending_id):
        """Modify with no field changes should approve directly."""
        eid = reviewer.modify(pending_id)
        assert eid > 0
        with repo.transaction():
            p = repo.get_pending(pending_id)
            assert p.status in ("approved", "modified")

    def test_modify_not_found(self, reviewer):
        with pytest.raises(ValueError, match="not found"):
            reviewer.modify(99999, d1=3)

    def test_modify_already_approved(self, reviewer, pending_id):
        reviewer.approve(pending_id)
        with pytest.raises(ValueError, match="already approved"):
            reviewer.modify(pending_id, d1=3)


# ── List pending ────────────────────────────────────────────────────────────

class TestListPending:
    def test_list_empty(self, reviewer):
        pending = reviewer.list_pending()
        assert len(pending) == 0

    def test_list_with_items(self, reviewer, pending_id):
        pending = reviewer.list_pending()
        assert len(pending) == 1
        assert pending[0].pending_id == pending_id

    def test_list_excludes_reviewed(self, reviewer, pending_id):
        reviewer.approve(pending_id)
        pending = reviewer.list_pending()
        assert len(pending) == 0


# ── Review log ──────────────────────────────────────────────────────────────

class TestReviewLog:
    def test_full_audit_trail(self, reviewer, repo, pending_id):
        """Approve + reject another → both logged."""
        reviewer.approve(pending_id)

        with repo.transaction():
            pid2 = repo.add_pending_edge("A", "B", direction=1, confidence=0.5)
        reviewer.reject(pid2, reason="no")

        all_logs = reviewer.get_review_log()
        assert len(all_logs) == 2

    def test_get_log_by_pending(self, reviewer, pending_id):
        reviewer.approve(pending_id)
        logs = reviewer.get_review_log(pending_id)
        assert len(logs) == 1
        assert logs[0].pending_id == pending_id


# ── End-to-end: discover → pending → review → edges ─────────────────────────

def test_full_workflow_e2e(repo, reviewer):
    """Simulate: AI proposes → human reviews → edge created."""
    with repo.transaction():
        repo.add_node("600219-南山铝业", "holding", "L3_holding", "", '["南山铝业"]')
        pid = repo.add_pending_edge(
            from_node_name="铝价上涨",
            to_node_name="600219-南山铝业",
            direction=1,
            from_node_proposed_type="sector",
            from_node_proposed_layer="L2_industry",
            d1=4, d2=3, d3=4, d4=3, d5=3,
            confidence=0.78,
            evidence_summary="铝价上涨→南山铝业利润增厚",
            triggered_by_event="沪铝期货涨3%",
        )

    pending = reviewer.list_pending()
    assert len(pending) == 1
    assert pending[0].pending_id == pid

    eid = reviewer.approve(pid)

    with repo.transaction():
        edge = repo.get_edge(eid)
        assert edge is not None
        assert edge.from_name == "铝价上涨"
        assert edge.to_name == "600219-南山铝业"

        p = repo.get_pending(pid)
        assert p.status == "approved"

    logs = reviewer.get_review_log(pid)
    assert len(logs) == 1
    assert logs[0].action == "approve"
