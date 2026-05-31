"""Unit tests for Phase 6 causal facade."""
from __future__ import annotations

import json

import pytest

from investment.agent_tools.causal_facade import (
    CausalInsight,
    CausalInsightReport,
    _build_action,
    _build_human_message,
    _enrich_assessment,
    backfill_credibility_tiers,
    run_causal_insight,
    update_validation_status,
)
from investment.core.db import init_db
from investment.core.sql_migrator import run_sql_migrations
from investment.migration._09_causal_ext import run as run_causal_ext


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    run_sql_migrations(db_path=db_path)
    run_causal_ext(db_path=db_path)
    return db_path


def _seed_assessment(db_path, **kwargs) -> int:
    """Insert a chain_assessments row, return assessment_id."""
    from investment.core.db import connect
    defaults = dict(
        date="2026-05-28",
        holding_code="600219",
        impact_score=0.9,
        impact_level="L4",
        direction="negative",
        paths_json="[]",
        triggering_signal_ids="[]",
        narrative_md="测试叙述",
        validation_status="open",
        revision_log="[]",
        scope_layer="L3_holding",
        credibility_tier="A",
    )
    defaults.update(kwargs)
    conn = connect(db_path)
    cur = conn.execute(
        """INSERT INTO chain_assessments
           (date, holding_code, impact_score, impact_level, direction,
            paths_json, triggering_signal_ids, narrative_md,
            validation_status, revision_log, scope_layer, credibility_tier)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (defaults["date"], defaults["holding_code"], defaults["impact_score"],
         defaults["impact_level"], defaults["direction"],
         defaults["paths_json"], defaults["triggering_signal_ids"],
         defaults["narrative_md"], defaults["validation_status"],
         defaults["revision_log"], defaults["scope_layer"],
         defaults["credibility_tier"]),
    )
    conn.commit()
    aid = cur.lastrowid
    conn.close()
    return aid


# ── _enrich_assessment ────────────────────────────────────────────────────────

class TestEnrichAssessment:
    def _make_row(self, **kwargs) -> dict:
        defaults = dict(
            assessment_id=1, holding_code="600219", holding_name="南山铝业",
            date="2026-05-28", impact_level="L4", direction="negative",
            scope_layer="L3_holding", credibility_tier="A",
            validation_status="open", narrative_md="",
            revision_log="[]",
        )
        defaults.update(kwargs)
        return defaults

    def test_direction_label_negative(self):
        ins = _enrich_assessment(self._make_row(direction="negative"))
        assert ins.direction_label == "利空"

    def test_direction_label_positive(self):
        ins = _enrich_assessment(self._make_row(direction="positive"))
        assert ins.direction_label == "利好"

    def test_credibility_label_a(self):
        ins = _enrich_assessment(self._make_row(credibility_tier="A"))
        assert "高可信" in ins.credibility_label

    def test_scope_label_l3(self):
        ins = _enrich_assessment(self._make_row(scope_layer="L3_holding"))
        assert "持仓层" in ins.scope_label

    def test_validation_label_open(self):
        ins = _enrich_assessment(self._make_row(validation_status="open"))
        assert "待验证" in ins.validation_label

    def test_action_required_not_empty(self):
        ins = _enrich_assessment(self._make_row())
        assert len(ins.action_required) > 0


# ── _build_action ─────────────────────────────────────────────────────────────

class TestBuildAction:
    def test_a_negative_suggests_review(self):
        action = _build_action("A", "negative", "600219")
        assert "减仓" in action or "审查" in action

    def test_a_positive_suggests_opportunity(self):
        action = _build_action("A", "positive", "600219")
        assert "加仓" in action or "机会" in action

    def test_b_suggests_monitor(self):
        action = _build_action("B", "negative", "600219")
        assert "关注" in action or "证据" in action

    def test_c_no_action(self):
        action = _build_action("C", "neutral", "600219")
        assert "暂不" in action or "记录" in action


# ── update_validation_status ──────────────────────────────────────────────────

class TestUpdateValidationStatus:
    def test_update_to_confirmed(self, tmp_db):
        aid = _seed_assessment(tmp_db)
        ok = update_validation_status(aid, "confirmed", "后续数据证实", db_path=tmp_db)
        assert ok is True

        from investment.core.db import connect
        conn = connect(tmp_db)
        row = conn.execute(
            "SELECT validation_status, revision_log FROM chain_assessments WHERE assessment_id=?",
            (aid,),
        ).fetchone()
        conn.close()
        assert row["validation_status"] == "confirmed"
        log = json.loads(row["revision_log"])
        assert len(log) == 1
        assert log[0]["new_status"] == "confirmed"
        assert log[0]["reason"] == "后续数据证实"

    def test_update_to_refuted(self, tmp_db):
        aid = _seed_assessment(tmp_db)
        ok = update_validation_status(aid, "refuted", "事件未发生", db_path=tmp_db)
        assert ok is True

        from investment.core.db import connect
        conn = connect(tmp_db)
        row = conn.execute(
            "SELECT validation_status FROM chain_assessments WHERE assessment_id=?",
            (aid,),
        ).fetchone()
        conn.close()
        assert row["validation_status"] == "refuted"

    def test_revision_log_accumulates(self, tmp_db):
        aid = _seed_assessment(tmp_db)
        update_validation_status(aid, "confirmed", "第一次确认", db_path=tmp_db)
        update_validation_status(aid, "refuted", "后来否定", db_path=tmp_db)

        from investment.core.db import connect
        conn = connect(tmp_db)
        row = conn.execute(
            "SELECT revision_log FROM chain_assessments WHERE assessment_id=?",
            (aid,),
        ).fetchone()
        conn.close()
        log = json.loads(row["revision_log"])
        assert len(log) == 2
        assert log[0]["new_status"] == "confirmed"
        assert log[1]["new_status"] == "refuted"

    def test_invalid_status_returns_false(self, tmp_db):
        aid = _seed_assessment(tmp_db)
        ok = update_validation_status(aid, "invalid_status", db_path=tmp_db)
        assert ok is False

    def test_nonexistent_id_returns_false(self, tmp_db):
        ok = update_validation_status(99999, "confirmed", db_path=tmp_db)
        assert ok is False


# ── _build_human_message ──────────────────────────────────────────────────────

class TestBuildHumanMessage:
    def _make_insight(self, credibility="A", direction="negative") -> CausalInsight:
        return CausalInsight(
            assessment_id=1, holding_code="600219", holding_name="南山铝业",
            date="2026-05-28", impact_level="L4", direction=direction,
            scope_layer="L3_holding", credibility_tier=credibility,
            validation_status="open", narrative="测试叙述",
            direction_label="利空" if direction == "negative" else "利好",
            credibility_label="高可信（建议行动）",
            scope_label="持仓层（直接影响你持有的股票）",
            validation_label="待验证",
            action_required="建议审查持仓逻辑",
        )

    def test_actionable_shown_prominently(self):
        report = CausalInsightReport(
            as_of="2026-05-28",
            actionable=[self._make_insight()],
            monitoring=[],
            anomalies_detected=[],
            human_message="",
        )
        msg = _build_human_message(report)
        assert "需要关注" in msg
        assert "所以你该做什么" in msg

    def test_no_signals_shows_ok(self):
        report = CausalInsightReport(
            as_of="2026-05-28",
            actionable=[], monitoring=[],
            anomalies_detected=[], human_message="",
        )
        msg = _build_human_message(report)
        assert "无高可信信号" in msg

    def test_anomalies_shown(self):
        report = CausalInsightReport(
            as_of="2026-05-28",
            actionable=[], monitoring=[],
            anomalies_detected=["600219", "002594"],
            human_message="",
        )
        msg = _build_human_message(report)
        assert "异动" in msg
        assert "600219" in msg

    def test_confidence_update_instructions_shown(self):
        report = CausalInsightReport(
            as_of="2026-05-28",
            actionable=[self._make_insight()],
            monitoring=[], anomalies_detected=[],
            human_message="",
        )
        msg = _build_human_message(report)
        assert "validate" in msg or "置信度" in msg

    def test_no_technical_codes_in_output(self):
        report = CausalInsightReport(
            as_of="2026-05-28",
            actionable=[self._make_insight()],
            monitoring=[], anomalies_detected=[],
            human_message="",
        )
        msg = _build_human_message(report)
        forbidden = ["assessment_id=", "credibility_tier=", "scope_layer="]
        for f in forbidden:
            assert f not in msg


# ── backfill_credibility_tiers ────────────────────────────────────────────────

class TestBackfillCredibilityTiers:
    def test_backfill_updates_default_c(self, tmp_db):
        # Seed an assessment with L5 impact but default 'C' credibility
        aid = _seed_assessment(tmp_db, impact_level="L5", credibility_tier="C")
        n = backfill_credibility_tiers(db_path=tmp_db)
        assert n >= 1

        from investment.core.db import connect
        conn = connect(tmp_db)
        row = conn.execute(
            "SELECT credibility_tier FROM chain_assessments WHERE assessment_id=?",
            (aid,),
        ).fetchone()
        conn.close()
        assert row["credibility_tier"] == "A"

    def test_backfill_idempotent(self, tmp_db):
        _seed_assessment(tmp_db, impact_level="L4", credibility_tier="C")
        n1 = backfill_credibility_tiers(db_path=tmp_db)
        n2 = backfill_credibility_tiers(db_path=tmp_db)
        # Second run should update 0 (already set to non-C)
        assert n2 == 0


# ── Integration: run_causal_insight ──────────────────────────────────────────

class TestRunCausalInsight:
    def test_runs_without_error(self, tmp_db):
        report = run_causal_insight(as_of="2026-05-28", db_path=tmp_db)
        assert isinstance(report, CausalInsightReport)

    def test_with_seeded_data(self, tmp_db):
        _seed_assessment(tmp_db, credibility_tier="A", direction="negative")
        report = run_causal_insight(as_of="2026-05-28", db_path=tmp_db)
        assert len(report.actionable) >= 1
        assert "所以你该做什么" in report.human_message

    def test_real_db_smoke(self):
        """Smoke test against real DB."""
        report = run_causal_insight(as_of="2026-05-28")
        assert report is not None
        assert len(report.human_message) > 50

    def test_zero_user_operations_needed(self, tmp_db):
        """User should get insights without any graph operations."""
        _seed_assessment(tmp_db, credibility_tier="B", direction="positive")
        report = run_causal_insight(as_of="2026-05-28", db_path=tmp_db)
        # Report should be complete without user doing anything
        assert report.human_message != ""
        assert report.as_of == "2026-05-28"

    def test_confidence_updatable_after_report(self, tmp_db):
        """Confidence can be updated after receiving the report."""
        aid = _seed_assessment(tmp_db, credibility_tier="A")
        report = run_causal_insight(as_of="2026-05-28", db_path=tmp_db)
        assert len(report.actionable) >= 1

        # User confirms the signal
        ok = update_validation_status(aid, "confirmed", "事件已发生", db_path=tmp_db)
        assert ok is True

        # Re-run: confirmed signal should still appear
        report2 = run_causal_insight(as_of="2026-05-28", db_path=tmp_db)
        confirmed = [i for i in report2.actionable if i.validation_status == "confirmed"]
        assert len(confirmed) >= 1
