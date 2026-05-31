"""Unit tests for Phase 3: translator + position_monitor."""
from __future__ import annotations

import pytest

from investment.agent_tools.translator import (
    HumanAlert,
    fmt_cny,
    fmt_pct,
    translate_alert,
    translate_alert_type,
    translate_alerts,
    translate_causal_layer,
    translate_decision_type,
    translate_deviation,
    translate_error_code,
    translate_risk_tolerance,
    translate_rule_path,
    translate_score,
    translate_severity,
    translate_stop_type,
)
from investment.agent_tools.position_monitor import (
    HoldingSummary,
    PositionReport,
    RuleBreach,
    TrancheSummary,
    _build_human_message,
    _compute_tranches,
    _rebalance_needed,
    _translate_breaches,
)
from investment.core.db import init_db
from investment.core.sql_migrator import run_sql_migrations


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    run_sql_migrations(db_path=db_path)
    return db_path


# ── Translator: basic translations ───────────────────────────────────────────

class TestTranslatorBasic:
    def test_alert_type_known(self):
        assert translate_alert_type("single_stock_drawdown_l2") == "个股回撤审视（L2）"

    def test_alert_type_unknown_passthrough(self):
        assert translate_alert_type("unknown_type") == "unknown_type"

    def test_severity_critical(self):
        assert "紧急" in translate_severity("critical")

    def test_severity_warning(self):
        assert "警告" in translate_severity("warning")

    def test_severity_info(self):
        assert "提示" in translate_severity("info")

    def test_rule_path_known(self):
        assert translate_rule_path("single_stock_max") == "单股仓位上限"

    def test_decision_type_new(self):
        assert translate_decision_type("NEW") == "新建仓"

    def test_decision_type_case_insensitive(self):
        assert translate_decision_type("reduce") == "减仓"

    def test_stop_type_stop_loss(self):
        assert translate_stop_type("STOP_LOSS") == "止损规则"

    def test_risk_tolerance_moderate(self):
        assert translate_risk_tolerance("moderate") == "稳健型"

    def test_causal_layer_l3(self):
        assert "持仓层" in translate_causal_layer("L3_holding")

    def test_error_code_timing(self):
        assert "时机" in translate_error_code("TIMING_ERROR")


class TestTranslatorScore:
    def test_score_high(self):
        label = translate_score(4.5)
        assert "强劲" in label

    def test_score_medium(self):
        label = translate_score(3.2)
        assert "基本成立" in label

    def test_score_low(self):
        label = translate_score(2.1)
        assert "裂缝" in label

    def test_score_very_low(self):
        label = translate_score(1.5)
        assert "严重" in label


class TestTranslatorFormatters:
    def test_fmt_pct_basic(self):
        assert fmt_pct(0.25) == "25.0%"

    def test_fmt_pct_negative(self):
        assert fmt_pct(-0.138) == "-13.8%"

    def test_fmt_cny_millions(self):
        result = fmt_cny(1_500_000)
        assert "M" in result or "万" in result

    def test_fmt_cny_wan(self):
        result = fmt_cny(50_000)
        assert "万" in result

    def test_fmt_cny_small(self):
        result = fmt_cny(999)
        assert "¥" in result


class TestTranslatorAlert:
    def _make_alert(self, atype="single_stock_drawdown_l2", severity="warning"):
        return {
            "alert_type": atype,
            "severity": severity,
            "message": "测试消息",
            "code": "600519",
            "name": "贵州茅台",
        }

    def test_translate_alert_returns_human_alert(self):
        result = translate_alert(self._make_alert())
        assert isinstance(result, HumanAlert)

    def test_human_alert_has_rationale(self):
        result = translate_alert(self._make_alert())
        assert len(result.rationale) > 0

    def test_human_alert_has_action(self):
        result = translate_alert(self._make_alert())
        assert len(result.action) > 0

    def test_human_alert_to_text_contains_action_directive(self):
        result = translate_alert(self._make_alert())
        text = result.to_text()
        assert "所以你该做什么" in text

    def test_translate_alerts_list(self):
        alerts = [self._make_alert(), self._make_alert("account_drawdown_l3", "critical")]
        results = translate_alerts(alerts)
        assert len(results) == 2
        assert all(isinstance(r, HumanAlert) for r in results)

    def test_critical_alert_has_critical_label(self):
        result = translate_alert(self._make_alert("account_drawdown_l3", "critical"))
        assert "紧急" in result.severity_label


