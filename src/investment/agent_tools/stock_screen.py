"""Stock screener — Phase 7 Skill ③.

Converts natural language queries into structured screening criteria,
runs candidate scan, annotates with style tags, persists strategies.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from investment.core.db import connect, transaction
from investment.agent_tools._runner import run_inv


# ── Criteria parsing ──────────────────────────────────────────────────────────

@dataclass
class ScreenCriteria:
    pe_max: Optional[float] = None
    pe_min: Optional[float] = None
    roe_min: Optional[float] = None
    dividend_yield_min: Optional[float] = None
    market_cap_min: Optional[float] = None   # 亿元
    market_cap_max: Optional[float] = None
    industry: Optional[str] = None
    style_tags: list[str] = field(default_factory=list)
    raw_query: str = ""


_STYLE_KEYWORDS: dict[str, list[str]] = {
    "价值":   ["低估值", "价值", "低PE", "便宜", "低市盈率"],
    "成长":   ["成长", "高增长", "高ROE", "高景气"],
    "红利":   ["高股息", "分红", "股息率", "红利"],
    "白马":   ["白马", "蓝筹", "龙头", "护城河"],
    "小盘":   ["小盘", "小市值", "中小"],
    "大盘":   ["大盘", "大市值", "权重"],
}

_NUMBER_PATTERN = re.compile(r"(\d+(?:\.\d+)?)")


def _extract_number(text: str, default: Optional[float] = None) -> Optional[float]:
    m = _NUMBER_PATTERN.search(text)
    return float(m.group(1)) if m else default


def parse_screen_query(query: str) -> ScreenCriteria:
    """Parse a natural language screening query into structured criteria."""
    q = query.lower()
    criteria = ScreenCriteria(raw_query=query)

    # PE
    if "pe" in q or "市盈率" in q:
        if "低" in q or "小于" in q or "以下" in q or "<" in q:
            criteria.pe_max = _extract_number(q, 20.0)
        elif "高" in q or "大于" in q or "以上" in q or ">" in q:
            criteria.pe_min = _extract_number(q, 30.0)
        else:
            criteria.pe_max = _extract_number(q, 20.0)

    # ROE
    if "roe" in q:
        criteria.roe_min = _extract_number(q, 15.0)

    # Dividend yield
    if "股息" in q or "分红" in q or "dividend" in q:
        criteria.dividend_yield_min = _extract_number(q, 3.0)

    # Market cap
    if "市值" in q:
        nums = _NUMBER_PATTERN.findall(q)
        if "以下" in q or "小于" in q or "小盘" in q:
            criteria.market_cap_max = float(nums[0]) if nums else 100.0
        elif "以上" in q or "大于" in q or "大盘" in q:
            criteria.market_cap_min = float(nums[0]) if nums else 500.0

    # Industry
    industries = ["消费", "医药", "科技", "金融", "能源", "新能源", "银行", "保险",
                  "地产", "制造", "化工", "钢铁", "有色", "食品", "白酒"]
    for ind in industries:
        if ind in q:
            criteria.industry = ind
            break

    # Style tags
    for tag, keywords in _STYLE_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            criteria.style_tags.append(tag)

    return criteria


# ── Strategy persistence ──────────────────────────────────────────────────────

def save_strategy(
    name: str,
    criteria: ScreenCriteria,
    description: str = "",
    db_path=None,
) -> int:
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    criteria_dict = {
        k: v for k, v in {
            "pe_max": criteria.pe_max, "pe_min": criteria.pe_min,
            "roe_min": criteria.roe_min,
            "dividend_yield_min": criteria.dividend_yield_min,
            "market_cap_min": criteria.market_cap_min,
            "market_cap_max": criteria.market_cap_max,
            "industry": criteria.industry,
        }.items() if v is not None
    }
    with transaction(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO custom_strategies
               (name, description, criteria_json, source_query, style_tags, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (name, description,
             json.dumps(criteria_dict, ensure_ascii=False),
             criteria.raw_query,
             json.dumps(criteria.style_tags, ensure_ascii=False),
             now, now),
        )
        return cur.lastrowid


def list_strategies(db_path=None) -> list[dict]:
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT * FROM custom_strategies WHERE active=1 ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Screening execution ───────────────────────────────────────────────────────

@dataclass
class ScreenResult:
    criteria: ScreenCriteria
    strategy_id: Optional[int]
    candidates_raw: str
    style_comment: str
    human_message: str


def _criteria_to_human(c: ScreenCriteria) -> str:
    parts = []
    if c.pe_max:
        parts.append(f"PE ≤ {c.pe_max}")
    if c.pe_min:
        parts.append(f"PE ≥ {c.pe_min}")
    if c.roe_min:
        parts.append(f"ROE ≥ {c.roe_min}%")
    if c.dividend_yield_min:
        parts.append(f"股息率 ≥ {c.dividend_yield_min}%")
    if c.market_cap_max:
        parts.append(f"市值 ≤ {c.market_cap_max}亿")
    if c.market_cap_min:
        parts.append(f"市值 ≥ {c.market_cap_min}亿")
    if c.industry:
        parts.append(f"行业：{c.industry}")
    return "、".join(parts) if parts else "无特定条件（全市场扫描）"


def _style_comment(tags: list[str]) -> str:
    if not tags:
        return "未识别出明确风格偏好"
    return f"风格标签：{'、'.join(tags)}。这类标的通常{'稳定性好' if '价值' in tags or '红利' in tags else '成长性强'}，适合{'长期持有' if '白马' in tags else '中期配置'}。"


def run_screen(
    query: str,
    save_as: Optional[str] = None,
    db_path=None,
) -> ScreenResult:
    """Parse query, run candidate scan, return results with style annotation."""
    criteria = parse_screen_query(query)

    # Run candidate scan (quick mode to avoid long waits)
    success, output = run_inv("candidate", "scan", "--quick")

    strategy_id = None
    if save_as:
        strategy_id = save_strategy(save_as, criteria, db_path=db_path)

    style_comment = _style_comment(criteria.style_tags)
    criteria_human = _criteria_to_human(criteria)

    lines = [
        f"## 选股结果\n",
        f"### 你的筛选条件\n{criteria_human}\n",
        f"### 风格分析\n{style_comment}\n",
    ]

    if success:
        lines.append("### 候选标的\n运行 `inv candidate list` 查看完整候选池。\n")
    else:
        lines.append(f"### 扫描状态\n扫描遇到问题，请检查网络连接后重试。\n")

    if strategy_id:
        lines.append(f"策略已保存（ID: {strategy_id}，名称：{save_as}），下次可直接复用。\n")

    lines.append("所以你该做什么：对感兴趣的候选标的运行 `/ic-memo` 做深度分析，通过后才能建仓。")

    return ScreenResult(
        criteria=criteria,
        strategy_id=strategy_id,
        candidates_raw=output,
        style_comment=style_comment,
        human_message="\n".join(lines),
    )
