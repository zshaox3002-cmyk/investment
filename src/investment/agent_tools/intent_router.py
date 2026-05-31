"""Intent router — maps user natural language to a Skill ID.

Implements the keyword-matching logic defined in prompts/_intent_router.md.
Returns the best-matching skill_id, or None if no match is found.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ── Skill definitions ─────────────────────────────────────────────────────────

@dataclass
class SkillDef:
    skill_id: str
    name: str
    priority: int          # lower = higher priority (P0=0, P1=1, P2=2)
    keywords: list[str] = field(default_factory=list)


_SKILLS: list[SkillDef] = [
    SkillDef(
        skill_id="onboarding",
        name="目标与资产录入",
        priority=0,
        keywords=[
            "开始", "入门", "初始化", "设置目标", "录入资产", "我要开始", "第一次",
            "新手", "怎么用", "配置", "我的目标", "年化", "期望收益", "风险承受",
            "可投资金", "资产录入", "重新设置", "更新目标",
        ],
    ),
    SkillDef(
        skill_id="position",
        name="仓位管理与再平衡巡检",
        priority=0,
        keywords=[
            "仓位", "持仓", "巡检", "再平衡", "rebalance", "超仓", "偏离", "告警",
            "风控", "铁律", "回撤", "止损", "止盈", "减仓", "加仓", "清仓", "建仓",
            "我的仓位", "有没有问题", "超限", "配置偏离", "盘后", "今天怎么样",
            "组合状态",
        ],
    ),
    SkillDef(
        skill_id="stock_screen",
        name="对话式选股",
        priority=2,
        keywords=[
            "选股", "找股票", "候选", "扫描", "筛选", "推荐", "有什么好股", "低估值",
            "高股息", "成长股", "价值股", "什么股票值得买", "帮我找", "符合条件",
            "股息率", "市值", "行业", "赛道", "白马", "蓝筹", "小盘", "大盘",
        ],
    ),
    SkillDef(
        skill_id="causal_insight",
        name="外部信息与因果归因",
        priority=2,
        keywords=[
            "为什么跌", "为什么涨", "原因", "新闻", "消息", "影响", "因果", "利好",
            "利空", "政策", "宏观", "事件", "异动", "解释", "分析一下",
            "最近发生了什么", "对持仓有什么影响", "信号", "因果链", "归因",
        ],
    ),
    SkillDef(
        skill_id="calendar",
        name="投资日历与催办",
        priority=1,
        keywords=[
            "日历", "提醒", "待办", "任务", "计划", "什么时候", "该做什么", "财报",
            "分红", "除权", "到期", "冷静期", "催办", "下一步", "接下来", "本周",
            "本月", "季度", "年度", "schedule", "deadline",
        ],
    ),
    SkillDef(
        skill_id="risk",
        name="组合风险量化",
        priority=1,
        keywords=[
            "风险", "波动率", "相关性", "集中度", "分散", "伪分散", "VaR",
            "风险贡献", "最大回撤", "夏普", "风险量化", "组合风险", "风险指标",
            "压力测试", "风险敞口", "集中风险",
        ],
    ),
    SkillDef(
        skill_id="cost",
        name="交易成本计算",
        priority=2,
        keywords=[
            "手续费", "佣金", "印花税", "过户费", "成本", "费用", "摩擦", "港股",
            "交易成本", "买入成本", "卖出成本", "实际到手", "税费", "费率",
            "算一下费用",
        ],
    ),
    SkillDef(
        skill_id="attribution",
        name="业绩归因",
        priority=1,
        keywords=[
            "归因", "业绩", "收益来源", "收益来自", "收益主要", "赚了多少", "亏了多少", "跑赢", "跑输",
            "基准", "沪深300", "超额收益", "alpha", "beta", "择时", "选股贡献",
            "配置贡献", "复盘", "总结", "这段时间表现", "收益拆解", "收益分析",
        ],
    ),
    SkillDef(
        skill_id="behavior",
        name="行为约束与决策日志",
        priority=2,
        keywords=[
            "冲动", "追高", "恐慌", "情绪", "纪律", "行为", "偏差", "锚定",
            "处置效应", "过度交易", "决策日志", "反思", "为什么买", "为什么卖",
            "有没有犯错", "行为检查", "冷静", "理性", "克制", "FOMO", "止损纪律",
        ],
    ),
]

# ── Routing logic ─────────────────────────────────────────────────────────────

@dataclass
class RouteResult:
    skill_id: Optional[str]
    skill_name: Optional[str]
    score: int                    # number of matched keywords
    matched_keywords: list[str]
    is_ambiguous: bool = False
    alternatives: list[str] = field(default_factory=list)


def route(user_input: str) -> RouteResult:
    """Route user natural language input to the best-matching Skill.

    Returns a RouteResult with the matched skill_id (or None if no match).
    When two skills are within 1 keyword of each other, marks as ambiguous.
    """
    text = user_input.lower()
    scores: list[tuple[SkillDef, int, list[str]]] = []

    for skill in _SKILLS:
        matched = [kw for kw in skill.keywords if kw.lower() in text]
        if matched:
            scores.append((skill, len(matched), matched))

    if not scores:
        return RouteResult(skill_id=None, skill_name=None, score=0, matched_keywords=[])

    # Sort by score desc, then priority asc (lower = higher priority)
    scores.sort(key=lambda x: (-x[1], x[0].priority))
    best_skill, best_score, best_keywords = scores[0]

    # Check for ambiguity: second-best within 1 keyword
    is_ambiguous = False
    alternatives: list[str] = []
    if len(scores) >= 2:
        second_skill, second_score, _ = scores[1]
        if best_score - second_score <= 1:
            is_ambiguous = True
            alternatives = [s.skill_id for s, _, _ in scores[1:3]]

    return RouteResult(
        skill_id=best_skill.skill_id,
        skill_name=best_skill.name,
        score=best_score,
        matched_keywords=best_keywords,
        is_ambiguous=is_ambiguous,
        alternatives=alternatives,
    )


def route_with_message(user_input: str) -> tuple[RouteResult, str]:
    """Route and return a human-readable routing message."""
    result = route(user_input)

    if result.skill_id is None:
        msg = (
            "没有找到匹配的功能。\n"
            "所以你该做什么：请描述你想做什么，例如「查看持仓」、「找股票」、「分析风险」等。"
        )
    elif result.is_ambiguous:
        alt_names = [s.name for s in _SKILLS if s.skill_id in result.alternatives]
        separator = "」或「"
        alts_str = separator.join(alt_names)
        msg = (
            f"你的意图可能是「{result.skill_name}」，也可能是「{alts_str}」。\n"
            "所以你该做什么：请说得更具体一些，帮我确认你想要哪个功能。"
        )
    else:
        kws = "、".join(result.matched_keywords[:3])
        msg = f"已路由到「{result.skill_name}」（匹配关键词：{kws}）。"

    return result, msg


def list_skills() -> list[dict]:
    """Return all skill definitions as a list of dicts."""
    return [
        {
            "skill_id": s.skill_id,
            "name": s.name,
            "priority": f"P{s.priority}",
            "keyword_count": len(s.keywords),
        }
        for s in _SKILLS
    ]
