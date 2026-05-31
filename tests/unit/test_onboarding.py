"""Unit tests for Phase 2 onboarding module."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from investment.agent_tools.onboarding import (
    ABCAllocation,
    OnboardingResult,
    ProfileInput,
    compute_gap_analysis,
    create_goal,
    create_profile,
    generate_abc_allocation,
    get_active_goals,
    get_latest_profile,
    record_assets,
    run_onboarding,
    validate_profile_input,
)
from investment.core.db import connect
from investment.core.sql_migrator import run_sql_migrations


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """Isolated SQLite DB with schema + onboarding migration applied."""
    db_path = tmp_path / "test.db"
    from investment.core.db import init_db
    init_db(db_path)
    run_sql_migrations(db_path=db_path)
    return db_path


def _moderate_input(**overrides) -> ProfileInput:
    defaults = dict(
        investable_capital=1_000_000,
        risk_tolerance="moderate",
        horizon_years=10,
        target_annual_return=10.0,
        max_drawdown_tolerance=20.0,
    )
    defaults.update(overrides)
    return ProfileInput(**defaults)


# ── Validation ────────────────────────────────────────────────────────────────

class TestValidation:
    def test_valid_input_no_errors(self):
        assert validate_profile_input(_moderate_input()) == []

    def test_negative_capital(self):
        errs = validate_profile_input(_moderate_input(investable_capital=-1))
        assert any("金额" in e for e in errs)

    def test_invalid_risk_tolerance(self):
        errs = validate_profile_input(_moderate_input(risk_tolerance="yolo"))
        assert any("风险承受" in e for e in errs)

    def test_horizon_too_short(self):
        errs = validate_profile_input(_moderate_input(horizon_years=0))
        assert any("期限" in e for e in errs)

    def test_horizon_too_long(self):
        errs = validate_profile_input(_moderate_input(horizon_years=51))
        assert any("期限" in e for e in errs)

    def test_return_too_high(self):
        errs = validate_profile_input(_moderate_input(target_annual_return=60))
        assert any("年化" in e for e in errs)

    def test_conservative_with_high_return(self):
        errs = validate_profile_input(_moderate_input(
            risk_tolerance="conservative", target_annual_return=20
        ))
        assert any("保守型" in e for e in errs)


# ── ABC allocation ────────────────────────────────────────────────────────────

class TestABCAllocation:
    def test_moderate_default_ratios(self):
        alloc = generate_abc_allocation(_moderate_input())
        assert alloc.a_ratio == pytest.approx(0.25, abs=0.01)
        assert alloc.b_ratio == pytest.approx(0.50, abs=0.01)
        assert alloc.c_ratio == pytest.approx(0.25, abs=0.01)

    def test_ratios_sum_to_one(self):
        for risk in ("conservative", "moderate", "aggressive"):
            alloc = generate_abc_allocation(_moderate_input(risk_tolerance=risk))
            assert alloc.a_ratio + alloc.b_ratio + alloc.c_ratio == pytest.approx(1.0, abs=1e-6)

    def test_conservative_higher_a(self):
        alloc_c = generate_abc_allocation(_moderate_input(risk_tolerance="conservative"))
        alloc_m = generate_abc_allocation(_moderate_input(risk_tolerance="moderate"))
        assert alloc_c.a_ratio > alloc_m.a_ratio

    def test_aggressive_higher_c(self):
        alloc_a = generate_abc_allocation(_moderate_input(risk_tolerance="aggressive"))
        alloc_m = generate_abc_allocation(_moderate_input(risk_tolerance="moderate"))
        assert alloc_a.c_ratio > alloc_m.c_ratio

    def test_short_horizon_reduces_c(self):
        alloc_short = generate_abc_allocation(_moderate_input(horizon_years=2))
        alloc_long = generate_abc_allocation(_moderate_input(horizon_years=10))
        assert alloc_short.c_ratio < alloc_long.c_ratio

    def test_amounts_match_capital(self):
        capital = 500_000
        alloc = generate_abc_allocation(_moderate_input(investable_capital=capital))
        total = alloc.a_amount + alloc.b_amount + alloc.c_amount
        assert total == pytest.approx(capital, rel=1e-4)

    def test_rationale_not_empty(self):
        alloc = generate_abc_allocation(_moderate_input())
        assert len(alloc.rationale) > 0


# ── Gap analysis ──────────────────────────────────────────────────────────────

class TestGapAnalysis:
    def test_gap_when_target_exceeds_capital(self):
        inp = _moderate_input(target_amount=2_000_000)
        alloc = generate_abc_allocation(inp)
        gap = compute_gap_analysis(inp, alloc)
        assert "差距" in gap
        assert "¥1,000,000" in gap

    def test_achieved_when_capital_exceeds_target(self):
        inp = _moderate_input(investable_capital=3_000_000, target_amount=2_000_000)
        alloc = generate_abc_allocation(inp)
        gap = compute_gap_analysis(inp, alloc)
        assert "已达成" in gap or "已超过" in gap

    def test_projection_when_no_target(self):
        inp = _moderate_input()  # no target_amount
        alloc = generate_abc_allocation(inp)
        gap = compute_gap_analysis(inp, alloc)
        assert "预计" in gap

    def test_aggressive_target_warns(self):
        # Required rate >> target rate → warning
        inp = _moderate_input(
            investable_capital=100_000,
            target_amount=10_000_000,
            horizon_years=5,
            target_annual_return=10.0,
        )
        alloc = generate_abc_allocation(inp)
        gap = compute_gap_analysis(inp, alloc)
        assert "激进" in gap or "偏激进" in gap


# ── DB operations ─────────────────────────────────────────────────────────────

class TestDBOperations:
    def test_create_profile_returns_id(self, tmp_db):
        inp = _moderate_input()
        alloc = generate_abc_allocation(inp)
        pid = create_profile(inp, alloc, db_path=tmp_db)
        assert isinstance(pid, int) and pid > 0

    def test_create_goal_links_to_profile(self, tmp_db):
        inp = _moderate_input(target_amount=2_000_000, deadline="2036-01-01")
        alloc = generate_abc_allocation(inp)
        pid = create_profile(inp, alloc, db_path=tmp_db)
        gid = create_goal(pid, inp, db_path=tmp_db)
        assert isinstance(gid, int) and gid > 0

        goals = get_active_goals(pid, db_path=tmp_db)
        assert len(goals) == 1
        assert goals[0]["target_annual_return"] == 10.0
        assert goals[0]["deadline"] == "2036-01-01"

    def test_record_assets(self, tmp_db):
        inp = _moderate_input()
        alloc = generate_abc_allocation(inp)
        pid = create_profile(inp, alloc, db_path=tmp_db)
        assets = [
            {"asset_type": "stock", "amount": 300_000, "account": "招商证券"},
            {"asset_type": "cash", "amount": 200_000, "account": "余额宝"},
        ]
        count = record_assets(pid, assets, db_path=tmp_db)
        assert count == 2

        conn = connect(tmp_db)
        rows = conn.execute(
            "SELECT * FROM asset_inventory WHERE profile_id=?", (pid,)
        ).fetchall()
        conn.close()
        assert len(rows) == 2

    def test_get_latest_profile(self, tmp_db):
        inp = _moderate_input()
        alloc = generate_abc_allocation(inp)
        create_profile(inp, alloc, db_path=tmp_db)
        profile = get_latest_profile(db_path=tmp_db)
        assert profile is not None
        assert profile["risk_tolerance"] == "moderate"
        assert profile["investable_capital"] == 1_000_000

    def test_get_latest_profile_none_when_empty(self, tmp_db):
        assert get_latest_profile(db_path=tmp_db) is None


# ── Full onboarding flow ──────────────────────────────────────────────────────

class TestRunOnboarding:
    def test_success_full_flow(self, tmp_db):
        inp = _moderate_input(target_amount=2_000_000, deadline="2036-01-01")
        assets = [{"asset_type": "stock", "amount": 500_000, "account": "招商证券"}]
        result = run_onboarding(inp, assets=assets, db_path=tmp_db)

        assert result.success is True
        assert result.profile_id is not None
        assert result.goal_id is not None
        assert result.allocation is not None
        assert "所以你该做什么" in result.human_message
        assert "A 档" in result.human_message
        assert "B 档" in result.human_message
        assert "C 档" in result.human_message

    def test_failure_on_invalid_input(self, tmp_db):
        inp = _moderate_input(investable_capital=-1)
        result = run_onboarding(inp, db_path=tmp_db)
        assert result.success is False
        assert result.profile_id is None
        assert "金额" in result.human_message

    def test_human_message_contains_rationale(self, tmp_db):
        inp = _moderate_input()
        result = run_onboarding(inp, db_path=tmp_db)
        assert "配置逻辑" in result.human_message

    def test_conservative_profile_stored(self, tmp_db):
        inp = _moderate_input(risk_tolerance="conservative", target_annual_return=6.0)
        result = run_onboarding(inp, db_path=tmp_db)
        assert result.success is True
        profile = get_latest_profile(db_path=tmp_db)
        assert profile["risk_tolerance"] == "conservative"
        assert profile["a_ratio"] > 0.25  # conservative has more A

    def test_multiple_profiles_latest_returned(self, tmp_db):
        run_onboarding(_moderate_input(investable_capital=500_000), db_path=tmp_db)
        run_onboarding(_moderate_input(investable_capital=800_000), db_path=tmp_db)
        profile = get_latest_profile(db_path=tmp_db)
        assert profile["investable_capital"] == 800_000

    def test_no_assets_still_succeeds(self, tmp_db):
        inp = _moderate_input()
        result = run_onboarding(inp, assets=None, db_path=tmp_db)
        assert result.success is True
