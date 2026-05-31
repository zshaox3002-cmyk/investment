"""Unit tests for Phase 4 risk engine.

Uses known synthetic data to verify calculations against hand-computed values.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from investment.agent_tools.risk_engine import (
    InstrumentReturns,
    PseudoDiversification,
    _align_returns,
    _build_human_message,
    calc_annualised_vol,
    calc_correlation_matrix,
    calc_max_drawdown,
    calc_portfolio_returns,
    calc_risk_contributions,
    calc_sharpe,
    calc_var,
    detect_pseudo_diversification,
    run_risk_engine,
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


def _make_instrument(code: str, returns: list[float], weight: float = 0.5) -> InstrumentReturns:
    return InstrumentReturns(
        instrument_id=hash(code) % 1000,
        code=code, name=code,
        tranche="C", weight=weight,
        returns=np.array(returns, dtype=float),
    )


# ── calc_annualised_vol ───────────────────────────────────────────────────────

class TestCalcAnnualisedVol:
    def test_known_value(self):
        # Daily returns with known std
        returns = np.array([0.01, -0.01, 0.02, -0.02, 0.01])
        daily_std = float(np.std(returns, ddof=1))
        expected = daily_std * math.sqrt(252)
        assert calc_annualised_vol(returns) == pytest.approx(expected, rel=1e-6)

    def test_zero_returns(self):
        returns = np.zeros(10)
        assert calc_annualised_vol(returns) == pytest.approx(0.0, abs=1e-10)

    def test_single_return(self):
        assert calc_annualised_vol(np.array([0.01])) == 0.0

    def test_empty(self):
        assert calc_annualised_vol(np.array([])) == 0.0

    def test_constant_positive(self):
        returns = np.full(20, 0.001)
        assert calc_annualised_vol(returns) == pytest.approx(0.0, abs=1e-10)


# ── calc_max_drawdown ─────────────────────────────────────────────────────────

class TestCalcMaxDrawdown:
    def test_no_drawdown(self):
        # Monotonically increasing prices
        returns = np.full(10, 0.01)
        dd, dur = calc_max_drawdown(returns)
        assert dd == pytest.approx(0.0, abs=1e-6)
        assert dur == 0

    def test_known_drawdown(self):
        # Prices: 100 → 110 → 99 → 105
        # log returns: ln(110/100), ln(99/110), ln(105/99)
        prices = [100, 110, 99, 105]
        returns = np.diff(np.log(prices))
        dd, dur = calc_max_drawdown(returns)
        # Peak at 110, trough at 99: drawdown = (99-110)/110 ≈ -0.1
        assert dd == pytest.approx(-0.1, abs=0.01)
        assert dur >= 1

    def test_duration_counts_consecutive(self):
        # Prices: 100 → 90 → 80 → 85 (drawdown for 2 periods)
        prices = [100, 90, 80, 85]
        returns = np.diff(np.log(prices))
        dd, dur = calc_max_drawdown(returns)
        assert dur == 2

    def test_empty(self):
        dd, dur = calc_max_drawdown(np.array([]))
        assert dd == 0.0
        assert dur == 0


# ── calc_var ──────────────────────────────────────────────────────────────────

class TestCalcVar:
    def test_var_95_known(self):
        # 200 returns: 180 at 0.01, 20 at -0.10
        # 5th percentile index = 0.05*199 = 9.95 → both neighbours are -0.10
        returns = np.array([0.01] * 180 + [-0.10] * 20)
        var = calc_var(returns, 0.95)
        assert var == pytest.approx(-0.10, abs=1e-6)

    def test_var_is_negative(self):
        returns = np.random.randn(100) * 0.01
        var = calc_var(returns, 0.95)
        # For random returns, 95% VaR should be negative (left tail)
        assert var <= 0.0 or abs(var) < 1e-6  # could be near zero

    def test_insufficient_data(self):
        assert calc_var(np.array([0.01, -0.01]), 0.95) == 0.0


# ── calc_sharpe ───────────────────────────────────────────────────────────────

class TestCalcSharpe:
    def test_positive_sharpe(self):
        # Consistent positive returns with some variance → positive Sharpe
        np.random.seed(42)
        returns = np.abs(np.random.randn(252)) * 0.005 + 0.001
        sharpe = calc_sharpe(returns)
        assert sharpe > 0

    def test_negative_sharpe(self):
        np.random.seed(42)
        returns = -np.abs(np.random.randn(252)) * 0.005 - 0.001
        sharpe = calc_sharpe(returns)
        assert sharpe < 0

    def test_zero_std(self):
        # All same returns → std=0 → Sharpe=0
        returns = np.full(10, 0.005)
        assert calc_sharpe(returns) == 0.0


# ── calc_correlation_matrix ───────────────────────────────────────────────────

class TestCalcCorrelationMatrix:
    def test_perfect_correlation(self):
        r = np.array([0.01, -0.01, 0.02, -0.02, 0.01])
        matrix = np.column_stack([r, r])  # identical series
        corr = calc_correlation_matrix(matrix)
        assert corr[0, 1] == pytest.approx(1.0, abs=1e-6)

    def test_perfect_negative_correlation(self):
        r = np.array([0.01, -0.01, 0.02, -0.02, 0.01])
        matrix = np.column_stack([r, -r])
        corr = calc_correlation_matrix(matrix)
        assert corr[0, 1] == pytest.approx(-1.0, abs=1e-6)

    def test_diagonal_is_one(self):
        r1 = np.random.randn(20)
        r2 = np.random.randn(20)
        matrix = np.column_stack([r1, r2])
        corr = calc_correlation_matrix(matrix)
        assert corr[0, 0] == pytest.approx(1.0, abs=1e-6)
        assert corr[1, 1] == pytest.approx(1.0, abs=1e-6)

    def test_single_instrument(self):
        r = np.array([[0.01], [-0.01], [0.02]])
        corr = calc_correlation_matrix(r)
        assert corr.shape == (1, 1)


# ── calc_risk_contributions ───────────────────────────────────────────────────

class TestCalcRiskContributions:
    def test_contributions_sum_to_one(self):
        np.random.seed(42)
        matrix = np.random.randn(50, 3) * 0.01
        weights = np.array([0.4, 0.35, 0.25])
        contrib, _ = calc_risk_contributions(matrix, weights)
        assert contrib.sum() == pytest.approx(1.0, abs=1e-4)

    def test_dominant_position_has_high_contrib(self):
        # One instrument with 10x the volatility of others
        np.random.seed(42)
        r_low = np.random.randn(50) * 0.001
        r_high = np.random.randn(50) * 0.05
        matrix = np.column_stack([r_high, r_low, r_low])
        weights = np.array([0.5, 0.25, 0.25])
        contrib, _ = calc_risk_contributions(matrix, weights)
        # High-vol instrument should dominate risk
        assert contrib[0] > 0.5

    def test_equal_weights_equal_vol_equal_contrib(self):
        np.random.seed(0)
        r = np.random.randn(100) * 0.01
        matrix = np.column_stack([r, r, r])  # identical
        weights = np.array([1/3, 1/3, 1/3])
        contrib, _ = calc_risk_contributions(matrix, weights)
        assert contrib[0] == pytest.approx(contrib[1], abs=1e-4)


# ── _align_returns ────────────────────────────────────────────────────────────

class TestAlignReturns:
    def test_aligns_to_min_length(self):
        inst_a = _make_instrument("A", [0.01, -0.01, 0.02, -0.02])
        inst_b = _make_instrument("B", [0.01, -0.01])
        matrix, aligned = _align_returns([inst_a, inst_b])
        assert matrix.shape == (2, 2)

    def test_empty_list(self):
        matrix, aligned = _align_returns([])
        assert matrix.shape == (0, 0)
        assert aligned == []


# ── detect_pseudo_diversification ────────────────────────────────────────────

class TestDetectPseudoDiversification:
    def _make_instruments(self, n: int, weights=None) -> list[InstrumentReturns]:
        if weights is None:
            weights = [1/n] * n
        return [_make_instrument(f"S{i}", [0.01, -0.01, 0.02], w)
                for i, w in enumerate(zip(range(n), weights))]

    def test_single_instrument_no_pseudo(self):
        insts = [_make_instrument("A", [0.01, -0.01, 0.02], 1.0)]
        corr = np.array([[1.0]])
        contrib = np.array([1.0])
        result = detect_pseudo_diversification(insts, corr, contrib, {})
        assert result.detected is False

    def test_high_single_contributor_detected(self):
        insts = [
            _make_instrument("A", [0.01, -0.01, 0.02], 0.5),
            _make_instrument("B", [0.01, -0.01, 0.02], 0.5),
        ]
        corr = np.array([[1.0, 0.5], [0.5, 1.0]])
        # A contributes 90% of risk
        contrib = np.array([0.9, 0.1])
        result = detect_pseudo_diversification(insts, corr, contrib, {})
        assert result.detected is True
        assert result.top_contributor_pct == pytest.approx(0.9)

    def test_theme_concentration_detected(self):
        insts = [
            _make_instrument("A", [0.01, -0.01, 0.02], 0.4),
            _make_instrument("B", [0.01, -0.01, 0.02], 0.35),
            _make_instrument("C", [0.01, -0.01, 0.02], 0.25),
        ]
        corr = np.eye(3)
        contrib = np.array([0.4, 0.35, 0.25])
        # All in 新能源汽车 theme
        theme_map = {
            insts[0].instrument_id: "新能源汽车",
            insts[1].instrument_id: "新能源汽车",
            insts[2].instrument_id: "新能源汽车",
        }
        result = detect_pseudo_diversification(insts, corr, contrib, theme_map)
        assert result.detected is True
        assert "新能源" in result.concentrated_theme


# ── Integration: run_risk_engine with real DB ─────────────────────────────────

class TestRiskEngineIntegration:
    def test_runs_without_error(self):
        report = run_risk_engine(lookback_days=60, save=False)
        assert report is not None
        assert report.calc_date is not None

    def test_human_message_not_empty(self):
        report = run_risk_engine(lookback_days=60, save=False)
        assert len(report.human_message) > 100

    def test_human_message_contains_action(self):
        report = run_risk_engine(lookback_days=60, save=False)
        assert "所以你该做什么" in report.human_message

    def test_no_technical_codes_in_output(self):
        report = run_risk_engine(lookback_days=60, save=False)
        msg = report.human_message
        forbidden = ["instrument_id=", "calc_date=", "corr_value="]
        for f in forbidden:
            assert f not in msg

    def test_insufficient_data_flagged(self):
        # Real DB only has ~5 days of data
        report = run_risk_engine(lookback_days=252, save=False)
        assert report.insufficient_data is True
        assert "数据不足" in report.human_message

    def test_save_persists_to_db(self, tmp_db):
        # Seed minimal data into tmp_db
        from investment.core.db import connect as db_connect
        conn = db_connect(tmp_db)
        # Insert two instruments
        conn.execute(
            "INSERT INTO instruments (code,market,name,asset_class,tranche) VALUES (?,?,?,?,?)",
            ("TEST1", "A", "测试股票1", "STOCK", "C")
        )
        conn.execute(
            "INSERT INTO instruments (code,market,name,asset_class,tranche) VALUES (?,?,?,?,?)",
            ("TEST2", "A", "测试股票2", "STOCK", "C")
        )
        conn.commit()
        # Insert holdings
        for code in ("TEST1", "TEST2"):
            iid = conn.execute("SELECT id FROM instruments WHERE code=?", (code,)).fetchone()["id"]
            conn.execute(
                "INSERT INTO holdings (instrument_id,effective_date,shares,cost_price,source) VALUES (?,?,?,?,?)",
                (iid, "2026-05-01", 1000, 10.0, "manual")
            )
            # Insert 5 days of quotes
            for i, price in enumerate([10.0, 10.1, 9.9, 10.2, 10.05]):
                d = f"2026-05-{26+i:02d}"
                conn.execute(
                    "INSERT OR IGNORE INTO quotes (instrument_id,quote_date,close,fetched_at) VALUES (?,?,?,?)",
                    (iid, d, price, "2026-05-30T00:00:00Z")
                )
        conn.commit()
        conn.close()

        report = run_risk_engine(lookback_days=60, db_path=tmp_db, save=True)
        # Verify saved to DB
        conn2 = db_connect(tmp_db)
        row = conn2.execute("SELECT * FROM risk_metrics WHERE calc_date=?", (report.calc_date,)).fetchone()
        conn2.close()
        assert row is not None
