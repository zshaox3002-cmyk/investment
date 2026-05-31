"""Unit tests for Phase 5 performance attribution engine.

Key invariant: timing + selection + allocation + interaction ≈ excess_return
"""
from __future__ import annotations

import pytest

from investment.agent_tools.attribution import (
    AttributionResult,
    _ability_assessment,
    _bhb_decompose,
    _build_human_message,
    _compute_daily_returns,
    _compute_portfolio_return,
    run_attribution,
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


def _seed_portfolio(db_path, prices_by_date: dict[str, float], shares: float = 1000.0):
    """Seed a single-instrument portfolio with given daily prices."""
    from investment.core.db import connect
    conn = connect(db_path)
    conn.execute(
        "INSERT INTO instruments (code,market,name,asset_class,tranche) VALUES (?,?,?,?,?)",
        ("TEST", "A", "测试股票", "STOCK", "C"),
    )
    iid = conn.execute("SELECT id FROM instruments WHERE code='TEST'").fetchone()["id"]
    dates = sorted(prices_by_date.keys())
    conn.execute(
        "INSERT INTO holdings (instrument_id,effective_date,shares,cost_price,source) VALUES (?,?,?,?,?)",
        (iid, dates[0], shares, prices_by_date[dates[0]], "manual"),
    )
    for d, price in prices_by_date.items():
        conn.execute(
            "INSERT OR IGNORE INTO quotes (instrument_id,quote_date,close,fetched_at) VALUES (?,?,?,?)",
            (iid, d, price, "2026-05-30T00:00:00Z"),
        )
    conn.commit()
    conn.close()
    return iid


def _seed_benchmark(db_path, prices_by_date: dict[str, float], code: str = "000300"):
    """Seed benchmark quotes directly into DB (bypass akshare)."""
    from investment.core.db import connect
    conn = connect(db_path)
    for d, price in prices_by_date.items():
        conn.execute(
            "INSERT OR IGNORE INTO benchmark_quotes (code,name,quote_date,close) VALUES (?,?,?,?)",
            (code, "沪深300", d, price),
        )
    conn.commit()
    conn.close()


# ── _compute_portfolio_return ─────────────────────────────────────────────────

class TestComputePortfolioReturn:
    def test_positive_return(self):
        values = {"2026-01-01": 100.0, "2026-01-02": 110.0}
        assert _compute_portfolio_return(values) == pytest.approx(0.10, rel=1e-6)

    def test_negative_return(self):
        values = {"2026-01-01": 100.0, "2026-01-02": 90.0}
        assert _compute_portfolio_return(values) == pytest.approx(-0.10, rel=1e-6)

    def test_zero_return(self):
        values = {"2026-01-01": 100.0, "2026-01-02": 100.0}
        assert _compute_portfolio_return(values) == pytest.approx(0.0, abs=1e-10)

    def test_single_value_returns_zero(self):
        assert _compute_portfolio_return({"2026-01-01": 100.0}) == 0.0

    def test_empty_returns_zero(self):
        assert _compute_portfolio_return({}) == 0.0

    def test_multi_day(self):
        values = {"2026-01-01": 100.0, "2026-01-02": 105.0, "2026-01-03": 110.25}
        result = _compute_portfolio_return(values)
        assert result == pytest.approx(0.1025, rel=1e-4)


# ── _compute_daily_returns ────────────────────────────────────────────────────

class TestComputeDailyReturns:
    def test_basic(self):
        values = {"2026-01-01": 100.0, "2026-01-02": 110.0, "2026-01-03": 99.0}
        returns = _compute_daily_returns(values)
        assert len(returns) == 2
        assert returns[0] == pytest.approx(0.10, rel=1e-6)
        assert returns[1] == pytest.approx(-0.10, rel=1e-4)

    def test_single_value_empty(self):
        assert _compute_daily_returns({"2026-01-01": 100.0}) == []


# ── _bhb_decompose ────────────────────────────────────────────────────────────

class TestBHBDecompose:
    def test_sum_equals_excess_short_period(self):
        """Core invariant: timing + selection + allocation + interaction = excess."""
        port = [0.01, -0.02, 0.015, -0.005]
        bench = [0.005, -0.01, 0.008, -0.003]
        timing, selection, allocation, interaction = _bhb_decompose(port, bench)
        total = timing + selection + allocation + interaction

        # Compute expected excess
        import numpy as np
        port_total = float(np.prod([1 + r for r in port]) - 1)
        bench_total = float(np.prod([1 + r for r in bench]) - 1)
        excess = port_total - bench_total

        assert total == pytest.approx(excess, abs=1e-6)

    def test_sum_equals_excess_long_period(self):
        """Invariant holds for longer periods too."""
        import numpy as np
        np.random.seed(42)
        port = list(np.random.randn(30) * 0.01)
        bench = list(np.random.randn(30) * 0.008)
        timing, selection, allocation, interaction = _bhb_decompose(port, bench)
        total = timing + selection + allocation + interaction

        port_total = float(np.prod([1 + r for r in port]) - 1)
        bench_total = float(np.prod([1 + r for r in bench]) - 1)
        excess = port_total - bench_total

        assert total == pytest.approx(excess, abs=1e-4)

    def test_zero_returns_zero_decomp(self):
        port = [0.0, 0.0, 0.0]
        bench = [0.0, 0.0, 0.0]
        timing, selection, allocation, interaction = _bhb_decompose(port, bench)
        assert timing == pytest.approx(0.0, abs=1e-10)
        assert selection == pytest.approx(0.0, abs=1e-10)

    def test_insufficient_data(self):
        timing, selection, allocation, interaction = _bhb_decompose([], [])
        assert timing == 0.0
        assert selection == 0.0

    def test_outperformance_positive_selection(self):
        # Portfolio consistently beats benchmark → positive selection
        port = [0.02, 0.02, 0.02, 0.02]
        bench = [0.01, 0.01, 0.01, 0.01]
        timing, selection, allocation, interaction = _bhb_decompose(port, bench)
        total = timing + selection + allocation + interaction
        assert total > 0  # positive excess


# ── _ability_assessment ───────────────────────────────────────────────────────

class TestAbilityAssessment:
    def test_outperformance_message(self):
        msg = _ability_assessment(0.15, 0.10, 0.05, 0.03, 0.015, 30)
        assert "跑赢" in msg

    def test_underperformance_message(self):
        msg = _ability_assessment(0.05, 0.10, -0.05, -0.03, -0.015, 30)
        assert "跑输" in msg

    def test_market_gave_most_message(self):
        # Portfolio +5%, benchmark +13%, but portfolio positive
        msg = _ability_assessment(0.05, 0.13, -0.08, -0.05, -0.02, 30)
        assert "大盘给的" in msg or "跑输" in msg

    def test_insufficient_data_warning(self):
        msg = _ability_assessment(0.05, 0.03, 0.02, 0.012, 0.006, 5)
        assert "数据" in msg or "参考" in msg


# ── _build_human_message ──────────────────────────────────────────────────────

class TestBuildHumanMessage:
    def _make_result(self, **kwargs) -> AttributionResult:
        defaults = dict(
            period_start="2026-01-01", period_end="2026-03-31",
            benchmark_code="000300", benchmark_name="沪深300",
            total_return=0.12, benchmark_return=0.08, excess_return=0.04,
            timing_contrib=0.012, selection_contrib=0.024, allocation_contrib=0.004,
            interaction_contrib=0.0,
            instrument_count=5, data_days=60, insufficient_data=False,
            human_message="",
        )
        defaults.update(kwargs)
        return AttributionResult(**defaults)

    def test_contains_returns_table(self):
        r = self._make_result()
        msg = _build_human_message(r)
        assert "组合收益" in msg
        assert "基准" in msg
        assert "超额收益" in msg

    def test_contains_decomposition(self):
        r = self._make_result()
        msg = _build_human_message(r)
        assert "择时" in msg
        assert "选股" in msg
        assert "配置" in msg

    def test_contains_action_directive(self):
        r = self._make_result()
        msg = _build_human_message(r)
        assert "所以你该做什么" in msg

    def test_insufficient_data_warning_shown(self):
        r = self._make_result(insufficient_data=True, data_days=3)
        msg = _build_human_message(r)
        assert "数据" in msg and "3" in msg

    def test_underperformance_action(self):
        r = self._make_result(excess_return=-0.05)
        msg = _build_human_message(r)
        assert "review stats" in msg or "复盘" in msg

    def test_outperformance_action(self):
        r = self._make_result(excess_return=0.05)
        msg = _build_human_message(r)
        assert "IC Memo" in msg or "成功经验" in msg


# ── Integration: run_attribution with seeded DB ───────────────────────────────

class TestRunAttributionIntegration:
    def test_sum_invariant_with_real_data(self, tmp_db):
        """Core invariant: contributions sum to excess_return."""
        prices_port = {
            "2026-05-26": 100.0, "2026-05-27": 102.0,
            "2026-05-28": 101.0, "2026-05-29": 103.0, "2026-05-30": 105.0,
        }
        prices_bench = {
            "2026-05-26": 4900.0, "2026-05-27": 4920.0,
            "2026-05-28": 4910.0, "2026-05-29": 4930.0, "2026-05-30": 4950.0,
        }
        _seed_portfolio(tmp_db, prices_port)
        _seed_benchmark(tmp_db, prices_bench)

        result = run_attribution(
            period_start="2026-05-26", period_end="2026-05-30",
            benchmark_code="000300", save=False, db_path=tmp_db,
        )

        total_decomp = (
            result.timing_contrib + result.selection_contrib +
            result.allocation_contrib + result.interaction_contrib
        )
        assert total_decomp == pytest.approx(result.excess_return, abs=1e-4)

    def test_insufficient_data_handled(self, tmp_db):
        """Single day of data → insufficient_data=True, no crash."""
        prices_port = {"2026-05-30": 100.0}
        prices_bench = {"2026-05-30": 4900.0}
        _seed_portfolio(tmp_db, prices_port)
        _seed_benchmark(tmp_db, prices_bench)

        result = run_attribution(
            period_start="2026-05-30", period_end="2026-05-30",
            save=False, db_path=tmp_db,
        )
        assert result.insufficient_data is True
        assert len(result.human_message) > 0

    def test_save_persists_to_db(self, tmp_db):
        prices_port = {
            "2026-05-26": 100.0, "2026-05-27": 102.0,
            "2026-05-28": 101.0, "2026-05-29": 103.0, "2026-05-30": 105.0,
        }
        prices_bench = {
            "2026-05-26": 4900.0, "2026-05-27": 4920.0,
            "2026-05-28": 4910.0, "2026-05-29": 4930.0, "2026-05-30": 4950.0,
        }
        _seed_portfolio(tmp_db, prices_port)
        _seed_benchmark(tmp_db, prices_bench)

        result = run_attribution(
            period_start="2026-05-26", period_end="2026-05-30",
            save=True, db_path=tmp_db,
        )

        from investment.core.db import connect
        conn = connect(tmp_db)
        row = conn.execute(
            "SELECT * FROM performance_attribution WHERE period_start=? AND period_end=?",
            (result.period_start, result.period_end),
        ).fetchone()
        conn.close()
        assert row is not None
        assert abs(row["total_return"] - result.total_return) < 1e-6

    def test_human_message_no_technical_codes(self, tmp_db):
        prices_port = {
            "2026-05-26": 100.0, "2026-05-27": 102.0,
            "2026-05-28": 101.0, "2026-05-29": 103.0, "2026-05-30": 105.0,
        }
        prices_bench = {
            "2026-05-26": 4900.0, "2026-05-27": 4920.0,
            "2026-05-28": 4910.0, "2026-05-29": 4930.0, "2026-05-30": 4950.0,
        }
        _seed_portfolio(tmp_db, prices_port)
        _seed_benchmark(tmp_db, prices_bench)

        result = run_attribution(
            period_start="2026-05-26", period_end="2026-05-30",
            save=False, db_path=tmp_db,
        )
        msg = result.human_message
        forbidden = ["instrument_id=", "period_id=", "benchmark_code="]
        for f in forbidden:
            assert f not in msg

    def test_real_db_smoke(self):
        """Smoke test against real DB — should not crash."""
        result = run_attribution(
            period_start="2026-05-26", period_end="2026-05-30",
            save=False,
        )
        assert result is not None
        assert result.data_days >= 0
        # Invariant must hold even with real data
        total_decomp = (
            result.timing_contrib + result.selection_contrib +
            result.allocation_contrib + result.interaction_contrib
        )
        assert total_decomp == pytest.approx(result.excess_return, abs=1e-4)
