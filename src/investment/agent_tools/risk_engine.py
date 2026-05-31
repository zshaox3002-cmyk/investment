"""Risk engine — Phase 4.

Computes portfolio risk metrics from historical price data:
  - Annualised volatility
  - Maximum drawdown + duration
  - 95%/99% VaR (historical simulation)
  - Pairwise correlation matrix
  - Per-instrument risk contribution
  - Pseudo-diversification detection

All calculations use pure numpy (no scipy dependency).
Results are persisted to risk_metrics / correlation_matrix / risk_contribution tables.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import numpy as np

from investment.core.db import connect, transaction
from investment.agent_tools.translator import fmt_cny, fmt_pct


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class InstrumentReturns:
    instrument_id: int
    code: str
    name: str
    tranche: str
    weight: float           # portfolio weight by market value
    returns: np.ndarray     # daily log returns, shape (T,)
    vol_annual: float = 0.0 # annualised vol, filled after calculation


@dataclass
class PseudoDiversification:
    detected: bool
    description: str
    concentrated_theme: str
    top_contributor_code: str
    top_contributor_pct: float  # % of total portfolio risk


@dataclass
class RiskReport:
    calc_date: str
    lookback_days: int
    instrument_count: int
    # Portfolio-level
    portfolio_vol: float        # annualised, decimal
    max_drawdown: float         # negative decimal
    dd_duration_days: int
    var_95: float               # 1-day, negative decimal
    var_99: float
    sharpe_ratio: float
    # Per-instrument
    risk_contributions: list[dict]  # {code, name, weight, vol, risk_contrib_pct}
    # Correlation
    high_correlations: list[dict]   # {code_a, name_a, code_b, name_b, corr}
    # Pseudo-diversification
    pseudo_div: PseudoDiversification
    # Human output
    human_message: str
    # Metadata
    data_days: int              # actual number of trading days used
    insufficient_data: bool = False


# ── Price data loading ────────────────────────────────────────────────────────

def _load_price_series(conn, lookback_days: int) -> list[InstrumentReturns]:
    """Load close prices for all B+C holdings, compute log returns."""
    # Get holdings with market values
    holdings = conn.execute(
        """SELECT i.id, i.code, i.name, i.tranche, i.theme, i.industry,
                  h.shares, h.cost_price,
                  COALESCE(q_latest.close, h.cost_price) AS latest_price
           FROM holdings h
           JOIN instruments i ON i.id = h.instrument_id
           LEFT JOIN (
               SELECT instrument_id, close
               FROM quotes
               WHERE (instrument_id, quote_date) IN (
                   SELECT instrument_id, MAX(quote_date) FROM quotes GROUP BY instrument_id
               )
           ) q_latest ON q_latest.instrument_id = i.id
           WHERE i.tranche IN ('B','C') AND i.active = 1
             AND h.shares > 0
             AND h.effective_date = (
               SELECT MAX(effective_date) FROM holdings h2
               WHERE h2.instrument_id = h.instrument_id
             )"""
    ).fetchall()

    if not holdings:
        return []

    # Total portfolio value for weights
    total_value = sum(
        float(h["shares"]) * float(h["latest_price"]) for h in holdings
    )
    if total_value <= 0:
        return []

    result: list[InstrumentReturns] = []
    for h in holdings:
        iid = h["id"]
        market_value = float(h["shares"]) * float(h["latest_price"])
        weight = market_value / total_value

        # Load price history
        rows = conn.execute(
            """SELECT quote_date, close FROM quotes
               WHERE instrument_id = ?
               ORDER BY quote_date DESC
               LIMIT ?""",
            (iid, lookback_days + 1),
        ).fetchall()

        if len(rows) < 3:  # need at least 2 returns (3 prices)
            continue

        prices = np.array([float(r["close"]) for r in reversed(rows)])
        # Log returns: ln(P_t / P_{t-1})
        log_returns = np.diff(np.log(prices))

        result.append(InstrumentReturns(
            instrument_id=iid,
            code=h["code"],
            name=h["name"],
            tranche=h["tranche"],
            weight=weight,
            returns=log_returns,
        ))

    return result


# ── Core calculations ─────────────────────────────────────────────────────────

_TRADING_DAYS_PER_YEAR = 252


def _align_returns(instruments: list[InstrumentReturns]) -> tuple[np.ndarray, list[InstrumentReturns]]:
    """Align all return series to the same length (min across instruments)."""
    if not instruments:
        return np.empty((0, 0)), []
    min_len = min(len(i.returns) for i in instruments)
    aligned = []
    for inst in instruments:
        inst_copy = InstrumentReturns(
            instrument_id=inst.instrument_id,
            code=inst.code, name=inst.name,
            tranche=inst.tranche, weight=inst.weight,
            returns=inst.returns[-min_len:],
        )
        aligned.append(inst_copy)
    # Matrix: shape (T, N)
    matrix = np.column_stack([i.returns for i in aligned])
    return matrix, aligned


def calc_portfolio_returns(matrix: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Compute weighted portfolio daily returns."""
    return matrix @ weights


