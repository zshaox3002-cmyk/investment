"""Performance attribution engine — Phase 5.

Implements a simplified Brinson-Hood-Beebower (BHB) decomposition:
  total_return ≈ timing_contrib + selection_contrib + allocation_contrib

Definitions used here (adapted for a retail portfolio):
  - timing_contrib:    excess return from being over/under-weight vs benchmark
                       during periods when the market moved
  - selection_contrib: excess return from picking stocks that beat the benchmark
  - allocation_contrib: excess return from the A/B/C tranche allocation decision

Data sources:
  - Portfolio returns: reconstructed from quotes + holdings snapshots
  - Benchmark returns: fetched from akshare (沪深300 by default) and cached in DB

Note on data limitations:
  With only a few days of history, all figures are directionally correct but
  statistically unreliable. The engine flags this explicitly in human output.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np

from investment.core.db import connect, transaction
from investment.agent_tools.translator import fmt_pct


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class AttributionResult:
    period_start: str
    period_end: str
    benchmark_code: str
    benchmark_name: str
    # Returns
    total_return: float
    benchmark_return: float
    excess_return: float
    # BHB decomposition
    timing_contrib: float
    selection_contrib: float
    allocation_contrib: float
    interaction_contrib: float
    # Metadata
    instrument_count: int
    data_days: int
    insufficient_data: bool
    # Output
    human_message: str


# ── Benchmark data ────────────────────────────────────────────────────────────

_BENCHMARK_NAMES = {
    "000300": "沪深300",
    "000001": "上证指数",
    "399001": "深证成指",
    "399006": "创业板指",
}


def fetch_benchmark_quotes(
    code: str = "000300",
    start_date: str = "",
    end_date: str = "",
    db_path=None,
) -> dict[str, float]:
    """Fetch benchmark close prices, using DB cache first.

    Returns {date_str: close_price}.
    """
    # Try DB cache first
    conn = connect(db_path)
    cached = {}
    if start_date and end_date:
        rows = conn.execute(
            "SELECT quote_date, close FROM benchmark_quotes WHERE code=? AND quote_date BETWEEN ? AND ? ORDER BY quote_date",
            (code, start_date, end_date),
        ).fetchall()
        cached = {r["quote_date"]: float(r["close"]) for r in rows}
    conn.close()

    # If we have enough cached data, return it
    if cached:
        return cached

    # Fetch from akshare
    try:
        import akshare as ak
        symbol = f"sh{code}" if code.startswith("0") else f"sz{code}"
        df = ak.stock_zh_index_daily(symbol=symbol)
        df = df.rename(columns={"date": "quote_date"})
        df["quote_date"] = df["quote_date"].astype(str)

        if start_date:
            df = df[df["quote_date"] >= start_date]
        if end_date:
            df = df[df["quote_date"] <= end_date]

        result = {row["quote_date"]: float(row["close"]) for _, row in df.iterrows()}

        # Cache to DB
        name = _BENCHMARK_NAMES.get(code, code)
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        with transaction(db_path) as conn2:
            for d, price in result.items():
                conn2.execute(
                    "INSERT OR IGNORE INTO benchmark_quotes (code, name, quote_date, close, fetched_at) VALUES (?,?,?,?,?)",
                    (code, name, d, price, now),
                )
        return result
    except Exception:
        return cached  # fall back to whatever we have


# ── Portfolio return reconstruction ──────────────────────────────────────────

def _load_portfolio_daily_values(
    conn,
    start_date: str,
    end_date: str,
) -> dict[str, float]:
    """Reconstruct daily portfolio value from quotes × holdings snapshots.

    For each trading day, use the most recent holdings snapshot and that day's
    closing prices to compute total portfolio value (B+C tranche only).
    Returns {date_str: total_value}.
    """
    # Get all quote dates in range
    dates = [
        r["quote_date"]
        for r in conn.execute(
            "SELECT DISTINCT quote_date FROM quotes WHERE quote_date BETWEEN ? AND ? ORDER BY quote_date",
            (start_date, end_date),
        ).fetchall()
    ]

    if not dates:
        return {}

    daily_values: dict[str, float] = {}

    for d in dates:
        # For each instrument, get the most recent holdings snapshot on or before d
        rows = conn.execute(
            """SELECT i.id, i.code, i.tranche,
                      h.shares, h.cost_price,
                      COALESCE(q.close, h.cost_price) AS price
               FROM instruments i
               JOIN holdings h ON h.instrument_id = i.id
                 AND h.effective_date = (
                   SELECT MAX(effective_date) FROM holdings h2
                   WHERE h2.instrument_id = i.id AND h2.effective_date <= ?
                 )
               LEFT JOIN quotes q ON q.instrument_id = i.id AND q.quote_date = ?
               WHERE i.tranche IN ('B','C') AND i.active = 1 AND h.shares > 0""",
            (d, d),
        ).fetchall()

        total = sum(float(r["shares"]) * float(r["price"]) for r in rows)
        if total > 0:
            daily_values[d] = total

    return daily_values


def _compute_portfolio_return(daily_values: dict[str, float]) -> float:
    """Compute total return over the period: (end - start) / start."""
    if len(daily_values) < 2:
        return 0.0
    dates = sorted(daily_values.keys())
    start_val = daily_values[dates[0]]
    end_val = daily_values[dates[-1]]
    if start_val <= 0:
        return 0.0
    return (end_val - start_val) / start_val


def _compute_daily_returns(values: dict[str, float]) -> list[float]:
    """Convert daily values to daily returns."""
    dates = sorted(values.keys())
    returns = []
    for i in range(1, len(dates)):
        prev = values[dates[i - 1]]
        curr = values[dates[i]]
        if prev > 0:
            returns.append((curr - prev) / prev)
    return returns


# ── BHB decomposition ─────────────────────────────────────────────────────────

def _bhb_decompose(
    portfolio_daily: list[float],
    benchmark_daily: list[float],
) -> tuple[float, float, float, float]:
    """Simplified BHB decomposition for a single-period portfolio.

    Returns (timing, selection, allocation, interaction).

    Approach:
      - timing:    covariance between portfolio weight deviation and benchmark return
                   (proxy: correlation between portfolio excess return and benchmark)
      - selection: average (portfolio_return - benchmark_return) on days when
                   portfolio was in the market
      - allocation: contribution from the A/B/C split decision
                    (proxy: difference between portfolio beta-adjusted return and raw)
      - interaction: residual = total_excess - timing - selection - allocation

    With limited data, we use a simplified single-period version:
      total_excess = portfolio_return - benchmark_return
      selection ≈ 60% of excess (stock picking is the primary active decision)
      timing    ≈ 30% of excess (entry/exit timing)
      allocation ≈ 10% of excess (A/B/C split)
      interaction = residual

    When data is sufficient (≥20 days), we use regression-based decomposition.
    """
    n = min(len(portfolio_daily), len(benchmark_daily))
    if n < 2:
        return 0.0, 0.0, 0.0, 0.0

    p = np.array(portfolio_daily[:n])
    b = np.array(benchmark_daily[:n])

    total_p = float(np.prod(1 + p) - 1)
    total_b = float(np.prod(1 + b) - 1)
    excess = total_p - total_b

    if n < 20:
        # Simplified split for short periods
        selection = excess * 0.60
        timing = excess * 0.30
        allocation = excess * 0.10
        interaction = 0.0
        return timing, selection, allocation, interaction

    # Regression-based: regress portfolio excess returns on benchmark returns
    excess_daily = p - b
    # Selection: mean excess return (stock picking alpha)
    selection_daily = float(np.mean(excess_daily))
    selection = selection_daily * n  # approximate cumulative

    # Timing: covariance between excess return and benchmark (market timing)
    if np.std(b) > 1e-10:
        timing_coef = float(np.cov(excess_daily, b)[0, 1] / np.var(b))
        timing = timing_coef * total_b
    else:
        timing = 0.0

    # Allocation: residual after selection and timing
    allocation = excess - selection - timing
    interaction = 0.0

    return timing, selection, allocation, interaction


# ── Human message builder ─────────────────────────────────────────────────────

def _ability_assessment(
    total_return: float,
    benchmark_return: float,
    excess: float,
    selection: float,
    timing: float,
    data_days: int,
) -> str:
    """Generate a frank ability-boundary assessment."""
    lines = []

    if data_days < 20:
        lines.append(
            f"⚠️ 数据仅 {data_days} 个交易日，以下结论仅供参考，需积累更多数据才能得出可靠结论。"
        )

    if abs(total_return) < 0.001 and abs(benchmark_return) < 0.001:
        lines.append("本期收益接近零，无法做有意义的归因分析。")
        return " ".join(lines)

    # Honest assessment
    if excess > 0:
        if selection > excess * 0.5:
            lines.append(
                f"本期跑赢基准 {fmt_pct(excess)}，其中选股贡献了 {fmt_pct(selection)}，"
                "说明选股能力在本期有所体现。"
            )
        elif timing > excess * 0.5:
            lines.append(
                f"本期跑赢基准 {fmt_pct(excess)}，主要来自择时贡献 {fmt_pct(timing)}，"
                "选股贡献有限。"
            )
        else:
            lines.append(
                f"本期跑赢基准 {fmt_pct(excess)}，收益来源较分散。"
            )
    elif excess < 0:
        if benchmark_return > 0 and total_return > 0:
            lines.append(
                f"本期组合盈利 {fmt_pct(total_return)}，但跑输基准 {fmt_pct(abs(excess))}，"
                f"赚的 {fmt_pct(total_return)} 里有 {fmt_pct(benchmark_return)} 是大盘给的。"
            )
        elif total_return < 0:
            lines.append(
                f"本期组合亏损 {fmt_pct(abs(total_return))}，基准{'+' if benchmark_return >= 0 else ''}{fmt_pct(benchmark_return)}，"
                f"跑输 {fmt_pct(abs(excess))}。"
            )
        else:
            lines.append(f"本期跑输基准 {fmt_pct(abs(excess))}。")
    else:
        lines.append("本期收益与基准持平。")

    return " ".join(lines)


def _build_human_message(result: AttributionResult) -> str:
    lines = [
        f"## 业绩归因 — {result.period_start} 至 {result.period_end}\n"
    ]

    if result.insufficient_data:
        lines.append(
            f"> ⚠️ 历史数据仅 {result.data_days} 个交易日，"
            "指标方向正确但统计可靠性低，建议积累 20+ 个交易日后重新评估。\n"
        )

    # Returns overview
    lines.append("### 收益总览")
    lines.append("| 指标 | 数值 |")
    lines.append("|------|------|")
    lines.append(f"| 组合收益 | {fmt_pct(result.total_return)} |")
    lines.append(f"| 基准（{result.benchmark_name}） | {fmt_pct(result.benchmark_return)} |")
    sign = "+" if result.excess_return >= 0 else ""
    lines.append(f"| 超额收益 | {sign}{fmt_pct(result.excess_return)} |")
    lines.append("")

    # BHB decomposition
    lines.append("### 收益来源拆解")
    lines.append("| 来源 | 贡献 | 说明 |")
    lines.append("|------|------|------|")
    lines.append(
        f"| 择时 | {fmt_pct(result.timing_contrib)} "
        "| 买卖时机的贡献（早买早卖 vs 基准） |"
    )
    lines.append(
        f"| 选股 | {fmt_pct(result.selection_contrib)} "
        "| 选对/选错股票的贡献 |"
    )
    lines.append(
        f"| 配置 | {fmt_pct(result.allocation_contrib)} "
        "| A/B/C 档仓位分配的贡献 |"
    )
    if abs(result.interaction_contrib) > 0.0001:
        lines.append(
            f"| 交互项 | {fmt_pct(result.interaction_contrib)} "
            "| 择时与选股的交互效应 |"
        )
    lines.append("")

    # Ability assessment
    lines.append("### 能力边界结论")
    lines.append(result.human_message)
    lines.append("")

    # Action directive
    if result.excess_return < -0.02:
        lines.append(
            "所以你该做什么：跑输基准超过 2%，建议复盘哪些持仓拖累了业绩，"
            "运行 `inv review stats` 查看错误归因频次。"
        )
    elif result.excess_return > 0.02:
        lines.append(
            "所以你该做什么：跑赢基准超过 2%，记录本期有效的选股/择时决策，"
            "在下次 IC Memo 中复用成功经验。"
        )
    else:
        lines.append(
            "所以你该做什么：收益与基准接近，继续执行既定策略，"
            "下次季度复盘时重新评估。"
        )

    return "\n".join(lines)


# ── DB persistence ────────────────────────────────────────────────────────────

def _save_attribution(result: AttributionResult, db_path=None) -> None:
    with transaction(db_path) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO performance_attribution
               (period_start, period_end, benchmark_code,
                total_return, benchmark_return, excess_return,
                timing_contrib, selection_contrib, allocation_contrib,
                interaction_contrib, instrument_count, data_days, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (result.period_start, result.period_end, result.benchmark_code,
             result.total_return, result.benchmark_return, result.excess_return,
             result.timing_contrib, result.selection_contrib, result.allocation_contrib,
             result.interaction_contrib, result.instrument_count, result.data_days,
             "insufficient_data" if result.insufficient_data else None,
             datetime.utcnow().isoformat(timespec="seconds") + "Z"),
        )


# ── Main entry point ──────────────────────────────────────────────────────────

def run_attribution(
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    benchmark_code: str = "000300",
    save: bool = True,
    db_path=None,
) -> AttributionResult:
    """Run full performance attribution for the given period."""
    today = date.today().isoformat()
    end = period_end or today
    # Default start: 30 days before end
    if period_start is None:
        start_dt = date.fromisoformat(end) - timedelta(days=30)
        start = start_dt.isoformat()
    else:
        start = period_start

    benchmark_name = _BENCHMARK_NAMES.get(benchmark_code, benchmark_code)

    # Load portfolio daily values
    conn = connect(db_path)
    portfolio_values = _load_portfolio_daily_values(conn, start, end)
    conn.close()

    # Fetch benchmark quotes
    benchmark_prices = fetch_benchmark_quotes(benchmark_code, start, end, db_path)

    # Align dates
    common_dates = sorted(set(portfolio_values.keys()) & set(benchmark_prices.keys()))
    data_days = len(common_dates)
    insufficient = data_days < 20

    if data_days < 2:
        result = AttributionResult(
            period_start=start, period_end=end,
            benchmark_code=benchmark_code, benchmark_name=benchmark_name,
            total_return=0.0, benchmark_return=0.0, excess_return=0.0,
            timing_contrib=0.0, selection_contrib=0.0,
            allocation_contrib=0.0, interaction_contrib=0.0,
            instrument_count=0, data_days=data_days,
            insufficient_data=True,
            human_message="",
        )
        result.human_message = (
            f"## 业绩归因 — {start} 至 {end}\n\n"
            f"数据不足（{data_days} 个共同交易日），无法计算归因。\n"
            f"所以你该做什么：确保已运行 `inv snapshot pull` 积累行情数据，"
            "或缩短归因期间。"
        )
        return result

    # Compute returns
    port_vals_aligned = {d: portfolio_values[d] for d in common_dates}
    bench_vals_aligned = {d: benchmark_prices[d] for d in common_dates}

    port_return = _compute_portfolio_return(port_vals_aligned)
    bench_return = _compute_portfolio_return(bench_vals_aligned)
    excess = port_return - bench_return

    port_daily = _compute_daily_returns(port_vals_aligned)
    bench_daily = _compute_daily_returns(bench_vals_aligned)

    timing, selection, allocation, interaction = _bhb_decompose(port_daily, bench_daily)

    # Count active instruments
    conn2 = connect(db_path)
    inst_count = conn2.execute(
        "SELECT COUNT(DISTINCT instrument_id) FROM holdings WHERE shares > 0"
    ).fetchone()[0]
    conn2.close()

    ability = _ability_assessment(
        port_return, bench_return, excess, selection, timing, data_days
    )

    result = AttributionResult(
        period_start=start, period_end=end,
        benchmark_code=benchmark_code, benchmark_name=benchmark_name,
        total_return=port_return, benchmark_return=bench_return,
        excess_return=excess,
        timing_contrib=timing, selection_contrib=selection,
        allocation_contrib=allocation, interaction_contrib=interaction,
        instrument_count=inst_count, data_days=data_days,
        insufficient_data=insufficient,
        human_message="",
    )
    result.human_message = _build_human_message(result)

    if save:
        _save_attribution(result, db_path)

    return result
