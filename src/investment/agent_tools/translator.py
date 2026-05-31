"""Human-readable translation layer — Phase 3.

Converts technical codes, rule names, and numeric values into plain Chinese
with an attached "所以你该做什么" action directive.

This module is the single source of truth for all user-facing text.
Every Skill must route its output through here before presenting to the user.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ── Translation tables ────────────────────────────────────────────────────────

ALERT_TYPE_NAMES: dict[str, str] = {
    "single_stock_drawdown_l1": "个股回撤预警（L1）",
    "single_stock_drawdown_l2": "个股回撤审视（L2）",
    "single_stock_drawdown_l3": "个股回撤强制线（L3）",
    "single_stock_position":    "单股仓位超限",
    "account_drawdown_l1":      "账户回撤预警（L1）",
    "account_drawdown_l2":      "账户回撤控制（L2）",
    "account_drawdown_l3":      "账户回撤硬刹车（L3）",
    "theme_concentration":      "主题集中度超限",
    "etf_drawdown":             "ETF 回撤预警",
    "etf_drift":                "ETF 偏离目标",
    "meituan_rsu_drawdown":     "美团 RSU 回撤",
    "meituan_rsu_daily_drop":   "美团 RSU 单日下跌",
    "stop_rule_stop_loss":      "止损触发",
    "stop_rule_take_profit":    "止盈触发",
    "stop_rule_grid_sell":      "网格卖出触发",
    "stop_rule_grid_buy":       "网格买入触发",
    "stop_rule_hard_dd":        "硬性回撤止损触发",
}

SEVERITY_LABELS: dict[str, str] = {
    "critical": "🔴 紧急",
    "warning":  "🟡 警告",
    "info":     "🔵 提示",
}

RULE_PATH_NAMES: dict[str, str] = {
    "single_stock_max":      "单股仓位上限",
    "theme_concentration":   "主题集中度",
    "active_position_total": "C 档主动选股总仓位",
    "drawdown_review":       "单股回撤触发审查",
    "portfolio_drawdown":    "账户整体回撤",
    "cooldown_buy":          "买入冷静期",
    "cooldown_sell":         "卖出冷静期",
    "cooldown_add":          "补仓冷静期",
    "ic_memo_required":      "买入前必须完成 IC Memo",
}

DECISION_TYPE_NAMES: dict[str, str] = {
    "NEW":       "新建仓",
    "ADD":       "加仓",
    "REDUCE":    "减仓",
    "EXIT":      "清仓",
    "REBALANCE": "再平衡",
    "EMERGENCY": "紧急操作",
}

STOP_TYPE_NAMES: dict[str, str] = {
    "STOP_LOSS":   "止损规则",
    "TAKE_PROFIT": "止盈规则",
    "GRID_SELL":   "网格卖出",
    "GRID_BUY":    "网格买入",
    "HARD_DD":     "硬性回撤止损",
}

RISK_TOLERANCE_NAMES: dict[str, str] = {
    "conservative": "保守型",
    "moderate":     "稳健型",
    "aggressive":   "积极型",
}

CAUSAL_LAYER_NAMES: dict[str, str] = {
    "L1_macro":    "宏观层（利率、汇率、政策）",
    "L2_sector":   "行业层（行业政策、竞争格局）",
    "L3_holding":  "持仓层（直接影响你持有的股票）",
    "L4_indicator":"指标层（财务指标、估值）",
}

SCORE_LABELS: dict[tuple[float, float], str] = {
    (4.0, 5.0): "论点强劲，核心支柱全部成立",
    (3.0, 3.9): "论点基本成立，有个别支柱需关注",
    (2.0, 2.9): "论点出现裂缝，建议重新审查",
    (1.0, 1.9): "论点严重受损，考虑减仓或清仓",
}

ERROR_CODE_NAMES: dict[str, str] = {
    "TIMING_ERROR":  "时机判断错误（追高杀跌）",
    "THESIS_BREAK":  "投资逻辑失效",
    "OVERSIZE":      "仓位过重",
    "PANIC_SELL":    "恐慌性卖出",
    "FOMO_BUY":      "追涨买入",
    "NO_STOP_LOSS":  "未设止损",
}


# ── Translation functions ─────────────────────────────────────────────────────

def translate_alert_type(alert_type: str) -> str:
    return ALERT_TYPE_NAMES.get(alert_type, alert_type)


def translate_severity(severity: str) -> str:
    return SEVERITY_LABELS.get(severity, severity)


def translate_rule_path(rule_path: str) -> str:
    return RULE_PATH_NAMES.get(rule_path, rule_path)


def translate_decision_type(decision_type: str) -> str:
    return DECISION_TYPE_NAMES.get(decision_type.upper(), decision_type)


def translate_stop_type(stop_type: str) -> str:
    return STOP_TYPE_NAMES.get(stop_type.upper(), stop_type)


def translate_risk_tolerance(risk: str) -> str:
    return RISK_TOLERANCE_NAMES.get(risk, risk)


def translate_causal_layer(layer: str) -> str:
    return CAUSAL_LAYER_NAMES.get(layer, layer)


def translate_score(score: float) -> str:
    for (lo, hi), label in SCORE_LABELS.items():
        if lo <= score <= hi:
            return label
    return f"评分 {score:.1f}"


def translate_error_code(code: str) -> str:
    return ERROR_CODE_NAMES.get(code.upper(), code)


def fmt_pct(value: float, decimals: int = 1) -> str:
    """Format a decimal fraction as a percentage string."""
    return f"{value * 100:.{decimals}f}%"


def fmt_cny(value: float) -> str:
    """Format a CNY amount."""
    if abs(value) >= 1_000_000:
        return f"¥{value / 1_000_000:.2f}M"
    if abs(value) >= 10_000:
        return f"¥{value / 10_000:.1f}万"
    return f"¥{value:,.0f}"


# ── Alert → human message ─────────────────────────────────────────────────────

# Why each rule protects the investor (shown in human output)
_RULE_RATIONALE: dict[str, str] = {
    "single_stock_drawdown_l1": "单股持续下跌时早发现，避免小亏变大亏",
    "single_stock_drawdown_l2": "回撤超 20% 说明原始 thesis 可能已失效，需要重新评估",
    "single_stock_drawdown_l3": "回撤超 30% 是系统性风险信号，必须强制止损保护本金",
    "single_stock_position":    "单股集中度过高会让一只股票的风险拖垮整个组合",
    "account_drawdown_l1":      "账户整体回撤预警，提醒你检查是否有系统性问题",
    "account_drawdown_l2":      "账户回撤超 15% 说明整体策略需要调整，暂停新建仓",
    "account_drawdown_l3":      "账户回撤超 20% 是极端风险信号，必须降仓保护本金",
    "theme_concentration":      "同一主题的股票高度相关，看似分散实则集中，一个事件可以同时打击所有持仓",
    "etf_drawdown":             "ETF 大幅回撤可能意味着市场系统性风险，需要关注",
    "etf_drift":                "ETF 偏离目标配置会改变你的风险敞口，需要再平衡",
    "stop_rule_stop_loss":      "止损规则是你事先设定的纪律，触发时必须执行",
    "stop_rule_take_profit":    "止盈规则是你事先设定的目标，触发时锁定收益",
    "stop_rule_hard_dd":        "硬性止损是最后防线，触发时必须无条件执行",
}

# Action directive for each alert type
_RULE_ACTION: dict[str, str] = {
    "single_stock_drawdown_l1": "关注该持仓，检查是否有基本面变化，暂不需要操作",
    "single_stock_drawdown_l2": "在 7 天内更新该股票的 thesis 评分，若论点失效则触发减仓决策",
    "single_stock_drawdown_l3": "立即运行 `inv trade decision CODE --type REDUCE`，启动减仓流程",
    "single_stock_position":    "运行 `inv trade decision CODE --type REDUCE`，将仓位降至 25% 以下",
    "account_drawdown_l1":      "检查各持仓基本面，暂不需要操作，保持观察",
    "account_drawdown_l2":      "暂停所有新建仓计划，重新评估组合整体策略",
    "account_drawdown_l3":      "立即启动降仓计划，将 C 档仓位降至 50% 以下",
    "theme_concentration":      "考虑减持同一主题中表现最弱的持仓，增加行业分散度",
    "etf_drawdown":             "检查 ETF 对应指数的基本面，若是系统性风险则考虑减仓",
    "etf_drift":                "在下次再平衡窗口（季度）调整 ETF 配置至目标比例",
    "stop_rule_stop_loss":      "按止损规则执行卖出，运行 `inv trade log CODE -s N -p PRICE --side SELL`",
    "stop_rule_take_profit":    "按止盈规则执行卖出，锁定收益",
    "stop_rule_grid_sell":      "按网格规则执行卖出",
    "stop_rule_grid_buy":       "按网格规则执行买入",
    "stop_rule_hard_dd":        "立即执行硬性止损，无条件卖出",
}


@dataclass
class HumanAlert:
    severity_label: str
    type_label: str
    stock_name: str
    stock_code: str
    raw_message: str
    rationale: str
    action: str

    def to_text(self) -> str:
        lines = [
            f"{self.severity_label} **{self.type_label}**",
        ]
        if self.stock_name:
            lines.append(f"持仓：{self.stock_name}（{self.stock_code}）")
        lines.append(f"情况：{self.raw_message}")
        lines.append(f"为什么这条规则保护你：{self.rationale}")
        lines.append(f"所以你该做什么：{self.action}")
        return "\n".join(lines)


def translate_alert(alert: dict) -> HumanAlert:
    """Convert a raw alert dict to a HumanAlert with full human-readable content."""
    atype = alert.get("alert_type") or alert.get("type", "")
    return HumanAlert(
        severity_label=translate_severity(alert.get("severity", "info")),
        type_label=translate_alert_type(atype),
        stock_name=alert.get("name", ""),
        stock_code=alert.get("code", ""),
        raw_message=alert.get("message", ""),
        rationale=_RULE_RATIONALE.get(atype, "遵守投资纪律，保护本金"),
        action=_RULE_ACTION.get(atype, "查看详情后决定是否需要操作"),
    )


def translate_alerts(alerts: list[dict]) -> list[HumanAlert]:
    return [translate_alert(a) for a in alerts]


# ── Deviation → human message ─────────────────────────────────────────────────

def translate_deviation(
    tranche: str,
    actual_ratio: float,
    target_ratio: float,
    actual_amount: float,
    target_amount: float,
) -> str:
    """Describe the deviation of a tranche from its target in plain language."""
    diff = actual_ratio - target_ratio
    diff_amount = actual_amount - target_amount
    tranche_names = {"A": "A 档（生活保障金）", "B": "B 档（核心 ETF）", "C": "C 档（主动选股）"}
    name = tranche_names.get(tranche, tranche)

    if abs(diff) < 0.02:
        return f"{name}：{fmt_pct(actual_ratio)}，接近目标 {fmt_pct(target_ratio)}，无需调整"

    direction = "超配" if diff > 0 else "低配"
    return (
        f"{name}：实际 {fmt_pct(actual_ratio)}，目标 {fmt_pct(target_ratio)}，"
        f"{direction} {fmt_pct(abs(diff))}（{fmt_cny(abs(diff_amount))}）"
    )