def calc_annualised_vol(returns: np.ndarray) -> float:
    """Annualised volatility from daily log returns."""
    if len(returns) < 2:
        return 0.0
    return float(np.std(returns, ddof=1) * np.sqrt(_TRADING_DAYS_PER_YEAR))


def calc_max_drawdown(returns: np.ndarray) -> tuple[float, int]:
    """Return (max_drawdown, duration_days). Drawdown is negative."""
    if len(returns) < 2:
        return 0.0, 0
    cum = np.exp(np.cumsum(returns))  # cumulative wealth index
    peak = np.maximum.accumulate(cum)
    drawdown = (cum - peak) / peak
    max_dd = float(np.min(drawdown))

    # Duration: longest consecutive drawdown period
    in_dd = drawdown < -1e-6
    max_dur = 0
    cur_dur = 0
    for d in in_dd:
        if d:
            cur_dur += 1
            max_dur = max(max_dur, cur_dur)
        else:
            cur_dur = 0

    return max_dd, max_dur


def calc_var(returns: np.ndarray, confidence: float = 0.95) -> float:
    """Historical simulation VaR. Returns negative value."""
    if len(returns) < 10:
        return 0.0
    return float(np.percentile(returns, (1 - confidence) * 100))


def calc_sharpe(returns: np.ndarray, risk_free_daily: float = 0.0) -> float:
    """Annualised Sharpe ratio (risk-free default = 0)."""
    if len(returns) < 2:
        return 0.0
    excess = returns - risk_free_daily
    std = np.std(excess, ddof=1)
    if std < 1e-10:
        return 0.0
    return float(np.mean(excess) / std * np.sqrt(_TRADING_DAYS_PER_YEAR))


def calc_correlation_matrix(matrix: np.ndarray) -> np.ndarray:
    """Pearson correlation matrix. Shape (N, N)."""
    if matrix.shape[1] < 2:
        return np.array([[1.0]])
    return np.corrcoef(matrix.T)


