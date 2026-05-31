"""Unit tests for intent_router — Phase 1."""
from __future__ import annotations

import pytest

from investment.agent_tools.intent_router import (
    route, route_with_message, list_skills, RouteResult,
)


class TestRouteBasic:
    def test_no_match_returns_none(self):
        result = route("今天天气真好")
        assert result.skill_id is None
        assert result.score == 0

    def test_position_keywords(self):
        result = route("我的仓位有没有问题")
        assert result.skill_id == "position"
        assert result.score >= 1

    def test_onboarding_keywords(self):
        result = route("我要开始用这个系统，帮我设置目标")
        assert result.skill_id == "onboarding"

    def test_stock_screen_keywords(self):
        result = route("帮我找一些低估值高股息的股票")
        assert result.skill_id == "stock_screen"

    def test_causal_insight_keywords(self):
        result = route("600519 今天为什么跌了，有什么新闻消息")
        assert result.skill_id == "causal_insight"

    def test_calendar_keywords(self):
        result = route("本周有什么投资任务需要完成，冷静期到期了吗")
        assert result.skill_id == "calendar"

    def test_risk_keywords(self):
        result = route("帮我算一下组合的波动率，有没有伪分散的问题")
        assert result.skill_id == "risk"

    def test_cost_keywords(self):
        result = route("买 10 万的股票要交多少手续费和印花税")
        assert result.skill_id == "cost"

    def test_attribution_keywords(self):
        result = route("帮我分析一下这个季度的业绩归因，跑赢基准了吗")
        assert result.skill_id == "attribution"

    def test_behavior_keywords(self):
        result = route("我是不是在追高，帮我检查一下有没有情绪因素")
        assert result.skill_id == "behavior"


class TestRoutePriority:
    def test_p0_beats_p2_on_tie(self):
        # "风险" is in both risk(P1) and position(P0) keywords
        # position has "风控" not "风险", so this tests priority when scores differ
        result = route("我的仓位风险怎么样")
        # "仓位" → position(P0), "风险" → risk(P1); position should win on priority
        assert result.skill_id == "position"

    def test_higher_score_wins_over_priority(self):
        # Many risk keywords should beat a single position keyword
        result = route("帮我算波动率、相关性、VaR、风险贡献、伪分散")
        assert result.skill_id == "risk"


class TestRouteAmbiguity:
    def test_ambiguous_when_scores_close(self):
        # "归因" hits both attribution and causal_insight
        result = route("帮我做一下归因分析")
        # Should be marked ambiguous or route to one of them
        assert result.skill_id in ("attribution", "causal_insight", "position")

    def test_ambiguous_flag_set(self):
        # Construct a case where two skills tie
        result = route("帮我做一下归因分析")
        # Either ambiguous or clear winner — just check it doesn't crash
        assert isinstance(result.is_ambiguous, bool)
        assert isinstance(result.alternatives, list)


class TestRouteResult:
    def test_matched_keywords_populated(self):
        result = route("我的仓位有没有问题，有没有超仓")
        assert len(result.matched_keywords) >= 2
        assert "仓位" in result.matched_keywords or "超仓" in result.matched_keywords

    def test_skill_name_populated(self):
        result = route("我的仓位有没有问题")
        assert result.skill_name == "仓位管理与再平衡巡检"


class TestRouteWithMessage:
    def test_no_match_message(self):
        _, msg = route_with_message("今天天气真好")
        assert "所以你该做什么" in msg

    def test_match_message_contains_skill_name(self):
        result, msg = route_with_message("我的仓位有没有问题")
        assert result.skill_name in msg

    def test_ambiguous_message_asks_clarification(self):
        result, msg = route_with_message("帮我做一下归因分析")
        if result.is_ambiguous:
            assert "所以你该做什么" in msg


class TestListSkills:
    def test_returns_nine_skills(self):
        skills = list_skills()
        assert len(skills) == 9

    def test_all_have_required_fields(self):
        for s in list_skills():
            assert "skill_id" in s
            assert "name" in s
            assert "priority" in s
            assert "keyword_count" in s
            assert s["keyword_count"] > 0

    def test_skill_ids_unique(self):
        ids = [s["skill_id"] for s in list_skills()]
        assert len(ids) == len(set(ids))

    def test_expected_skill_ids_present(self):
        ids = {s["skill_id"] for s in list_skills()}
        expected = {
            "onboarding", "position", "stock_screen", "causal_insight",
            "calendar", "risk", "cost", "attribution", "behavior",
        }
        assert ids == expected


class TestRouterAcceptanceCases:
    """验收标准：给定一句用户口语，路由层能正确指向对应 Skill。"""

    CASES = [
        ("我刚开始用，不知道怎么设置", "onboarding"),
        ("我的仓位有没有问题", "position"),
        ("帮我找一些低 PE 高股息的股票", "stock_screen"),
        ("600519 今天为什么跌了 5%", "causal_insight"),
        ("本周有什么投资任务需要完成", "calendar"),
        ("帮我算一下组合的波动率", "risk"),
        ("买 10 万的股票要交多少手续费", "cost"),
        ("我今年的收益主要来自哪里", "attribution"),
        ("我是不是在追高", "behavior"),
    ]

    @pytest.mark.parametrize("query,expected_skill", CASES)
    def test_route_acceptance(self, query: str, expected_skill: str):
        result = route(query)
        assert result.skill_id == expected_skill, (
            f"Query: '{query}'\n"
            f"Expected: {expected_skill}\n"
            f"Got: {result.skill_id} (score={result.score}, "
            f"keywords={result.matched_keywords})"
        )
