"""Goal engine — Phase 4.

Computes YTD progress against annual return target and writes to goal_progress.

Data sources (priority order):
  1. quotes × holdings → daily portfolio value series (B+C tranche)
  2. cash_balances → A-tranche value
  3. goals → target_annual_return
  4. benchmark_quotes (沪深300) → benchmark YTD
  5. risk_metrics → max_drawdown, portfolio_vol (for risk_budget_used)

All values are stored as decimals (0.10 = 10%).
When data is insufficient, marks result with insufficient_data=True and
fills available fields, leaving the rest None.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from investment.core.db import connect, transaction


@dataclass
class GoalProgressResult:
    progress_date: str

    # Returns
    target_annual_return: Optional[float]   # e.g. 0.10
    actual_ytd_return: Optional[float]
    target_ytd_return: Optional[float]      # linear interpolation
    progress_gap: Optional[float]           # actual - target_ytd (positive = ahead)
    required_return_remaining: Optional[float]

    # Risk
    max_drawdown: Optional[float]           # negative decimal
    risk_budget_used: Optional[float]       # 0–1

    # Benchmark
    benchmark_return_ytd: Optional[float]

    # Meta
    portfolio_value: Optional[float]
    insufficient_data: bool
    human_message: str


# ── Data loading helpers ──────────────────────────────────────────────────────

def _year_start(today: date) -> str:
    return today.replace(month=1, day=1).isoformat()


def _days_elapsed(today: date) -> int:
    return (today - today.replace(month=1, day=1)).days + 1


def _days_in_year(today: date) -> int:
    import calendar
    return 366 if calendar.isleap(today.year) else 365


def _load_portfolio_start_value(conn, year_start: str) -> Optional[float]:
    """Earliest B+C market value at or after year_start."""
    row = conn.execute(
        """SELECT SUM(h.shares * COALESCE(q.close, h.cost_price)) AS val
           FROM holdings h
           JOIN instruments i ON i.id = h.instrument_id
           LEFT JOIN quotes q
             ON q.instrument_id = i.id
             AND q.quote_date = (
               SELECT MIN(q2.quote_date) FROM quotes q2
               WHERE q2.instrument_id = i.id AND q2.quote_date >= ?
             )
           WHERE i.tranche IN ('B','C') AND i.active=1
             AND h.effective_date = (
               SELECT MIN(h2.effective_date) FROM holdings h2
               WHERE h2.instrument_id = h.instrument_id AND h2.effective_date >= ?
             )""",
        (year_start, year_start),
    ).fetchone()
    val = row["val"] if row else None
    return float(val) if val and val > 0 else None


def _load_portfolio_current_value(conn) -> Optional[float]:
    """Current B+C market value from v_portfolio_snapshot."""
    row = conn.execute(
        "SELECT SUM(market_value) AS val FROM v_portfolio_snapshot WHERE tranche IN ('B','C')"
    ).fetchone()
    val = row["val"] if row else None
    return float(val) if val and val > 0 else None


def _load_cash_value(conn) -> float:
    """A-tranche cash balance (latest per instrument)."""
    row = conn.execute(
        """SELECT SUM(cb.balance) AS total
           FROM cash_balances cb
           JOIN instruments i ON i.id = cb.instrument_id
           WHERE i.tranche = 'A'
             AND cb.effective_date = (
               SELECT MAX(effective_date) FROM cash_balances cb2
               WHERE cb2.instrument_id = cb.instrument_id
             )"""
    ).fetchone()
    return float(row["total"] or 0.0)


def _load_goal_target(conn) -> Optional[float]:
    """Latest active goal's target_annual_return (decimal)."""
    row = conn.execute(
        "SELECT target_annual_return FROM goals WHERE status='active' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row or row["target_annual_return"] is None:
        return None
    v = float(row["target_annual_return"])
    # goals table stores as percent (10.0) or decimal (0.10) — normalise to decimal
    return v / 100.0 if v > 1.0 else v


def _load_benchmark_ytd(conn, year_start: str, today_str: str) -> Optional[float]:
    """沪深300 YTD return from benchmark_quotes cache."""
    start_row = conn.execute(
        "SELECT close FROM benchmark_quotes WHERE code='000300' AND quote_date >= ? ORDER BY quote_date LIMIT 1",
        (year_start,),
    ).fetchone()
    end_row = conn.execute(
        "SELECT close FROM benchmark_quotes WHERE code='000300' AND quote_date <= ? ORDER BY quote_date DESC LIMIT 1",
        (today_str,),
    ).fetchone()
    if start_row and end_row and start_row["close"] > 0:
        return (float(end_row["close"]) - float(start_row["close"])) / float(start_row["close"])
    return None


def _load_risk_snapshot(conn) -> tuple[Optional[float], Optional[float]]:
    """(max_drawdown, portfolio_vol) from latest risk_metrics row."""
    row = conn.execute(
        "SELECT max_drawdown, portfolio_vol FROM risk_metrics ORDER BY calc_date DESC, id DESC LIMIT 1"
    ).fetchone()
    if row:
        return (
            float(row["max_drawdown"]) if row["max_drawdown"] is not None else None,
            float(row["portfolio_vol"]) if row["portfolio_vol"] is not None else None,
        )
    return None, None


# ── Computation ───────────────────────────────────────────────────────────────

def compute_goal_progress(db_path=None) -> GoalProgressResult:
    """Compute YTD progress vs annual target. Returns GoalProgressResult."""
    today = date.today()
    today_str = today.isoformat()
    year_start = _year_start(today)

    conn = connect(db_path)
    try:
        target_return = _load_goal_target(conn)
        start_value   = _load_portfolio_start_value(conn, year_start)
        current_bc    = _load_portfolio_current_value(conn)
        cash_value    = _load_cash_value(conn)
        benchmark_ytd = _load_benchmark_ytd(conn, year_start, today_str)
        max_dd, port_vol = _load_risk_snapshot(conn)
    finally:
        conn.close()

    # Total portfolio value (B+C + A cash)
    portfolio_value = (current_bc or 0.0) + cash_value

    # Compute YTD return
    if start_value and start_value > 0 and current_bc is not None:
        actual_ytd = (current_bc - start_value) / start_value
        insufficient = False
    else:
        actual_ytd = None
        insufficient = True

    # Linear target YTD (time-proportional)
    target_ytd: Optional[float] = None
    progress_gap: Optional[float] = None
    required_remaining: Optional[float] = None

    if target_return is not None:
        elapsed_frac = _days_elapsed(today) / _days_in_year(today)
        target_ytd = target_return * elapsed_frac
        if actual_ytd is not None:
            progress_gap = actual_ytd - target_ytd
            # Required daily return for remaining days to hit annual target
            remaining_days = _days_in_year(today) - _days_elapsed(today)
            if remaining_days > 0 and actual_ytd is not None:
                # (1 + target) = (1 + actual_ytd) * (1 + r_remaining)^(remaining/365)
                # r_remaining = ((1+target)/(1+actual_ytd))^(365/remaining) - 1
                try:
                    required_remaining = (
                        ((1 + target_return) / (1 + actual_ytd))
                        ** (365.0 / remaining_days)
                        - 1.0
                    )
                except (ZeroDivisionError, ValueError):
                    required_remaining = None

    # Risk budget used: max_drawdown / max_drawdown_tolerance
    risk_budget_used: Optional[float] = None
    if max_dd is not None:
        conn2 = connect(db_path)
        profile = conn2.execute(
            "SELECT max_drawdown_tolerance FROM user_profile ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn2.close()
        if profile and profile["max_drawdown_tolerance"]:
            tolerance = float(profile["max_drawdown_tolerance"])
            # tolerance stored as percent (20.0 → 0.20)
            if tolerance > 1.0:
                tolerance /= 100.0
            risk_budget_used = min(abs(max_dd) / tolerance, 1.0) if tolerance > 0 else None

    human = _build_human_message(
        today_str, target_return, actual_ytd, target_ytd, progress_gap,
        required_remaining, max_dd, risk_budget_used, benchmark_ytd,
        portfolio_value, insufficient,
    )

    return GoalProgressResult(
        progress_date=today_str,
        target_annual_return=target_return,
        actual_ytd_return=actual_ytd,
        target_ytd_return=target_ytd,
        progress_gap=progress_gap,
        required_return_remaining=required_remaining,
        max_drawdown=max_dd,
        risk_budget_used=risk_budget_used,
        benchmark_return_ytd=benchmark_ytd,
        portfolio_value=portfolio_value if portfolio_value > 0 else None,
        insufficient_data=insufficient,
        human_message=human,
    )


def save_goal_progress(result: GoalProgressResult, db_path=None) -> bool:
    """Upsert into goal_progress (primary key = progress_date)."""
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    note = "insufficient_data" if result.insufficient_data else None
    try:
        with transaction(db_path) as conn:
            conn.execute(
                """INSERT INTO goal_progress
                   (progress_date, target_annual_return, actual_ytd_return,
                    target_ytd_return, progress_gap, required_return_remaining,
                    max_drawdown, risk_budget_used, benchmark_return_ytd,
                    portfolio_value, notes, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(progress_date) DO UPDATE SET
                     actual_ytd_return=excluded.actual_ytd_return,
                     target_ytd_return=excluded.target_ytd_return,
                     progress_gap=excluded.progress_gap,
                     required_return_remaining=excluded.required_return_remaining,
                     max_drawdown=excluded.max_drawdown,
                     risk_budget_used=excluded.risk_budget_used,
                     benchmark_return_ytd=excluded.benchmark_return_ytd,
                     portfolio_value=excluded.portfolio_value,
                     notes=excluded.notes""",
                (result.progress_date, result.target_annual_return, result.actual_ytd_return,
                 result.target_ytd_return, result.progress_gap, result.required_return_remaining,
                 result.max_drawdown, result.risk_budget_used, result.benchmark_return_ytd,
                 result.portfolio_value, note, now),
            )
        return True
    except Exception:
        return False


def run_goal_engine(db_path=None) -> GoalProgressResult:
    """Compute and persist goal progress. Returns result."""
    result = compute_goal_progress(db_path)
    save_goal_progress(result, db_path)
    return result


# ── Human message ─────────────────────────────────────────────────────────────

def _pct(v: Optional[float], decimals: int = 2) -> str:
    if v is None:
        return "N/A"
    return f"{v*100:+.{decimals}f}%"


def _build_human_message(
    today: str,
    target_annual: Optional[float],
    actual_ytd: Optional[float],
    target_ytd: Optional[float],
    gap: Optional[float],
    required_remaining: Optional[float],
    max_dd: Optional[float],
    risk_budget: Optional[float],
    benchmark_ytd: Optional[float],
    portfolio_value: Optional[float],
    insufficient: bool,
) -> str:
    lines = [f"## 年度目标进度 — {today}\n"]

    if insufficient:
        lines.append("⚠ 数据不足：缺少年初持仓或报价数据，无法计算 YTD 收益。")
        lines.append("所以你该做什么：先执行 `inv snapshot pull` 拉取行情，或检查 holdings 数据是否完整。")
        return "\n".join(lines)

    if portfolio_value:
        lines.append(f"当前组合总值：{portfolio_value/1e4:.1f}万元")

    lines.append(f"\n| 指标 | 当前值 |")
    lines.append(f"|------|-------|")
    lines.append(f"| 年度目标收益率 | {_pct(target_annual)} |")
    lines.append(f"| 实际 YTD 收益 | {_pct(actual_ytd)} |")
    lines.append(f"| 应达 YTD（线性） | {_pct(target_ytd)} |")
    lines.append(f"| 进度差距 | {_pct(gap)} ({'领先' if (gap or 0) >= 0 else '落后'}) |")
    lines.append(f"| 剩余所需年化 | {_pct(required_remaining)} |")
    lines.append(f"| 最大回撤 | {_pct(max_dd)} |")
    lines.append(f"| 风险预算使用 | {f'{risk_budget*100:.0f}%' if risk_budget is not None else 'N/A'} |")
    lines.append(f"| 沪深300 YTD | {_pct(benchmark_ytd)} |")

    lines.append("")
    if gap is not None and gap < -0.02:
        lines.append(
            "所以你该做什么：当前落后年度目标，重点检查持仓论点是否失效，"
            "考虑优化持仓结构以提高选股回报。"
        )
    elif gap is not None and gap >= 0:
        lines.append("当前领先年度目标，维持现有策略，注意控制回撤。")
    else:
        lines.append("进度基本符合预期，继续监控。")

    return "\n".join(lines)