class TestTranslatorDeviation:
    def test_within_tolerance_no_action(self):
        text = translate_deviation("C", 0.25, 0.25, 250_000, 250_000)
        assert "无需调整" in text

    def test_overweight_detected(self):
        text = translate_deviation("C", 0.40, 0.25, 400_000, 250_000)
        assert "超配" in text

    def test_underweight_detected(self):
        text = translate_deviation("B", 0.30, 0.50, 300_000, 500_000)
        assert "低配" in text


# ── Position monitor: compute_tranches ───────────────────────────────────────

class TestComputeTranches:
    def _make_holdings(self):
        return [
            {"tranche": "B", "market_value": 500_000},
            {"tranche": "C", "market_value": 300_000},
            {"tranche": "C", "market_value": 200_000},
        ]

    def test_total_includes_cash(self):
        holdings = self._make_holdings()
        _, total = _compute_tranches(holdings, cash_value=200_000, profile=None)
        assert total == pytest.approx(1_200_000)

    def test_tranche_ratios_sum_to_one(self):
        holdings = self._make_holdings()
        tranches, total = _compute_tranches(holdings, cash_value=200_000, profile=None)
        ratio_sum = sum(t.actual_ratio for t in tranches)
        assert ratio_sum == pytest.approx(1.0, abs=1e-6)

    def test_with_profile_shows_deviation(self):
        holdings = self._make_holdings()
        profile = {"a_ratio": 0.25, "b_ratio": 0.50, "c_ratio": 0.25}
        tranches, _ = _compute_tranches(holdings, cash_value=200_000, profile=profile)
        c_tranche = next(t for t in tranches if t.tranche == "C")
        assert c_tranche.target_ratio == 0.25

    def test_without_profile_no_target(self):
        holdings = self._make_holdings()
        tranches, _ = _compute_tranches(holdings, cash_value=200_000, profile=None)
        for t in tranches:
            assert t.target_ratio == 0.0

    def test_empty_holdings_no_crash(self):
        tranches, total = _compute_tranches([], cash_value=100_000, profile=None)
        assert len(tranches) == 3
        assert total == pytest.approx(100_000)


class TestRebalanceNeeded:
    def _make_tranches(self, actual_ratios, target_ratios):
        return [
            TrancheSummary(
                tranche=t, market_value=0,
                target_ratio=tr, actual_ratio=ar,
                deviation_text="",
            )
            for t, ar, tr in zip(["A", "B", "C"], actual_ratios, target_ratios)
        ]

    def test_no_rebalance_within_threshold(self):
        tranches = self._make_tranches([0.25, 0.50, 0.25], [0.25, 0.50, 0.25])
        assert _rebalance_needed(tranches) is False

    def test_rebalance_needed_when_over_threshold(self):
        tranches = self._make_tranches([0.10, 0.50, 0.40], [0.25, 0.50, 0.25])
        assert _rebalance_needed(tranches) is True

    def test_no_rebalance_when_no_target(self):
        tranches = self._make_tranches([0.10, 0.50, 0.40], [0.0, 0.0, 0.0])
        assert _rebalance_needed(tranches) is False