def calc_risk_contributions(
    matrix: np.ndarray,
    weights: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute per-instrument risk contribution percentages.

    Returns (risk_contrib_pct, marginal_contrib).
    risk_contrib_pct sums to 1.0.
    """
    n = len(weights)
    if n == 0 or matrix.shape[0] < 2:
        return np.zeros(n), np.zeros(n)

    cov = np.cov(matrix.T, ddof=1)  # (N, N) covariance matrix
    if cov.ndim == 0:
        cov = np.array([[float(cov)]])

    port_var = float(weights @ cov @ weights)
    if port_var < 1e-12:
        return np.ones(n) / n, np.zeros(n)

    # Marginal contribution: ∂σ/∂w_i = (Σw)_i / σ
    port_vol = np.sqrt(port_var)
    marginal = (cov @ weights) / port_vol
    # Component contribution: w_i * marginal_i
    component = weights * marginal
    contrib_pct = component / port_vol  # normalised to sum = 1

    return contrib_pct, marginal


# ── Pseudo-diversification detection ─────────────────────────────────────────

_THEME_GROUPS: dict[str, list[str]] = {
    "新能源/电力链": ["新能源汽车", "光伏/太阳能", "储能", "电力设备", "新能源"],
    "科技/互联网":   ["互联网", "半导体", "消费电子", "软件", "人工智能", "科技"],
    "消费":          ["白酒", "食品饮料", "消费", "零售"],
    "金融":          ["银行", "保险", "证券", "金融"],
}


def detect_pseudo_diversification(
    instruments: list[InstrumentReturns],
    corr_matrix: np.ndarray,
    risk_contrib_pct: np.ndarray,
    instrument_themes: dict[int, str],  # instrument_id → theme/industry
) -> PseudoDiversification:
    """Detect if the portfolio is pseudo-diversified (many stocks, same theme)."""
    n = len(instruments)
    if n < 2:
        return PseudoDiversification(
            detected=False, description="持仓数量不足，无法评估分散度",
            concentrated_theme="", top_contributor_code="",
            top_contributor_pct=0.0,
        )

    # Find top risk contributor
    top_idx = int(np.argmax(risk_contrib_pct))
    top_contrib = float(risk_contrib_pct[top_idx])
    top_code = instruments[top_idx].code

    # Check theme concentration
    theme_weights: dict[str, float] = {}
    for inst in instruments:
        theme = instrument_themes.get(inst.instrument_id, "")
        for group_name, keywords in _THEME_GROUPS.items():
            if any(kw in theme for kw in keywords):
                theme_weights[group_name] = theme_weights.get(group_name, 0) + inst.weight
                break

    concentrated_theme = ""
    max_theme_weight = 0.0
    for theme, w in theme_weights.items():
        if w > max_theme_weight:
            max_theme_weight = w
            concentrated_theme = theme

    # High average correlation check
    upper_tri = corr_matrix[np.triu_indices(n, k=1)]
    avg_corr = float(np.mean(upper_tri)) if len(upper_tri) > 0 else 0.0

    # Pseudo-diversification: many stocks but high theme concentration or high avg corr
    pseudo = (
        (n >= 3 and max_theme_weight > 0.5) or
        (n >= 3 and avg_corr > 0.7) or
        (top_contrib > 0.5)
    )

    if pseudo:
        parts = []
        if max_theme_weight > 0.5:
            parts.append(
                f"账面 {n} 只持仓，但 {fmt_pct(max_theme_weight)} 集中在「{concentrated_theme}」主题"
            )
        if avg_corr > 0.7:
            parts.append(f"持仓平均相关性 {avg_corr:.2f}，高度同向波动")
        if top_contrib > 0.5:
            parts.append(f"{top_code} 贡献了组合 {fmt_pct(top_contrib)} 的风险")
        description = "；".join(parts) if parts else "持仓高度集中"
    else:
        description = f"持仓分散度良好（平均相关性 {avg_corr:.2f}，主题集中度 {fmt_pct(max_theme_weight)}）"

    return PseudoDiversification(
        detected=pseudo,
        description=description,
        concentrated_theme=concentrated_theme,
        top_contributor_code=top_code,
        top_contributor_pct=top_contrib,
    )


# ── DB persistence ────────────────────────────────────────────────────────────

def _save_risk_metrics(conn, report: RiskReport) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO risk_metrics
           (calc_date, lookback_days, portfolio_vol, max_drawdown, dd_duration_days,
            var_95, var_99, sharpe_ratio, instrument_count, notes, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (report.calc_date, report.lookback_days,
         report.portfolio_vol, report.max_drawdown, report.dd_duration_days,
         report.var_95, report.var_99, report.sharpe_ratio,
         report.instrument_count,
         "insufficient_data" if report.insufficient_data else None,
         datetime.utcnow().isoformat(timespec="seconds") + "Z"),
    )


def _save_correlations(conn, report: RiskReport, instruments: list[InstrumentReturns],
                       corr_matrix: np.ndarray) -> None:
    today = report.calc_date
    for i, inst_a in enumerate(instruments):
        for j, inst_b in enumerate(instruments):
            if j <= i:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO correlation_matrix
                   (calc_date, instrument_id_a, instrument_id_b, corr_value,
                    lookback_days, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (today, inst_a.instrument_id, inst_b.instrument_id,
                 float(corr_matrix[i, j]), report.lookback_days,
                 datetime.utcnow().isoformat(timespec="seconds") + "Z"),
            )


def _save_risk_contributions(conn, report: RiskReport) -> None:
    today = report.calc_date
    for rc in report.risk_contributions:
        conn.execute(
            """INSERT OR REPLACE INTO risk_contribution
               (calc_date, instrument_id, weight, vol, risk_contrib_pct,
                marginal_contrib, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (today, rc["instrument_id"], rc["weight"], rc.get("vol"),
             rc["risk_contrib_pct"], rc.get("marginal_contrib"),
             datetime.utcnow().isoformat(timespec="seconds") + "Z"),
        )


# ── Human message builder ─────────────────────────────────────────────────────

def _vol_label(vol: float) -> str:
    if vol < 0.12:
        return "低"
    if vol < 0.20:
        return "中"
    if vol < 0.30:
        return "较高"
    return "高"


def _dd_label(dd: float) -> str:
    if dd > -0.10:
        return "可接受"
    if dd > -0.20:
        return "需关注"
    return "⚠️ 超警戒线"


def _build_human_message(report: RiskReport) -> str:
    lines = [f"## 组合风险报告 — {report.calc_date}\n"]

    if report.insufficient_data:
        lines.append(
            f"> ⚠️ 历史数据不足（仅 {report.data_days} 个交易日），"
            "以下指标仅供参考，建议积累更多数据后重新评估。\n"
        )

    # Core metrics table
    lines.append("### 核心风险指标")
    lines.append("| 指标 | 数值 | 评价 |")
    lines.append("|------|------|------|")
    lines.append(
        f"| 年化波动率 | {fmt_pct(report.portfolio_vol)} "
        f"| {_vol_label(report.portfolio_vol)} |"
    )
    lines.append(
        f"| 最大回撤 | {fmt_pct(report.max_drawdown)} "
        f"| {_dd_label(report.max_drawdown)} |"
    )
    lines.append(f"| 回撤持续 | {report.dd_duration_days} 个交易日 | — |")
    lines.append(f"| 95% VaR（单日） | {fmt_pct(report.var_95)} | 单日最大损失参考 |")
    lines.append(f"| Sharpe 比率 | {report.sharpe_ratio:.2f} | {'良好' if report.sharpe_ratio > 1 else '一般' if report.sharpe_ratio > 0 else '较差'} |")
    lines.append("")

    # Pseudo-diversification
    pd = report.pseudo_div
    if pd.detected:
        lines.append("### ⚠️ 伪分散警示")
        lines.append(pd.description)
        lines.append(
            f"\n所以你该做什么：减持「{pd.concentrated_theme or pd.top_contributor_code}」"
            "相关持仓，增加低相关性资产，实现真正的风险分散。"
        )
    else:
        lines.append(f"### ✅ 分散度\n{pd.description}")
    lines.append("")

    # Risk contributions
    if report.risk_contributions:
        lines.append("### 各持仓风险贡献")
        lines.append("| 股票 | 仓位权重 | 风险贡献 |")
        lines.append("|------|---------|---------|")
        for rc in sorted(report.risk_contributions, key=lambda x: -x["risk_contrib_pct"]):
            lines.append(
                f"| {rc['name']}（{rc['code']}）"
                f"| {fmt_pct(rc['weight'])} "
                f"| {fmt_pct(rc['risk_contrib_pct'])} |"
            )
        lines.append("")

    # High correlations
    if report.high_correlations:
        lines.append("### 高相关持仓对（相关性 > 0.7）")
        for hc in report.high_correlations[:5]:
            lines.append(
                f"- {hc['name_a']} × {hc['name_b']}：相关性 {hc['corr']:.2f}"
            )
        lines.append(
            "\n所以你该做什么：高相关持仓在同一事件下会同涨同跌，"
            "考虑减持其中一只，换入低相关性标的。"
        )
        lines.append("")

    # Overall action
    if report.max_drawdown < -0.15 or report.portfolio_vol > 0.25:
        lines.append(
            "### 综合建议\n"
            "当前组合风险偏高。\n"
            "所以你该做什么：优先处理上方伪分散或高相关警示，"
            "考虑将 C 档仓位降至目标比例以下，增加 A 档现金缓冲。"
        )
    else:
        lines.append(
            "### 综合建议\n"
            "当前组合风险在可控范围内。\n"
            "所以你该做什么：维持现有配置，下次月度复盘时重新评估。"
        )

    return "\n".join(lines)


# ── Main entry point ──────────────────────────────────────────────────────────

def run_risk_engine(
    lookback_days: int = 60,
    calc_date: Optional[str] = None,
    db_path=None,
    save: bool = True,
) -> RiskReport:
    """Compute full risk report and optionally persist to DB."""
    conn = connect(db_path)
    today = calc_date or date.today().isoformat()

    instruments = _load_price_series(conn, lookback_days)

    # Load instrument themes for pseudo-div detection
    theme_map: dict[int, str] = {}
    if instruments:
        ids = tuple(i.instrument_id for i in instruments)
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id, COALESCE(theme, industry, '') AS theme FROM instruments WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        theme_map = {r["id"]: r["theme"] for r in rows}

    conn.close()

    # Handle insufficient data
    if len(instruments) < 2:
        report = RiskReport(
            calc_date=today, lookback_days=lookback_days,
            instrument_count=len(instruments),
            portfolio_vol=0.0, max_drawdown=0.0, dd_duration_days=0,
            var_95=0.0, var_99=0.0, sharpe_ratio=0.0,
            risk_contributions=[], high_correlations=[],
            pseudo_div=PseudoDiversification(
                detected=False,
                description="持仓数量不足，无法计算风险指标",
                concentrated_theme="", top_contributor_code="",
                top_contributor_pct=0.0,
            ),
            human_message="",
            data_days=0,
            insufficient_data=True,
        )
        report.human_message = _build_human_message(report)
        return report

    matrix, aligned = _align_returns(instruments)
    data_days = matrix.shape[0]
    insufficient = data_days < 20

    weights = np.array([i.weight for i in aligned])
    # Normalise weights (in case of rounding)
    weights = weights / weights.sum()

    port_returns = calc_portfolio_returns(matrix, weights)
    port_vol = calc_annualised_vol(port_returns)
    max_dd, dd_dur = calc_max_drawdown(port_returns)
    var_95 = calc_var(port_returns, 0.95)
    var_99 = calc_var(port_returns, 0.99)
    sharpe = calc_sharpe(port_returns)

    # Per-instrument vol
    for i, inst in enumerate(aligned):
        inst.vol_annual = calc_annualised_vol(matrix[:, i])

    corr_matrix = calc_correlation_matrix(matrix)
    risk_contrib_pct, marginal = calc_risk_contributions(matrix, weights)

    # Risk contributions list
    rc_list = []
    for i, inst in enumerate(aligned):
        rc_list.append({
            "instrument_id": inst.instrument_id,
            "code": inst.code,
            "name": inst.name,
            "weight": float(weights[i]),
            "vol": inst.vol_annual,
            "risk_contrib_pct": float(risk_contrib_pct[i]),
            "marginal_contrib": float(marginal[i]),
        })

    # High correlations
    high_corr = []
    n = len(aligned)
    for i in range(n):
        for j in range(i + 1, n):
            c = float(corr_matrix[i, j])
            if c > 0.7:
                high_corr.append({
                    "code_a": aligned[i].code, "name_a": aligned[i].name,
                    "code_b": aligned[j].code, "name_b": aligned[j].name,
                    "corr": c,
                })
    high_corr.sort(key=lambda x: -x["corr"])

    pseudo_div = detect_pseudo_diversification(
        aligned, corr_matrix, risk_contrib_pct, theme_map
    )

    report = RiskReport(
        calc_date=today, lookback_days=lookback_days,
        instrument_count=len(aligned),
        portfolio_vol=port_vol, max_drawdown=max_dd,
        dd_duration_days=dd_dur, var_95=var_95, var_99=var_99,
        sharpe_ratio=sharpe,
        risk_contributions=rc_list,
        high_correlations=high_corr,
        pseudo_div=pseudo_div,
        human_message="",
        data_days=data_days,
        insufficient_data=insufficient,
    )
    report.human_message = _build_human_message(report)

    if save:
        with transaction(db_path) as conn2:
            _save_risk_metrics(conn2, report)
            _save_correlations(conn2, report, aligned, corr_matrix)
            _save_risk_contributions(conn2, report)

    return report
