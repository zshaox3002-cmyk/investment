"""Onboarding business logic — Phase 2.

Covers:
  - Profile creation / update
  - Goal creation
  - Asset inventory recording
  - A/B/C ratio generation
  - capital.yaml user section write
  - Gap analysis (actual vs target)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from investment.core.db import connect, transaction
from investment.core.settings import CAPITAL_PATH, DB_PATH


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ProfileInput:
    investable_capital: float
    risk_tolerance: str          # conservative | moderate | aggressive
    horizon_years: int
    target_annual_return: float  # percent, e.g. 10.0
    max_drawdown_tolerance: float = 20.0
    target_amount: Optional[float] = None
    deadline: Optional[str] = None
    notes: str = ""


@dataclass
class ABCAllocation:
    a_ratio: float
    b_ratio: float
    c_ratio: float
    a_amount: float
    b_amount: float
    c_amount: float
    rationale: str


@dataclass
class OnboardingResult:
    success: bool
    profile_id: Optional[int]
    goal_id: Optional[int]
    allocation: Optional[ABCAllocation]
    gap_analysis: str
    human_message: str
    error: str = ""


# ── Validation ────────────────────────────────────────────────────────────────

_RISK_LEVELS = {"conservative", "moderate", "aggressive"}
_RISK_LABELS = {
    "conservative": "保守型",
    "moderate": "稳健型",
    "aggressive": "积极型",
}


def validate_profile_input(inp: ProfileInput) -> list[str]:
    """Return a list of validation error messages (empty = valid)."""
    errors: list[str] = []
    if inp.investable_capital <= 0:
        errors.append("可投资金额必须大于 0")
    if inp.risk_tolerance not in _RISK_LEVELS:
        errors.append(f"风险承受能力必须是 conservative / moderate / aggressive 之一")
    if not (1 <= inp.horizon_years <= 50):
        errors.append("投资期限必须在 1-50 年之间")
    if not (1.0 <= inp.target_annual_return <= 50.0):
        errors.append("目标年化收益率必须在 1%-50% 之间")
    if not (1.0 <= inp.max_drawdown_tolerance <= 100.0):
        errors.append("最大回撤容忍度必须在 1%-100% 之间")
    # Sanity: aggressive target with conservative risk
    if inp.risk_tolerance == "conservative" and inp.target_annual_return > 15:
        errors.append("保守型风险偏好与超过 15% 的目标年化不匹配，请重新确认")
    return errors


# ── A/B/C ratio generation ────────────────────────────────────────────────────

def generate_abc_allocation(inp: ProfileInput) -> ABCAllocation:
    """Generate A/B/C ratios based on risk tolerance and horizon.

    Rules:
      conservative: A=35%, B=50%, C=15%
      moderate:     A=25%, B=50%, C=25%  (system default)
      aggressive:   A=15%, B=45%, C=40%

    Short horizon (<3y) shifts 5% from C to A regardless of risk level.
    High return target (>15%) with moderate risk shifts 5% from B to C.
    """
    base = {
        "conservative": (0.35, 0.50, 0.15),
        "moderate":     (0.25, 0.50, 0.25),
        "aggressive":   (0.15, 0.45, 0.40),
    }
    a, b, c = base[inp.risk_tolerance]

    rationale_parts = [f"基础配置（{_RISK_LABELS[inp.risk_tolerance]}）：A={a:.0%} B={b:.0%} C={c:.0%}"]

    if inp.horizon_years < 3:
        shift = 0.05
        a = min(a + shift, 0.60)
        c = max(c - shift, 0.05)
        rationale_parts.append(f"投资期限 <3 年，C 档降低 5%（流动性保护）")

    if inp.risk_tolerance == "moderate" and inp.target_annual_return > 15:
        a_adj, b_adj, c_adj = -0.0, -0.05, 0.05
        a = max(a + a_adj, 0.10)
        b = max(b + b_adj, 0.30)
        c = min(c + c_adj, 0.50)
        rationale_parts.append(f"目标年化 >{inp.target_annual_return:.0f}%，C 档上调 5%（收益增强）")

    # Normalize to sum = 1.0
    total = a + b + c
    a, b, c = round(a / total, 4), round(b / total, 4), round(c / total, 4)
    c = round(1.0 - a - b, 4)  # ensure exact sum

    capital = inp.investable_capital
    return ABCAllocation(
        a_ratio=a, b_ratio=b, c_ratio=c,
        a_amount=round(capital * a, 2),
        b_amount=round(capital * b, 2),
        c_amount=round(capital * c, 2),
        rationale="；".join(rationale_parts),
    )


# ── Gap analysis ──────────────────────────────────────────────────────────────

def compute_gap_analysis(inp: ProfileInput, alloc: ABCAllocation) -> str:
    """Return a plain-language gap analysis between current assets and target."""
    capital = inp.investable_capital
    target = inp.target_amount
    years = inp.horizon_years
    rate = inp.target_annual_return / 100

    lines: list[str] = []

    if target and target > capital:
        gap = target - capital
        # Required annual return to reach target
        required_rate = (target / capital) ** (1 / years) - 1 if years > 0 else 0
        lines.append(
            f"目标金额 ¥{target:,.0f}，当前可投资金 ¥{capital:,.0f}，"
            f"差距 ¥{gap:,.0f}（{gap/capital:.0%}）。"
        )
        lines.append(
            f"按 {years} 年期限，需要年化 {required_rate:.1%} 才能达标"
            f"（你的目标是 {rate:.1%}）。"
        )
        if required_rate > rate * 1.2:
            lines.append("⚠️ 目标偏激进，建议适当延长期限或降低目标金额。")
        elif required_rate <= rate * 0.8:
            lines.append("✅ 目标保守，按当前配置大概率可以达成。")
        else:
            lines.append("目标合理，按计划执行即可。")
    elif target and target <= capital:
        lines.append(f"✅ 当前可投资金 ¥{capital:,.0f} 已超过目标 ¥{target:,.0f}，目标已达成。")
    else:
        # No target amount, just show projection
        projected = capital * ((1 + rate) ** years)
        lines.append(
            f"按目标年化 {rate:.1%}，{years} 年后预计资产约 ¥{projected:,.0f}。"
        )

    return " ".join(lines)


# ── DB operations ─────────────────────────────────────────────────────────────

def create_profile(inp: ProfileInput, alloc: ABCAllocation, db_path=None) -> int:
    """Insert user_profile row, return new profile_id."""
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with transaction(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO user_profile
               (risk_tolerance, max_drawdown_tolerance, horizon_years,
                investable_capital, a_ratio, b_ratio, c_ratio, notes,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (inp.risk_tolerance, inp.max_drawdown_tolerance, inp.horizon_years,
             inp.investable_capital, alloc.a_ratio, alloc.b_ratio, alloc.c_ratio,
             inp.notes, now, now),
        )
        return cur.lastrowid


def create_goal(profile_id: int, inp: ProfileInput, db_path=None) -> int:
    """Insert goals row, return new goal_id."""
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with transaction(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO goals
               (profile_id, target_annual_return, target_amount, deadline,
                status, notes, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (profile_id, inp.target_annual_return, inp.target_amount,
             inp.deadline, "active", inp.notes, now),
        )
        return cur.lastrowid


def record_assets(
    profile_id: int,
    assets: list[dict],
    db_path=None,
) -> int:
    """Insert asset_inventory rows. assets: list of {asset_type, amount, account, currency}."""
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    count = 0
    with transaction(db_path) as conn:
        for a in assets:
            conn.execute(
                """INSERT INTO asset_inventory
                   (profile_id, asset_type, amount, account, currency, notes, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (profile_id, a["asset_type"], a["amount"],
                 a.get("account", ""), a.get("currency", "CNY"),
                 a.get("notes", ""), now),
            )
            count += 1
    return count