class TestTranslateBreaches:
    def test_known_rule_path_translated(self):
        raw = [{"rule_path": "single_stock_max", "current_value": 0.45,
                "threshold": 0.25, "status": "active", "code": "", "name": ""}]
        breaches = _translate_breaches(raw)
        assert len(breaches) == 1
        assert "单股仓位上限" in breaches[0].rule_name
        assert "所以你该做什么" not in breaches[0].rule_name  # action is separate field
        assert len(breaches[0].action_required) > 0

    def test_unknown_rule_path_passthrough(self):
        raw = [{"rule_path": "unknown_rule", "current_value": 0.5,
                "threshold": 0.3, "status": "active", "code": "", "name": ""}]
        breaches = _translate_breaches(raw)
        assert len(breaches) == 1


class TestBuildHumanMessage:
    def _make_report(self, alerts=None, breaches=None, rebalance=False, has_profile=False):
        tranches = [
            TrancheSummary("A", 300_000, 0.25, 0.30, "A 档：30%，目标 25%，超配 5%"),
            TrancheSummary("B", 500_000, 0.50, 0.50, "B 档：50%，接近目标"),
            TrancheSummary("C", 200_000, 0.25, 0.20, "C 档：20%，目标 25%，低配 5%"),
        ]
        holdings = [
            HoldingSummary("600519", "贵州茅台", "C", 200_000, 180_000, 0.11, 1800.0, 100.0, 1.0)
        ]
        return PositionReport(
            as_of="2026-05-30",
            total_portfolio_value=1_000_000,
            holdings=holdings,
            tranches=tranches,
            alerts=alerts or [],
            rule_breaches=breaches or [],
            rebalance_needed=rebalance,
            human_message="",
            has_profile=has_profile,
        )

    def test_no_alerts_shows_ok(self):
        report = self._make_report()
        msg = _build_human_message(report)
        assert "无告警" in msg

    def test_critical_alert_shown_prominently(self):
        from investment.agent_tools.translator import translate_alert
        alert = translate_alert({
            "alert_type": "account_drawdown_l3", "severity": "critical",
            "message": "回撤超限", "code": "", "name": "账户",
        })
        report = self._make_report(alerts=[alert])
        msg = _build_human_message(report)
        assert "紧急" in msg
        assert "所以你该做什么" in msg

    def test_rebalance_needed_shown(self):
        report = self._make_report(rebalance=True, has_profile=True)
        msg = _build_human_message(report)
        assert "再平衡" in msg
        assert "所以你该做什么" in msg

    def test_rule_breaches_shown(self):
        breach = RuleBreach("单股仓位上限（25%）", 0.45, 0.25, "active",
                            "运行 inv trade decision 启动减仓")
        report = self._make_report(breaches=[breach])
        msg = _build_human_message(report)
        assert "规则违反" in msg
        assert "所以你该做什么" in msg

    def test_holdings_table_present(self):
        report = self._make_report()
        msg = _build_human_message(report)
        assert "贵州茅台" in msg


# ── Integration: run_position_monitor with real DB ────────────────────────────

class TestPositionMonitorIntegration:
    def test_runs_without_error(self):
        """Smoke test against the real DB."""
        from investment.agent_tools.position_monitor import run_position_monitor
        report = run_position_monitor()
        assert isinstance(report, PositionReport)
        assert report.as_of is not None
        assert report.total_portfolio_value >= 0

    def test_human_message_not_empty(self):
        from investment.agent_tools.position_monitor import run_position_monitor
        report = run_position_monitor()
        assert len(report.human_message) > 100

    def test_human_message_contains_action_directive(self):
        from investment.agent_tools.position_monitor import run_position_monitor
        report = run_position_monitor()
        # Either "所以你该做什么" in alerts/breaches, or "无告警" message
        assert "所以你该做什么" in report.human_message or "无告警" in report.human_message

    def test_no_technical_codes_in_output(self):
        """Verify technical codes are not leaked into human message."""
        from investment.agent_tools.position_monitor import run_position_monitor
        report = run_position_monitor()
        msg = report.human_message
        # These should not appear in user-facing output
        forbidden = ["alert_type=", "instrument_id=", "rule_path=", "d1=", "d2="]
        for f in forbidden:
            assert f not in msg, f"Technical code '{f}' leaked into human message"
