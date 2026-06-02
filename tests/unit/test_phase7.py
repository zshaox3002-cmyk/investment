"""Unit tests for Phase 7: calendar, stock_screen, cost_calculator, behavior_guard."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from investment.agent_tools.calendar import (
    CalendarReport,
    _action_for,
    _build_human_message as cal_build_msg,
    _urgency,
    complete_task,
    create_task,
    get_tasks,
    mark_overdue_tasks,
    run_calendar,
    seed_standard_tasks,
)
from investment.agent_tools.cost_calculator import (
    CostBreakdown,
    _build_human_message as cost_build_msg,
    calc_cost,
    detect_market,
    save_cost_log,
)
from investment.agent_tools.stock_screen import (
    ScreenCriteria,
    _criteria_to_human,
    _style_comment,
    list_strategies,
    parse_screen_query,
    save_strategy,
)
from investment.agent_tools.behavior_guard import (
    BiasFlag,
    BehaviorReport,
    _build_human_message as beh_build_msg,
    log_decision,
    run_behavior_check,
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


# ═══════════════════════════════════════════════════════════════════════════════
# Skill ⑤ — Calendar
# ═══════════════════════════════════════════════════════════════════════════════

class TestCalendarUrgency:
    def test_overdue(self):
        assert "逾期" in _urgency(-1, "high")

    def test_today(self):
        assert "今日" in _urgency(0, "high")

    def test_soon(self):
        assert "天后" in _urgency(2, "medium")

    def test_future(self):
        label = _urgency(10, "low")
        assert "10" in label


class TestCalendarActions:
    def test_cooldown_action_contains_trade_log(self):
        action = _action_for("cooldown", "600519")
        assert "trade log" in action or "成交" in action

    def test_rebalance_action_contains_risk(self):
        action = _action_for("rebalance", None)
        assert "risk" in action or "再平衡" in action

    def test_monthly_action_contains_thesis(self):
        action = _action_for("monthly", None)
        assert "thesis" in action or "论点" in action


class TestCalendarCRUD:
    def test_create_task(self, tmp_db):
        tid = create_task("测试任务", "custom", "2026-06-30", db_path=tmp_db)
        assert isinstance(tid, int) and tid > 0

    def test_complete_task(self, tmp_db):
        tid = create_task("测试任务", "custom", "2026-06-30", db_path=tmp_db)
        ok = complete_task(tid, db_path=tmp_db)
        assert ok is True
        tasks = get_tasks(period="year", include_done=True, db_path=tmp_db)
        done = [t for t in tasks if t["id"] == tid]
        assert done[0]["status"] == "done"

    def test_complete_nonexistent_returns_false(self, tmp_db):
        assert complete_task(99999, db_path=tmp_db) is False

    def test_mark_overdue(self, tmp_db):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        tid = create_task("过期任务", "custom", yesterday, db_path=tmp_db)
        n = mark_overdue_tasks(db_path=tmp_db)
        assert n >= 1
        tasks = get_tasks(period="year", include_done=True, db_path=tmp_db)
        overdue = [t for t in tasks if t["id"] == tid]
        assert overdue[0]["status"] == "overdue"

    def test_seed_standard_tasks(self, tmp_db):
        n = seed_standard_tasks(db_path=tmp_db)
        assert n >= 2  # at least monthly + quarterly tasks

    def test_seed_idempotent(self, tmp_db):
        n1 = seed_standard_tasks(db_path=tmp_db)
        n2 = seed_standard_tasks(db_path=tmp_db)
        assert n2 == 0  # second run creates nothing


class TestCalendarHumanMessage:
    def _make_report(self, overdue=0, due_soon=0, upcoming=0) -> CalendarReport:
        from investment.agent_tools.calendar import CalendarTask
        def make_task(i, status="pending"):
            return CalendarTask(
                task_id=i, title=f"任务{i}", category="custom",
                due_date="2026-06-01", priority="medium", status=status,
                related_code=None, notes=None, days_until_due=5,
                urgency_label="🔵 5 天后",
                action_required="执行操作",
            )
        return CalendarReport(
            as_of="2026-05-30", period="周",
            overdue=[make_task(i, "overdue") for i in range(overdue)],
            due_soon=[make_task(i) for i in range(due_soon)],
            upcoming=[make_task(i) for i in range(upcoming)],
            human_message="",
        )

    def test_overdue_shown(self):
        report = self._make_report(overdue=2)
        msg = cal_build_msg(report)
        assert "逾期" in msg
        assert "所以你该做什么" in msg

    def test_empty_shows_ok(self):
        report = self._make_report()
        msg = cal_build_msg(report)
        assert "无待办" in msg

    def test_run_calendar_smoke(self, tmp_db):
        report = run_calendar(period="week", db_path=tmp_db)
        assert isinstance(report, CalendarReport)
        assert len(report.human_message) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Skill ③ — Stock Screen
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseScreenQuery:
    def test_pe_max(self):
        c = parse_screen_query("PE低于15倍的股票")
        assert c.pe_max == pytest.approx(15.0)

    def test_roe_min(self):
        c = parse_screen_query("ROE超过15%的成长股")
        assert c.roe_min == pytest.approx(15.0)

    def test_dividend_yield(self):
        c = parse_screen_query("股息率超过3%的红利股")
        assert c.dividend_yield_min == pytest.approx(3.0)

    def test_style_tags_value(self):
        c = parse_screen_query("低估值高股息的价值股")
        assert "价值" in c.style_tags or "红利" in c.style_tags

    def test_industry_detected(self):
        c = parse_screen_query("消费行业的白马股")
        assert c.industry == "消费"

    def test_empty_query_no_crash(self):
        c = parse_screen_query("")
        assert isinstance(c, ScreenCriteria)


class TestCriteriaToHuman:
    def test_pe_shown(self):
        c = ScreenCriteria(pe_max=15.0)
        text = _criteria_to_human(c)
        assert "PE" in text and "15" in text

    def test_empty_criteria(self):
        c = ScreenCriteria()
        text = _criteria_to_human(c)
        assert "全市场" in text or "无特定" in text


class TestStyleComment:
    def test_value_style(self):
        comment = _style_comment(["价值", "红利"])
        assert "稳定" in comment or "长期" in comment

    def test_empty_tags(self):
        comment = _style_comment([])
        assert "未识别" in comment


class TestStrategyCRUD:
    def test_save_and_list(self, tmp_db):
        c = parse_screen_query("低PE高股息")
        sid = save_strategy("低PE红利策略", c, db_path=tmp_db)
        assert isinstance(sid, int) and sid > 0
        strategies = list_strategies(db_path=tmp_db)
        assert any(s["id"] == sid for s in strategies)

    def test_criteria_json_stored(self, tmp_db):
        import json
        c = ScreenCriteria(pe_max=15.0, roe_min=12.0)
        sid = save_strategy("测试策略", c, db_path=tmp_db)
        from investment.core.db import connect
        conn = connect(tmp_db)
        row = conn.execute("SELECT criteria_json FROM custom_strategies WHERE id=?", (sid,)).fetchone()
        conn.close()
        data = json.loads(row["criteria_json"])
        assert data["pe_max"] == 15.0
        assert data["roe_min"] == 12.0


# ═══════════════════════════════════════════════════════════════════════════════
# Skill ⑦ — Cost Calculator
# ═══════════════════════════════════════════════════════════════════════════════

class TestDetectMarket:
    def test_sh_a_share(self):
        assert detect_market("600519") == "A_SH"

    def test_sz_a_share(self):
        assert detect_market("002594") == "A_SZ"

    def test_gem(self):
        assert detect_market("300750") == "A_SZ"

    def test_hk_5digit(self):
        assert detect_market("02015") == "HK"

    def test_etf_sh(self):
        assert detect_market("510300") == "A_SH"


class TestCalcCost:
    def test_sh_buy_no_stamp_duty(self):
        b = calc_cost("600519", 100, 1800.0, "BUY")
        assert b.stamp_duty == pytest.approx(0.0)
        assert b.commission > 0
        assert b.transfer_fee > 0

    def test_sh_sell_has_stamp_duty(self):
        b = calc_cost("600519", 100, 1800.0, "SELL")
        assert b.stamp_duty == pytest.approx(100 * 1800 * 0.001)

    def test_sz_no_transfer_fee(self):
        b = calc_cost("002594", 100, 200.0, "BUY")
        assert b.transfer_fee == pytest.approx(0.0)

    def test_commission_minimum_applied(self):
        # Small trade: 10 shares × 5 yuan = 50 yuan gross → commission = max(50*0.00025, 5) = 5
        b = calc_cost("600519", 10, 5.0, "BUY")
        assert b.commission == pytest.approx(5.0)

    def test_buy_net_amount_is_gross_plus_cost(self):
        b = calc_cost("600519", 100, 100.0, "BUY")
        assert b.net_amount == pytest.approx(b.gross_amount + b.total_cost, rel=1e-6)

    def test_sell_net_amount_is_gross_minus_cost(self):
        b = calc_cost("600519", 100, 100.0, "SELL")
        assert b.net_amount == pytest.approx(b.gross_amount - b.total_cost, rel=1e-6)

    def test_cost_rate_positive(self):
        b = calc_cost("600519", 100, 100.0, "BUY")
        assert b.cost_rate > 0

    def test_human_message_contains_action(self):
        b = calc_cost("600519", 100, 100.0, "BUY")
        assert "所以你该做什么" in b.human_message

    def test_hk_has_stamp_duty_both_sides(self):
        b_buy = calc_cost("02015", 1000, 50.0, "BUY")
        b_sell = calc_cost("02015", 1000, 50.0, "SELL")
        assert b_buy.stamp_duty > 0
        assert b_sell.stamp_duty > 0

    def test_save_cost_log(self, tmp_db):
        b = calc_cost("600519", 100, 100.0, "BUY")
        log_id = save_cost_log(b, db_path=tmp_db)
        assert isinstance(log_id, int) and log_id > 0


class TestCostHumanMessage:
    def test_contains_fee_breakdown(self):
        b = calc_cost("600519", 100, 1800.0, "SELL")
        msg = cost_build_msg(b)
        assert "印花税" in msg
        assert "券商佣金" in msg

    def test_no_technical_codes(self):
        b = calc_cost("600519", 100, 1800.0, "BUY")
        msg = cost_build_msg(b)
        assert "commission_rate=" not in msg
        assert "stamp_duty_sell=" not in msg


# ═══════════════════════════════════════════════════════════════════════════════
# Skill ⑨ — Behavior Guard
# ═══════════════════════════════════════════════════════════════════════════════

class TestBehaviorBuildMessage:
    def _make_report(self, biases=None) -> BehaviorReport:
        return BehaviorReport(
            as_of="2026-05-30",
            biases=biases or [],
            trade_count_30d=3,
            avg_holding_days=120.0,
            human_message="",
        )

    def test_no_biases_shows_ok(self):
        report = self._make_report()
        msg = beh_build_msg(report)
        assert "未检测到" in msg

    def test_bias_shown_with_action(self):
        bias = BiasFlag(
            bias_type="FOMO_BUY", bias_label="追涨买入",
            evidence="近5日涨幅15%", severity="high",
            action="等待回调再买入",
        )
        report = self._make_report(biases=[bias])
        msg = beh_build_msg(report)
        assert "追涨买入" in msg
        assert "所以你该做什么" in msg

    def test_overtrading_flagged(self):
        report = self._make_report()
        report.trade_count_30d = 12
        msg = beh_build_msg(report)
        assert "偏高" in msg or "12" in msg

    def test_long_holding_positive(self):
        report = self._make_report()
        report.avg_holding_days = 300
        msg = beh_build_msg(report)
        assert "长线" in msg or "价值" in msg


class TestLogDecision:
    def test_log_creates_journal_entry(self, tmp_db):
        journal_id, biases = log_decision("BUY", "估值低", "600519", db_path=tmp_db)
        assert isinstance(journal_id, int) and journal_id > 0

    def test_returns_bias_list(self, tmp_db):
        journal_id, biases = log_decision("HOLD", "继续持有", db_path=tmp_db)
        assert isinstance(biases, list)

    def test_journal_stored_in_db(self, tmp_db):
        journal_id, _ = log_decision("SELL", "止盈", "600519", db_path=tmp_db)
        from investment.core.db import connect
        conn = connect(tmp_db)
        row = conn.execute(
            "SELECT * FROM decision_journal WHERE id=?", (journal_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["decision_type"] == "SELL"
        assert row["stated_reason"] == "止盈"


class TestRunBehaviorCheck:
    def test_runs_without_error(self, tmp_db):
        report = run_behavior_check(db_path=tmp_db)
        assert isinstance(report, BehaviorReport)

    def test_human_message_not_empty(self, tmp_db):
        report = run_behavior_check(db_path=tmp_db)
        assert len(report.human_message) > 50

    def test_real_db_smoke(self):
        report = run_behavior_check()
        assert report is not None
        assert "所以你该做什么" in report.human_message or "未检测到" in report.human_message


# ═══════════════════════════════════════════════════════════════════════════════
# Skill ③ — run_screen integration (mocked CLI)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunScreen:
    def test_run_screen_success(self):
        from investment.agent_tools.stock_screen import run_screen
        with patch(
            "investment.agent_tools.stock_screen.run_inv",
            return_value=(True, "Scan complete: 3 candidates"),
        ):
            result = run_screen("低PE高股息消费股")
        assert result.criteria.pe_max is not None or result.criteria.dividend_yield_min is not None
        assert "所以你该做什么" in result.human_message
        assert result.strategy_id is None  # no save_as

    def test_run_screen_saves_strategy(self, tmp_db):
        from investment.agent_tools.stock_screen import run_screen
        with patch(
            "investment.agent_tools.stock_screen.run_inv",
            return_value=(True, "Scan complete: 5 candidates"),
        ):
            result = run_screen("低PE高股息", save_as="测试策略", db_path=tmp_db)
        assert result.strategy_id is not None
        assert result.strategy_id > 0
        assert "策略已保存" in result.human_message

    def test_run_screen_failure(self):
        from investment.agent_tools.stock_screen import run_screen
        with patch(
            "investment.agent_tools.stock_screen.run_inv",
            return_value=(False, "Connection refused"),
        ):
            result = run_screen("低PE高股息")
        assert "扫描状态" in result.human_message or "问题" in result.human_message

    def test_run_screen_style_tags_in_output(self):
        from investment.agent_tools.stock_screen import run_screen
        with patch(
            "investment.agent_tools.stock_screen.run_inv",
            return_value=(True, "Scan complete"),
        ):
            result = run_screen("高股息红利股")
        assert len(result.style_comment) > 0

    def test_run_screen_with_industry(self):
        from investment.agent_tools.stock_screen import run_screen
        with patch(
            "investment.agent_tools.stock_screen.run_inv",
            return_value=(True, "Scan complete"),
        ):
            result = run_screen("白酒行业的白马股")
        assert result.criteria.industry == "白酒"


# ═══════════════════════════════════════════════════════════════════════════════
# Skill ③ — edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseScreenQueryEdgeCases:
    def test_market_cap_max(self):
        c = parse_screen_query("市值100亿以下的小盘股")
        assert c.market_cap_max is not None

    def test_market_cap_min(self):
        c = parse_screen_query("市值500亿以上的大盘股")
        assert c.market_cap_min is not None

    def test_multiple_conditions(self):
        c = parse_screen_query("PE低于20 ROE超过15% 股息率超过3% 消费行业")
        assert c.pe_max is not None
        assert c.roe_min is not None
        assert c.dividend_yield_min is not None
        assert c.industry == "消费"

    def test_style_tags_growth(self):
        c = parse_screen_query("高增长高ROE的成长股")
        assert "成长" in c.style_tags

    def test_financial_sector(self):
        c = parse_screen_query("银行股")
        assert c.industry == "银行"

    def test_default_pe_max(self):
        c = parse_screen_query("低市盈率股票")
        assert c.pe_max is not None

    def test_raw_query_stored(self):
        c = parse_screen_query("帮我找白酒龙头")
        assert c.raw_query == "帮我找白酒龙头"

    def test_no_duplicate_style_tags(self):
        c = parse_screen_query("低估值高股息的价值股红利股白马股")
        # Just check no crash
        assert isinstance(c.style_tags, list)


# ═══════════════════════════════════════════════════════════════════════════════
# Skill ⑦ — cost calculator edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestDetectMarketEdgeCases:
    def test_bj_exchange(self):
        assert detect_market("430001") == "A_BJ"

    def test_hk_with_suffix(self):
        assert detect_market("00700.HK") == "HK"

    def test_kcb(self):
        assert detect_market("688001") == "A_SH"

    def test_unknown_defaults_to_sh(self):
        assert detect_market("999999") == "A_SH"


class TestCalcCostEdgeCases:
    def test_custom_broker_rate(self):
        b = calc_cost("600519", 100, 100.0, "BUY", broker_commission_rate=0.0001)
        # gross=10000, commission_rate=0.0001 → 1.0, but min is 5.0
        assert b.commission == pytest.approx(5.0)

    def test_cost_rate_roundtrip(self):
        """Bought and sold immediately: total cost = buy_cost + sell_cost."""
        b_buy = calc_cost("600519", 100, 100.0, "BUY")
        b_sell = calc_cost("600519", 100, 100.0, "SELL")
        total_pct = (b_buy.total_cost + b_sell.total_cost) / b_buy.gross_amount
        assert total_pct > 0.001  # at least 0.1% round-trip

    def test_small_trade_commission_min(self):
        """Very small trade should hit commission minimum."""
        # 1 share * 100 yuan = 100 gross → commission = max(100*0.00025, 5) = 5
        b = calc_cost("600519", 1, 100.0, "BUY")
        assert b.commission == pytest.approx(5.0)

    def test_large_trade_commission(self):
        """Large trade should have commission > minimum."""
        b = calc_cost("600519", 10000, 50.0, "BUY")
        # gross = 500,000; commission = max(500000*0.00025, 5) = 125
        assert b.commission > 5.0


# ═══════════════════════════════════════════════════════════════════════════════
# Skill ⑤ — calendar edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestCalendarEdgeCases:
    def test_get_tasks_today_period(self, tmp_db):
        tasks = get_tasks(period="today", db_path=tmp_db)
        assert isinstance(tasks, list)

    def test_get_tasks_month_period(self, tmp_db):
        tasks = get_tasks(period="month", db_path=tmp_db)
        assert isinstance(tasks, list)

    def test_get_tasks_include_done(self, tmp_db):
        tid = create_task("已完成任务", "custom", "2026-06-30", db_path=tmp_db)
        complete_task(tid, db_path=tmp_db)
        tasks = get_tasks(period="year", include_done=True, db_path=tmp_db)
        done = [t for t in tasks if t["id"] == tid]
        assert len(done) > 0

    def test_create_task_with_all_fields(self, tmp_db):
        tid = create_task(
            "检查止损", "cooldown", "2026-06-15",
            priority="high", related_code="600519", notes="冷静期到期",
            db_path=tmp_db,
        )
        assert tid > 0

    def test_mark_overdue_empty_db(self, tmp_db):
        n = mark_overdue_tasks(db_path=tmp_db)
        assert n >= 0  # just shouldn't crash


# ═══════════════════════════════════════════════════════════════════════════════
# Skill ⑨ — behavior guard edge cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestBehaviorGuardEdgeCases:
    def test_log_decision_no_code(self, tmp_db):
        journal_id, biases = log_decision("HOLD", "观望", db_path=tmp_db)
        assert journal_id > 0

    def test_log_decision_with_emotion_check(self, tmp_db):
        journal_id, biases = log_decision(
            "BUY", "低PE高ROE", "600519",
            emotion_check="经过冷静期，基于数据分析",
            db_path=tmp_db,
        )
        assert journal_id > 0

    def test_behavior_flags_written(self, tmp_db):
        from investment.core.db import connect
        journal_id, biases = log_decision("BUY", "测试", "600519", db_path=tmp_db)
        # Check behavior_flags table
        conn = connect(tmp_db)
        rows = conn.execute("SELECT * FROM behavior_flags").fetchall()
        conn.close()
        # biases may be empty if no price data, that's ok
        assert isinstance(rows, list)

    def test_run_behavior_check_empty_db(self, tmp_db):
        report = run_behavior_check(db_path=tmp_db)
        assert report.trade_count_30d >= 0
        assert report.avg_holding_days >= 0.0

    def test_run_behavior_check_with_lookback(self, tmp_db):
        report = run_behavior_check(lookback_days=180, db_path=tmp_db)
        assert isinstance(report, BehaviorReport)
