"""Unit tests for Phase 4: goal_engine and position_health."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional
from unittest.mock import patch, MagicMock

import pytest

from investment.core.db import connect, init_db
from investment.core.sql_migrator import run_sql_migrations
from investment.migration._10_agent_v3 import run as run_m10
from investment.agent_tools.goal_engine import (
    GoalProgressResult,
    compute_goal_progress,
    save_goal_progress,
    run_goal_engine,
    _days_elapsed,
    _days_in_year,
    _year_start,
    _build_human_message,
    _pct,
)
from investment.agent_tools.position_health import (
    PositionHealthRecord,
    compute_position_health,
    save_position_health,
    run_position_health,
    build_health_summary,
    _score_pnl,
    _score_thesis,
    _score_risk_contrib,
    _alert_penalty,
    _label,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    run_sql_migrations(db_path=db_path)
    run_m10(db_path=db_path)
    return db_path


@pytest.fixture
def conn(tmp_db):
    c = connect(tmp_db)
    yield c
    c.close()


def _seed_instrument(conn, code="600519", name="贵州茅台", tranche="C"):
    conn.execute(
        "INSERT OR IGNORE INTO instruments (code, market, name, asset_class, tranche) "
        "VALUES (?, 'A', ?, 'STOCK', ?)",
        (code, name, tranche),
    )
    conn.commit()
    return conn.execute("SELECT id FROM instruments WHERE code=?", (code,)).fetchone()["id"]


def _seed_holding(conn, iid, shares=100, cost_price=100.0):
    today = date.today().isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO holdings (instrument_id, effective_date, shares, cost_price, source) "
        "VALUES (?,?,?,?,'manual')",
        (iid, today, shares, cost_price),
    )
    conn.commit()


def _seed_quote(conn, iid, close=90.0, quote_date=None):
    d = quote_date or date.today().isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO quotes (instrument_id, quote_date, close, fetched_at) "
        "VALUES (?,?,?,CURRENT_TIMESTAMP)",
        (iid, d, close),
    )
    conn.commit()


def _seed_goal(conn, target_return=10.0):
    # user_profile must exist first (FK)
    conn.execute(
        "INSERT OR IGNORE INTO user_profile "
        "(risk_tolerance, max_drawdown_tolerance, horizon_years, investable_capital, "
        "a_ratio, b_ratio, c_ratio, created_at, updated_at) "
        "VALUES ('moderate', 20.0, 5, 1000000, 0.25, 0.50, 0.25, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    conn.commit()
    pid = conn.execute("SELECT id FROM user_profile ORDER BY id DESC LIMIT 1").fetchone()["id"]
    conn.execute(
        "INSERT INTO goals (profile_id, name, target_annual_return, target_amount, deadline, status, created_at) "
        "VALUES (?, '年度目标', ?, 0, '2026-12-31', 'active', CURRENT_TIMESTAMP)",
        (pid, target_return),
    )
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# goal_engine helpers
# ═══════════════════════════════════════════════════════════════════════════════

def test_year_start():
    d = date(2026, 6, 15)
    assert _year_start(d) == "2026-01-01"


def test_days_elapsed_jan1():
    d = date(2026, 1, 1)
    assert _days_elapsed(d) == 1


def test_days_elapsed_jun2():
    d = date(2026, 6, 2)
    elapsed = _days_elapsed(d)
    assert elapsed == 153   # Jan(31)+Feb(28)+Mar(31)+Apr(30)+May(31)+Jun(2) = 153


def test_days_in_year_non_leap():
    assert _days_in_year(date(2026, 1, 1)) == 365


def test_days_in_year_leap():
    assert _days_in_year(date(2024, 1, 1)) == 366


def test_pct_none():
    assert _pct(None) == "N/A"


def test_pct_positive():
    assert _pct(0.10) == "+10.00%"


def test_pct_negative():
    assert _pct(-0.05) == "-5.00%"


# ═══════════════════════════════════════════════════════════════════════════════
# compute_goal_progress — no data
# ═══════════════════════════════════════════════════════════════════════════════

def test_goal_progress_insufficient_data_no_holdings(tmp_db):
    result = compute_goal_progress(db_path=tmp_db)
    assert result.insufficient_data is True
    assert result.actual_ytd_return is None
    assert "insufficient" in result.human_message.lower() or "不足" in result.human_message


# ═══════════════════════════════════════════════════════════════════════════════
# compute_goal_progress — with data
# ═══════════════════════════════════════════════════════════════════════════════

def test_goal_progress_with_goal_and_holdings(tmp_db, conn):
    iid = _seed_instrument(conn)
    year_start = date.today().replace(month=1, day=1).isoformat()
    # Year-start holding: 100 shares
    conn.execute(
        "INSERT OR REPLACE INTO holdings (instrument_id, effective_date, shares, cost_price, source) "
        "VALUES (?,?,100,100.0,'manual')",
        (iid, year_start),
    )
    # Year-start quote: 100
    _seed_quote(conn, iid, close=100.0, quote_date=year_start)
    # Current quote: 110 → +10% YTD
    _seed_quote(conn, iid, close=110.0)
    _seed_goal(conn, target_return=10.0)
    conn.commit()

    result = compute_goal_progress(db_path=tmp_db)
    assert result.insufficient_data is False
    assert result.actual_ytd_return is not None
    assert abs(result.actual_ytd_return - 0.10) < 0.01
    assert result.target_annual_return is not None
    assert result.target_ytd_return is not None
    assert result.progress_gap is not None


def test_goal_progress_no_goal_still_computes(tmp_db, conn):
    # No goal seeded — target_annual_return should be None
    # but actual YTD should still be computed if data available
    iid = _seed_instrument(conn)
    year_start = date.today().replace(month=1, day=1).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO holdings (instrument_id, effective_date, shares, cost_price, source) "
        "VALUES (?,?,50,200.0,'manual')",
        (iid, year_start),
    )
    _seed_quote(conn, iid, close=190.0)
    conn.commit()

    result = compute_goal_progress(db_path=tmp_db)
    assert result.target_annual_return is None
    assert result.target_ytd_return is None
    assert result.progress_gap is None


# ═══════════════════════════════════════════════════════════════════════════════
# save_goal_progress — upsert
# ═══════════════════════════════════════════════════════════════════════════════

def test_save_goal_progress_ok(tmp_db):
    today = date.today().isoformat()
    r = GoalProgressResult(
        progress_date=today,
        target_annual_return=0.10,
        actual_ytd_return=0.05,
        target_ytd_return=0.042,
        progress_gap=0.008,
        required_return_remaining=0.095,
        max_drawdown=-0.08,
        risk_budget_used=0.40,
        benchmark_return_ytd=0.03,
        portfolio_value=500_000.0,
        insufficient_data=False,
        human_message="ok",
    )
    ok = save_goal_progress(r, db_path=tmp_db)
    assert ok
    row = connect(tmp_db).execute(
        "SELECT * FROM goal_progress WHERE progress_date=?", (today,)
    ).fetchone()
    assert row is not None
    assert abs(float(row["actual_ytd_return"]) - 0.05) < 1e-9


def test_save_goal_progress_upsert(tmp_db):
    today = date.today().isoformat()

    def _r(ytd):
        return GoalProgressResult(
            progress_date=today, target_annual_return=0.10,
            actual_ytd_return=ytd, target_ytd_return=None, progress_gap=None,
            required_return_remaining=None, max_drawdown=None, risk_budget_used=None,
            benchmark_return_ytd=None, portfolio_value=None,
            insufficient_data=False, human_message="",
        )

    save_goal_progress(_r(0.03), db_path=tmp_db)
    save_goal_progress(_r(0.07), db_path=tmp_db)

    rows = connect(tmp_db).execute(
        "SELECT COUNT(*) AS n FROM goal_progress WHERE progress_date=?", (today,)
    ).fetchone()
    assert rows["n"] == 1

    row = connect(tmp_db).execute(
        "SELECT actual_ytd_return FROM goal_progress WHERE progress_date=?", (today,)
    ).fetchone()
    assert abs(float(row["actual_ytd_return"]) - 0.07) < 1e-9


def test_save_goal_progress_marks_insufficient(tmp_db):
    today = date.today().isoformat()
    r = GoalProgressResult(
        progress_date=today, target_annual_return=None, actual_ytd_return=None,
        target_ytd_return=None, progress_gap=None, required_return_remaining=None,
        max_drawdown=None, risk_budget_used=None, benchmark_return_ytd=None,
        portfolio_value=None, insufficient_data=True, human_message="",
    )
    save_goal_progress(r, db_path=tmp_db)
    row = connect(tmp_db).execute(
        "SELECT notes FROM goal_progress WHERE progress_date=?", (today,)
    ).fetchone()
    assert row["notes"] == "insufficient_data"


# ═══════════════════════════════════════════════════════════════════════════════
# build_human_message — branch coverage
# ═══════════════════════════════════════════════════════════════════════════════

def test_human_message_insufficient():
    msg = _build_human_message(
        "2026-06-02", None, None, None, None, None, None, None, None, None, True
    )
    assert "数据不足" in msg or "insufficient" in msg.lower()


def test_human_message_ahead():
    msg = _build_human_message(
        "2026-06-02", 0.10, 0.06, 0.042, 0.018, 0.085,
        -0.05, 0.25, 0.03, 500_000.0, False
    )
    assert "领先" in msg


def test_human_message_behind():
    msg = _build_human_message(
        "2026-06-02", 0.10, 0.01, 0.042, -0.032, 0.12,
        -0.08, 0.40, 0.03, 500_000.0, False
    )
    assert "落后" in msg or "检查" in msg


# ═══════════════════════════════════════════════════════════════════════════════
# position_health — scoring helpers
# ═══════════════════════════════════════════════════════════════════════════════

def test_score_pnl_positive():
    assert _score_pnl(0.25) == 40.0
    assert _score_pnl(0.15) == 35.0


def test_score_pnl_zero():
    assert _score_pnl(0.0) == 28.0


def test_score_pnl_deep_loss():
    assert _score_pnl(-0.25) == 0.0


def test_score_thesis_none_neutral():
    assert _score_thesis(None) == pytest.approx(12.5)


def test_score_thesis_max():
    assert _score_thesis(5.0) == pytest.approx(25.0)


def test_score_thesis_zero():
    assert _score_thesis(0.0) == pytest.approx(0.0)


def test_score_risk_contrib_low():
    assert _score_risk_contrib(0.05) == 20.0    # 5% contrib → full 20 pts


def test_score_risk_contrib_high():
    assert _score_risk_contrib(0.50) == 0.0     # 50% contrib → 0 pts


def test_alert_penalty():
    assert _alert_penalty(0) == 0.0
    assert _alert_penalty(3) == 15.0
    assert _alert_penalty(10) == 15.0   # capped


def test_label_act_on_low_score():
    assert _label(20.0, 0, -0.10) == "act"


def test_label_act_on_deep_drawdown():
    assert _label(60.0, 0, -0.25) == "act"


def test_label_act_on_many_alerts():
    assert _label(65.0, 3, -0.05) == "act"


def test_label_healthy():
    assert _label(80.0, 0, -0.05) == "healthy"


def test_label_watch():
    assert _label(55.0, 0, -0.05) == "watch"


def test_label_review_low_score():
    assert _label(40.0, 0, -0.05) == "review"


# ═══════════════════════════════════════════════════════════════════════════════
# compute_position_health — integration
# ═══════════════════════════════════════════════════════════════════════════════

def test_compute_position_health_empty_db(tmp_db):
    records = compute_position_health(db_path=tmp_db)
    assert records == []


def test_compute_position_health_with_holding(tmp_db, conn):
    iid = _seed_instrument(conn)
    _seed_holding(conn, iid, shares=100, cost_price=100.0)
    _seed_quote(conn, iid, close=85.0)   # pnl = -15%

    records = compute_position_health(db_path=tmp_db)
    assert len(records) == 1
    r = records[0]
    assert r.code == "600519"
    assert r.pnl_pct is not None
    assert abs(r.pnl_pct - (-0.15)) < 0.01
    assert r.health_score is not None
    assert r.health_label in ("act", "review", "watch", "healthy", "unknown")


def test_compute_position_health_thesis_bonus(tmp_db, conn):
    iid = _seed_instrument(conn)
    _seed_holding(conn, iid, shares=100, cost_price=100.0)
    _seed_quote(conn, iid, close=105.0)  # +5% pnl

    # Add a good thesis score
    conn.execute(
        "INSERT OR REPLACE INTO theses (instrument_id, version, body_path, current_score, updated_at) "
        "VALUES (?, '1.0', '/tmp/t.md', 4.5, CURRENT_TIMESTAMP)",
        (iid,),
    )
    conn.commit()

    records = compute_position_health(db_path=tmp_db)
    assert len(records) == 1
    r = records[0]
    # With good thesis + small gain, score should be reasonably high
    assert r.health_score is not None
    assert r.health_score >= 50  # 28 pnl + 22.5 thesis + 10 risk_neutral - 0 alert


def test_compute_position_health_alert_penalty(tmp_db, conn):
    iid = _seed_instrument(conn)
    _seed_holding(conn, iid, shares=100, cost_price=100.0)
    _seed_quote(conn, iid, close=100.0)

    # Seed 3 alerts
    today = date.today().isoformat()
    for i in range(3):
        conn.execute(
            "INSERT INTO alerts (alert_date, alert_type, severity, instrument_id, message) "
            "VALUES (?, 'risk', 'warning', ?, ?)",
            (today, iid, f"警告 {i}"),
        )
    conn.commit()

    records = compute_position_health(db_path=tmp_db)
    assert len(records) == 1
    r = records[0]
    assert r.alert_count == 3
    assert r.health_label == "act"   # 3 alerts → act


# ═══════════════════════════════════════════════════════════════════════════════
# save_position_health — upsert
# ═══════════════════════════════════════════════════════════════════════════════

def test_save_position_health_writes_rows(tmp_db, conn):
    iid = _seed_instrument(conn)
    today = date.today().isoformat()
    r = PositionHealthRecord(
        calc_date=today, instrument_id=iid, code="600519", name="贵州茅台", tranche="C",
        health_score=72.5, health_label="healthy",
        pnl_pct=0.08, drawdown_pct=-0.03,
        weight_total=0.20, weight_tranche=0.45,
        risk_contrib_pct=0.15, thesis_score=4.0, alert_count=0,
        suggested_action="维持当前策略", evidence={},
    )
    count = save_position_health([r], db_path=tmp_db)
    assert count == 1
    row = connect(tmp_db).execute(
        "SELECT * FROM position_health WHERE calc_date=? AND instrument_id=?",
        (today, iid),
    ).fetchone()
    assert row["health_label"] == "healthy"
    assert abs(float(row["health_score"]) - 72.5) < 0.1


def test_save_position_health_upsert(tmp_db, conn):
    iid = _seed_instrument(conn)
    today = date.today().isoformat()

    def _rec(score, label):
        return PositionHealthRecord(
            calc_date=today, instrument_id=iid, code="600519", name="贵州茅台", tranche="C",
            health_score=score, health_label=label,
            pnl_pct=0.0, drawdown_pct=None, weight_total=None, weight_tranche=None,
            risk_contrib_pct=None, thesis_score=None, alert_count=0,
            suggested_action=None, evidence={},
        )

    save_position_health([_rec(60.0, "watch")], db_path=tmp_db)
    save_position_health([_rec(35.0, "review")], db_path=tmp_db)

    rows = connect(tmp_db).execute(
        "SELECT COUNT(*) AS n FROM position_health WHERE calc_date=? AND instrument_id=?",
        (today, iid),
    ).fetchone()
    assert rows["n"] == 1

    row = connect(tmp_db).execute(
        "SELECT health_label FROM position_health WHERE calc_date=? AND instrument_id=?",
        (today, iid),
    ).fetchone()
    assert row["health_label"] == "review"   # overwritten


# ═══════════════════════════════════════════════════════════════════════════════
# build_health_summary
# ═══════════════════════════════════════════════════════════════════════════════

def test_build_health_summary_empty():
    msg = build_health_summary([])
    assert "暂无" in msg


def test_build_health_summary_act_warning(tmp_db, conn):
    iid = _seed_instrument(conn)
    today = date.today().isoformat()
    records = [
        PositionHealthRecord(
            calc_date=today, instrument_id=iid, code="600519", name="贵州茅台", tranche="C",
            health_score=15.0, health_label="act",
            pnl_pct=-0.22, drawdown_pct=-0.25,
            weight_total=0.30, weight_tranche=0.60,
            risk_contrib_pct=0.45, thesis_score=1.5, alert_count=2,
            suggested_action="立即减仓", evidence={},
        )
    ]
    msg = build_health_summary(records)
    assert "act" in msg
    assert "立即" in msg or "处理" in msg
