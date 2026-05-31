"""Unit tests for agent_tools Phase 0 wrappers.

Tests use subprocess mocking to avoid real CLI calls.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from investment.agent_tools.base import ToolResult
from investment.agent_tools import data_tables, migrate_run, migrate_verify
from investment.agent_tools.snapshot import snapshot_pull, snapshot_show
from investment.agent_tools.dashboard import dashboard_render
from investment.agent_tools.trade import (
    trade_decision, trade_list, trade_log, trade_apply, trade_stop, exec_monitor,
)
from investment.agent_tools.thesis import thesis_sync, thesis_list, thesis_score, thesis_stale
from investment.agent_tools.candidate import candidate_scan, candidate_list
from investment.agent_tools.review import review_log, review_stats
from investment.agent_tools.causal import (
    causal_daily, causal_scan, causal_assess,
    causal_review_list, causal_review_approve, causal_review_reject,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_run(module: str, success: bool, output: str):
    """Return a patch context for run_inv in the given agent_tools module."""
    return patch(
        f"investment.agent_tools.{module}.run_inv",
        return_value=(success, output),
    )


# ── ToolResult ────────────────────────────────────────────────────────────────

class TestToolResult:
    def test_bool_true(self):
        assert bool(ToolResult(success=True)) is True

    def test_bool_false(self):
        assert bool(ToolResult(success=False)) is False

    def test_defaults(self):
        r = ToolResult(success=True)
        assert r.data == {}
        assert r.human_message == ""
        assert r.raw_output == ""


# ── data_tables ───────────────────────────────────────────────────────────────

class TestDataTables:
    _TABLE_OUTPUT = (
        "DB objects\n"
        "┏━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        "┃ name                  ┃\n"
        "┡━━━━━━━━━━━━━━━━━━━━━━━┩\n"
        "│ alerts                │\n"
        "│ holdings              │\n"
        "│ instruments           │\n"
        "│ v_portfolio_snapshot  │\n"
        "└───────────────────────┘\n"
    )

    def test_success_parses_tables(self):
        with _mock_run("data", True, self._TABLE_OUTPUT):
            result = data_tables()
        assert result.success is True
        assert "alerts" in result.data["tables"]
        assert "holdings" in result.data["tables"]
        assert result.data["count"] == 4

    def test_success_human_message_contains_action(self):
        with _mock_run("data", True, self._TABLE_OUTPUT):
            result = data_tables()
        assert "所以你该做什么" in result.human_message

    def test_failure_returns_false(self):
        with _mock_run("data", False, "error: db not found"):
            result = data_tables()
        assert result.success is False
        assert "所以你该做什么" in result.human_message


# ── migrate ───────────────────────────────────────────────────────────────────

class TestMigrate:
    def test_migrate_run_success(self):
        with _mock_run("migrate", True, "Migration complete"):
            result = migrate_run()
        assert result.success is True
        assert "migrate_verify" in result.human_message

    def test_migrate_verify_success(self):
        with _mock_run("migrate", True, "✓ All checks passed"):
            result = migrate_verify()
        assert result.success is True

    def test_migrate_verify_failure_human_message(self):
        with _mock_run("migrate", False, "⚠ Some checks failed"):
            result = migrate_verify()
        assert result.success is False
        assert "diff_report" in result.human_message


# ── snapshot ──────────────────────────────────────────────────────────────────

class TestSnapshot:
    def test_pull_success(self):
        with _mock_run("snapshot", True, "Snapshot complete"):
            result = snapshot_pull()
        assert result.success is True
        assert "dashboard_render" in result.human_message

    def test_pull_failure(self):
        with _mock_run("snapshot", False, "Connection refused"):
            result = snapshot_pull()
        assert result.success is False
        assert "qt.gtimg.cn" in result.human_message

    def test_show_with_date(self):
        with _mock_run("snapshot", True, "Report for 2026-05-30"):
            result = snapshot_show("2026-05-30")
        assert result.success is True
        assert result.data["date"] == "2026-05-30"

    def test_show_no_date_defaults_to_today(self):
        with _mock_run("snapshot", True, "Today report"):
            result = snapshot_show()
        assert result.data["date"] == "今日"


# ── dashboard ─────────────────────────────────────────────────────────────────

class TestDashboard:
    def test_render_post_market(self):
        with _mock_run("dashboard", True, "DASHBOARD.html generated"):
            result = dashboard_render("post-market")
        assert result.success is True
        assert "DASHBOARD.html" in result.human_message

    def test_render_pre_market(self):
        with _mock_run("dashboard", True, "DASHBOARD.html generated"):
            result = dashboard_render("pre-market")
        assert result.success is True

    def test_invalid_mode(self):
        result = dashboard_render("invalid-mode")
        assert result.success is False
        assert "post-market" in result.human_message


# ── trade ─────────────────────────────────────────────────────────────────────

class TestTrade:
    def test_decision_new_mentions_cooldown(self):
        with _mock_run("trade", True, "decision_001 created"):
            result = trade_decision("600519", "NEW", notes="测试")
        assert result.success is True
        assert "7 天" in result.human_message

    def test_decision_reduce_cooldown(self):
        with _mock_run("trade", True, "decision_002 created"):
            result = trade_decision("600519", "REDUCE")
        assert "3 天" in result.human_message

    def test_trade_list(self):
        with _mock_run("trade", True, "active decisions"):
            result = trade_list("active")
        assert result.success is True

    def test_trade_apply_success(self):
        with _mock_run("trade", True, "Holdings updated"):
            result = trade_apply("trade_001")
        assert result.success is True
        assert "snapshot_pull" in result.human_message

    def test_exec_monitor_no_trigger(self):
        with _mock_run("trade", True, "No rules triggered"):
            result = exec_monitor()
        assert result.success is True
        assert result.data["triggered"] is False

    def test_exec_monitor_triggered(self):
        with _mock_run("trade", True, "TRIGGERED: STOP_LOSS for 600519"):
            result = exec_monitor()
        assert result.data["triggered"] is True
        assert "触发" in result.human_message


# ── thesis ────────────────────────────────────────────────────────────────────

class TestThesis:
    def test_sync_success(self):
        with _mock_run("thesis", True, "Synced 7 theses"):
            result = thesis_sync()
        assert result.success is True
        assert "thesis_stale" in result.human_message

    def test_score_low_warns(self):
        with _mock_run("thesis", True, "Score updated"):
            result = thesis_score("600519", 2.0)
        assert result.success is True
        assert "2.5" in result.human_message

    def test_stale_success(self):
        with _mock_run("thesis", True, "No stale theses"):
            result = thesis_stale(30)
        assert result.success is True


# ── candidate ─────────────────────────────────────────────────────────────────

class TestCandidate:
    def test_scan_success(self):
        with _mock_run("candidate", True, "Scan complete: 5 candidates"):
            result = candidate_scan(quick=True)
        assert result.success is True
        assert "ic-memo" in result.human_message or "candidate_list" in result.human_message

    def test_list_success(self):
        with _mock_run("candidate", True, "Candidates listed"):
            result = candidate_list()
        assert result.success is True


# ── review ────────────────────────────────────────────────────────────────────

class TestReview:
    def test_log_success(self):
        with _mock_run("review", True, "Review logged"):
            result = review_log(42, error_code="TIMING_ERROR", notes="追高了")
        assert result.success is True
        assert "review_stats" in result.human_message

    def test_stats_success(self):
        with _mock_run("review", True, "Stats generated"):
            result = review_stats(months=6)
        assert result.success is True
        assert "6" in result.human_message


# ── causal ────────────────────────────────────────────────────────────────────

class TestCausal:
    def test_daily_success(self):
        with _mock_run("causal", True, "Daily pipeline complete"):
            result = causal_daily()
        assert result.success is True
        assert "L3+" in result.human_message

    def test_daily_failure_mentions_api_key(self):
        with _mock_run("causal", False, "API key not found"):
            result = causal_daily()
        assert result.success is False
        assert "ANTHROPIC_API_KEY" in result.human_message

    def test_scan_success(self):
        with _mock_run("causal", True, "Scanned 10 signals"):
            result = causal_scan()
        assert result.success is True

    def test_assess_with_code(self):
        with _mock_run("causal", True, "Assessment complete"):
            result = causal_assess(code="600519", explain=True)
        assert result.success is True
        assert result.data["code"] == "600519"

    def test_review_list_pending(self):
        with _mock_run("causal", True, "pending edge 1\npending edge 2"):
            result = causal_review_list()
        assert result.success is True
        assert result.data["pending_count"] > 0

    def test_review_approve(self):
        with _mock_run("causal", True, "Edge #5 approved"):
            result = causal_review_approve(5)
        assert result.success is True

    def test_review_reject(self):
        with _mock_run("causal", True, "Edge #3 rejected"):
            result = causal_review_reject(3, reason="数据不支持")
        assert result.success is True