def get_latest_profile(db_path=None) -> Optional[dict]:
    """Return the most recent user_profile row as a dict, or None."""
    conn = connect(db_path)
    row = conn.execute(
        "SELECT * FROM user_profile ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_active_goals(profile_id: int, db_path=None) -> list[dict]:
    """Return active goals for a profile."""
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT * FROM goals WHERE profile_id=? AND status='active' ORDER BY created_at",
        (profile_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── capital.yaml user section ─────────────────────────────────────────────────

_USER_SECTION_MARKER = "# ── 用户专属配置（由 onboarding 生成，勿手动编辑）"
_USER_SECTION_END = "# ── 用户专属配置结束 ──"


def write_capital_user_section(alloc: ABCAllocation, inp: ProfileInput) -> None:
    """Append/replace the user-specific section in capital.yaml.

    Never touches the existing default section.
    """
    capital_text = CAPITAL_PATH.read_text(encoding="utf-8")

    # Remove existing user section if present
    if _USER_SECTION_MARKER in capital_text:
        start = capital_text.index(_USER_SECTION_MARKER)
        end_marker = _USER_SECTION_END
        if end_marker in capital_text:
            end = capital_text.index(end_marker) + len(end_marker)
            capital_text = capital_text[:start].rstrip() + "\n" + capital_text[end:].lstrip()

    now = datetime.utcnow().strftime("%Y-%m-%d")
    user_section = f"""

{_USER_SECTION_MARKER}
# 生成时间：{now}
# 风险偏好：{_RISK_LABELS[inp.risk_tolerance]}  期限：{inp.horizon_years}年  目标年化：{inp.target_annual_return}%
user_investable_capital: {inp.investable_capital}
user_a_ratio: {alloc.a_ratio}
user_b_ratio: {alloc.b_ratio}
user_c_ratio: {alloc.c_ratio}
user_a_amount: {alloc.a_amount}
user_b_amount: {alloc.b_amount}
user_c_amount: {alloc.c_amount}
user_allocation_rationale: "{alloc.rationale}"
{_USER_SECTION_END}
"""
    CAPITAL_PATH.write_text(capital_text.rstrip() + user_section, encoding="utf-8")


# ── Main entry point ──────────────────────────────────────────────────────────

def run_onboarding(inp: ProfileInput, assets: list[dict] | None = None, db_path=None) -> OnboardingResult:
    """Full onboarding flow: validate → allocate → persist → write config → human message."""
    errors = validate_profile_input(inp)
    if errors:
        return OnboardingResult(
            success=False,
            profile_id=None, goal_id=None, allocation=None,
            gap_analysis="",
            human_message=f"输入有误，请修正后重试：\n" + "\n".join(f"- {e}" for e in errors),
            error="; ".join(errors),
        )

    alloc = generate_abc_allocation(inp)
    gap = compute_gap_analysis(inp, alloc)

    profile_id = create_profile(inp, alloc, db_path)
    goal_id = create_goal(profile_id, inp, db_path)
    if assets:
        record_assets(profile_id, assets, db_path)

    # Write capital.yaml user section (only if using real DB path)
    if db_path is None or str(db_path) == str(DB_PATH):
        try:
            write_capital_user_section(alloc, inp)
        except Exception:
            pass  # non-fatal: DB is the canonical source

    human = _build_human_message(inp, alloc, gap)
    return OnboardingResult(
        success=True,
        profile_id=profile_id,
        goal_id=goal_id,
        allocation=alloc,
        gap_analysis=gap,
        human_message=human,
    )


def _build_human_message(inp: ProfileInput, alloc: ABCAllocation, gap: str) -> str:
    capital = inp.investable_capital
    return f"""## 目标与资产录入完成

### 你的投资画像
- 可投资金额：¥{capital:,.0f}
- 风险承受：{_RISK_LABELS[inp.risk_tolerance]}
- 投资期限：{inp.horizon_years} 年
- 目标年化：{inp.target_annual_return}%
- 最大可接受回撤：{inp.max_drawdown_tolerance}%

### 专属配置方案
| 档位 | 用途 | 比例 | 金额 |
|------|------|------|------|
| A 档 | 生活保障金（货币/债券） | {alloc.a_ratio:.0%} | ¥{alloc.a_amount:,.0f} |
| B 档 | 核心 ETF（宽基指数） | {alloc.b_ratio:.0%} | ¥{alloc.b_amount:,.0f} |
| C 档 | 主动选股 | {alloc.c_ratio:.0%} | ¥{alloc.c_amount:,.0f} |

配置逻辑：{alloc.rationale}

### 目标差距分析
{gap}

所以你该做什么：运行 `inv profile show` 确认配置，然后开始执行 B 档 ETF 建仓计划。"""
