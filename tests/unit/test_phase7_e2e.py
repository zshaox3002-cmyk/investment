"""Phase 7 tests: data quality guard, suppress API, enhanced endpoints, E2E."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from investment.core.db import connect, init_db
from investment.core.sql_migrator import run_sql_migrations
from investment.migration._10_agent_v3 import run as run_m10
from investment.agent_orchestrator.task_generator import (
    apply_data_quality_guard,
    _upsert_task,
)
from investment.agent_orchestrator.runner import OrchestratorResult, ModuleResult


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


@pytest.fixture(autouse=True)
def patch_connect(tmp_db, monkeypatch):
    import investment.core.db as db_module
    import investment.web.app as app_module
    from investment.core.db import connect as rc, transaction as rtx
    import contextlib

    def _c(path=None):
        return rc(tmp_db)

    @contextlib.contextmanager
    def _tx(path=None):
        with rtx(tmp_db) as conn:
            yield conn

    monkeypatch.setattr(db_module, "connect", _c)
    monkeypatch.setattr(db_module, "transaction", _tx)
    monkeypatch.setattr(app_module, "connect", _c)
    monkeypatch.setattr(app_module, "transaction", _tx)
    return tmp_db


@pytest.fixture
def client(patch_connect):
    from investment.web.app import app
    return TestClient(app)


def _seed_instrument(conn, code="600519", name="贵州茅台", tranche="C"):
    conn.execute(
        "INSERT OR IGNORE INTO instruments (code, market, name, asset_class, tranche) "
        "VALUES (?,?,?,'STOCK',?)", (code, "A", name, tranche)
    )
    conn.commit()
    return conn.execute("SELECT id FROM instruments WHERE code=?", (code,)).fetchone()["id"]


def _seed_holding(conn, iid, shares=100, cost=100.0):
    today = date.today().isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO holdings (instrument_id, effective_date, shares, cost_price, source) "
        "VALUES (?,?,?,?,'manual')", (iid, today, shares, cost)
    )
    conn.commit()


def _seed_quote(conn, iid, close=100.0, qdate=None):
    d = qdate or date.today().isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO quotes (instrument_id, quote_date, close, fetched_at) "
        "VALUES (?,?,?,CURRENT_TIMESTAMP)", (iid, d, close)
    )
    conn.commit()


def _empty_orch(tmp_db) -> OrchestratorResult:
    today = date.today().isoformat()
    r = OrchestratorResult(mode="premarket", run_date=today, started_at=today+"T08:00:00Z")
    for attr in ["exec_monitor","position","risk","attribution","calendar","causal"]:
        setattr(r, attr, ModuleResult(attr, ok=True, data=None))
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# Data quality guard
# ═══════════════════════════════════════════════════════════════════════════════

def test_guard_no_holdings_no_op(tmp_db):
    count = apply_data_quality_guard(db_path=tmp_db)
    assert count == 0


def test_guard_stale_quote_blocks_task(tmp_db, conn):
    """Task for code with stale quote (no recent quote) should be blocked."""
    iid = _seed_instrument(conn)
    _seed_holding(conn, iid)
    # No quote seeded → stale

    today = date.today().isoformat()
    tid = _upsert_task(
        title="买入 600519", category="custom", due_date=today,
        priority="high", decision_layer="executable",
        source_module="test", source_ref="guard_test_stale",
        related_code="600519", db_path=tmp_db,
    )
    assert tid is not None

    blocked = apply_data_quality_guard(db_path=tmp_db)
    assert blocked >= 1

    row = connect(tmp_db).execute(
        "SELECT decision_layer, blocking_reason FROM task_calendar WHERE id=?", (tid,)
    ).fetchone()
    assert row["decision_layer"] == "blocked"
    assert "mock" in row["blocking_reason"].lower() or "过期" in row["blocking_reason"]


def test_guard_fresh_quote_not_blocked(tmp_db, conn):
    """Task for code with fresh quote should NOT be blocked."""
    iid = _seed_instrument(conn)
    _seed_holding(conn, iid)
    _seed_quote(conn, iid, close=95.0)  # today's quote

    today = date.today().isoformat()
    tid = _upsert_task(
        title="买入 600519", category="custom", due_date=today,
        priority="high", decision_layer="executable",
        source_module="test", source_ref="guard_test_fresh",
        related_code="600519", db_path=tmp_db,
    )

    blocked = apply_data_quality_guard(db_path=tmp_db)
    assert blocked == 0

    row = connect(tmp_db).execute(
        "SELECT decision_layer FROM task_calendar WHERE id=?", (tid,)
    ).fetchone()
    assert row["decision_layer"] == "executable"  # not degraded


def test_guard_idempotent(tmp_db, conn):
    """Running guard twice doesn't re-block already-blocked tasks."""
    iid = _seed_instrument(conn)
    _seed_holding(conn, iid)
    today = date.today().isoformat()
    _upsert_task(
        title="买入", category="custom", due_date=today, priority="high",
        decision_layer="executable", source_module="t", source_ref="idem",
        related_code="600519", db_path=tmp_db,
    )

    count1 = apply_data_quality_guard(db_path=tmp_db)
    count2 = apply_data_quality_guard(db_path=tmp_db)
    assert count1 >= 1
    assert count2 == 0  # already blocked, no change


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/data-quality/suppress
# ═══════════════════════════════════════════════════════════════════════════════

def test_suppress_creates_record(client, tmp_db, conn):
    resp = client.post("/api/data-quality/suppress", json={
        "issue_type": "mock_price", "code": "600519", "reason": "已手动确认"
    })
    assert resp.status_code == 200
    d = resp.json()
    assert d["ok"] is True
    assert d["code"] == "600519"

    row = connect(tmp_db).execute(
        "SELECT * FROM task_calendar WHERE action_type='dq_suppress' AND related_code='600519'"
    ).fetchone()
    assert row is not None
    assert row["status"] == "done"


def test_suppress_restores_blocked_task(client, tmp_db, conn):
    """Suppressing a code restores its blocked tasks to confirm."""
    iid = _seed_instrument(conn)
    _seed_holding(conn, iid)
    today = date.today().isoformat()

    # Create a blocked task for this code
    conn.execute(
        "INSERT INTO task_calendar (title, category, due_date, priority, decision_layer, "
        "related_code, source_module, source_ref, blocking_reason, status) "
        "VALUES ('买入','custom',?,?,'blocked','600519','dq','r1','数据过期','pending')",
        (today, "high"),
    )
    conn.commit()

    resp = client.post("/api/data-quality/suppress", json={
        "issue_type": "stale_quote", "code": "600519"
    })
    assert resp.status_code == 200

    row = connect(tmp_db).execute(
        "SELECT decision_layer, blocking_reason FROM task_calendar "
        "WHERE related_code='600519' AND status='pending'"
    ).fetchone()
    assert row["decision_layer"] == "confirm"  # restored
    assert row["blocking_reason"] is None


def test_suppress_idempotent(client, tmp_db):
    """Double-suppressing the same code should not error."""
    body = {"issue_type": "mock_price", "code": "600519"}
    resp1 = client.post("/api/data-quality/suppress", json=body)
    resp2 = client.post("/api/data-quality/suppress", json=body)
    assert resp1.status_code == 200
    assert resp2.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# Enhanced API: risk/summary — pseudo_div
# ═══════════════════════════════════════════════════════════════════════════════

def test_risk_summary_pseudo_div_field(client):
    resp = client.get("/api/risk/summary")
    assert resp.status_code == 200
    d = resp.json()
    assert "pseudo_div" in d
    # When no data: pseudo_div is None
    assert d["pseudo_div"] is None or isinstance(d["pseudo_div"], dict)


def test_risk_summary_risk_contributions(client):
    resp = client.get("/api/risk/summary")
    assert resp.status_code == 200
    d = resp.json()
    assert "risk_contributions" in d
    assert isinstance(d["risk_contributions"], list)


# ═══════════════════════════════════════════════════════════════════════════════
# Enhanced API: goals/progress — attribution
# ═══════════════════════════════════════════════════════════════════════════════

def test_goals_progress_has_attribution(client):
    resp = client.get("/api/goals/progress")
    assert resp.status_code == 200
    d = resp.json()
    assert "attribution" in d
    assert isinstance(d["attribution"], dict)


def test_goals_progress_with_data(client, tmp_db, conn):
    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO goal_progress "
        "(progress_date, target_annual_return, actual_ytd_return, "
        "target_ytd_return, progress_gap, required_return_remaining, "
        "max_drawdown, risk_budget_used, benchmark_return_ytd, portfolio_value, created_at) "
        "VALUES (?,0.10,-0.05,0.042,-0.092,0.41,-0.08,0.40,-0.01,500000,CURRENT_TIMESTAMP)",
        (today,),
    )
    conn.commit()

    resp = client.get("/api/goals/progress")
    d = resp.json()
    assert d["latest"]["actual_ytd_return"] == pytest.approx(-0.05)
    assert d["latest"]["required_return_remaining"] == pytest.approx(0.41)
    assert d["latest"]["benchmark_return_ytd"] == pytest.approx(-0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# Enhanced API: portfolio/health — tranches
# ═══════════════════════════════════════════════════════════════════════════════

def test_portfolio_health_has_tranches(client):
    resp = client.get("/api/portfolio/health")
    assert resp.status_code == 200
    d = resp.json()
    assert "tranches" in d
    assert isinstance(d["tranches"], list)


def test_portfolio_health_tranche_denominator_label(client, tmp_db, conn):
    """When holdings exist, tranches should include denominator_label."""
    iid = _seed_instrument(conn)
    _seed_holding(conn, iid)
    _seed_quote(conn, iid, close=95.0)
    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO position_health "
        "(calc_date,instrument_id,health_score,health_label,pnl_pct,drawdown_pct,"
        "weight_total,weight_tranche,risk_contrib_pct,thesis_score,alert_count,"
        "suggested_action,evidence_json,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
        (today, iid, 50.0, "watch", -0.05, -0.05, 0.5, 0.5, 0.2, None, 0, None, "{}"),
    )
    conn.commit()

    resp = client.get("/api/portfolio/health")
    d = resp.json()
    if d["tranches"]:
        t = d["tranches"][0]
        assert "denominator_label" in t
        assert "ABC" in t["denominator_label"]


# ═══════════════════════════════════════════════════════════════════════════════
# E2E: agent run → writes to DB → API returns data
# ═══════════════════════════════════════════════════════════════════════════════

def test_e2e_task_generation_to_api(tmp_db, conn, client):
    """E2E: generate_tasks writes task_calendar → GET /api/tasks returns it."""
    today = date.today().isoformat()

    # Seed minimal data for position_monitor to detect rebalance
    orch = _empty_orch(tmp_db)
    pos = MagicMock()
    pos.rebalance_needed = True
    pos.rule_breaches = []
    pos.holdings = []
    orch.position = ModuleResult("position", ok=True, data=pos)

    from investment.agent_orchestrator.task_generator import generate_tasks
    ids = generate_tasks(orch, db_path=tmp_db)
    assert len(ids) >= 1

    # API should return the generated task
    resp = client.get(f"/api/tasks?layer=confirm")
    assert resp.status_code == 200
    tasks = resp.json()
    task_ids = [t["id"] for t in tasks]
    assert any(i in task_ids for i in ids)


def test_e2e_operating_state_to_api(tmp_db, client, conn):
    """E2E: save operating state → API returns it."""
    from investment.agent_orchestrator.operating_state import save_operating_state, OperatingState
    today = date.today().isoformat()
    state = OperatingState(
        state_date=today, health_light="yellow", state_label="测试状态",
        executable_count=1, confirm_count=2, monitor_count=3,
    )
    save_operating_state(state, db_path=tmp_db)

    resp = client.get("/api/operating-state/today")
    d = resp.json()
    assert d["health_light"] == "yellow"
    assert d["executable_count"] == 1
    assert d["state_label"] == "测试状态"


def test_e2e_goal_progress_to_api(tmp_db, client, conn):
    """E2E: save goal_progress → API returns it."""
    from investment.agent_tools.goal_engine import save_goal_progress, GoalProgressResult
    today = date.today().isoformat()
    result = GoalProgressResult(
        progress_date=today, target_annual_return=0.10, actual_ytd_return=-0.08,
        target_ytd_return=0.042, progress_gap=-0.122, required_return_remaining=0.30,
        max_drawdown=-0.10, risk_budget_used=0.50, benchmark_return_ytd=-0.02,
        portfolio_value=450000.0, insufficient_data=False, human_message="",
    )
    save_goal_progress(result, db_path=tmp_db)

    resp = client.get("/api/goals/progress")
    d = resp.json()
    assert abs(float(d["latest"]["actual_ytd_return"]) - (-0.08)) < 1e-9
    assert abs(float(d["latest"]["required_return_remaining"]) - 0.30) < 1e-9


def test_e2e_position_health_to_api(tmp_db, client, conn):
    """E2E: save position_health → API returns it."""
    from investment.agent_tools.position_health import save_position_health, PositionHealthRecord
    iid = _seed_instrument(conn)
    today = date.today().isoformat()
    records = [
        PositionHealthRecord(
            calc_date=today, instrument_id=iid, code="600519", name="贵州茅台", tranche="C",
            health_score=35.0, health_label="review",
            pnl_pct=-0.10, drawdown_pct=-0.08, weight_total=0.20, weight_tranche=0.45,
            risk_contrib_pct=0.20, thesis_score=3.0, alert_count=1,
            suggested_action="审查论点", evidence={},
        )
    ]
    save_position_health(records, db_path=tmp_db)

    resp = client.get("/api/portfolio/health")
    d = resp.json()
    assert d["source"] == "position_health"
    assert len(d["holdings"]) == 1
    assert d["holdings"][0]["health_label"] == "review"


def test_e2e_data_quality_guard_blocks_and_suppress_restores(tmp_db, client, conn):
    """E2E: stale quote → guard blocks task → suppress → task restored to confirm."""
    iid = _seed_instrument(conn)
    _seed_holding(conn, iid)
    # No quote → stale

    today = date.today().isoformat()
    from investment.agent_orchestrator.task_generator import _upsert_task
    tid = _upsert_task(
        title="买入 600519 E2E", category="custom", due_date=today,
        priority="high", decision_layer="executable",
        source_module="test", source_ref="e2e_guard",
        related_code="600519", db_path=tmp_db,
    )

    # Guard runs → task blocked
    from investment.agent_orchestrator.task_generator import apply_data_quality_guard
    apply_data_quality_guard(db_path=tmp_db)

    row = connect(tmp_db).execute(
        "SELECT decision_layer FROM task_calendar WHERE id=?", (tid,)
    ).fetchone()
    assert row["decision_layer"] == "blocked"

    # Suppress via API → task restored
    resp = client.post("/api/data-quality/suppress", json={
        "issue_type": "stale_quote", "code": "600519", "reason": "E2E test"
    })
    assert resp.status_code == 200

    row2 = connect(tmp_db).execute(
        "SELECT decision_layer FROM task_calendar WHERE id=?", (tid,)
    ).fetchone()
    assert row2["decision_layer"] == "confirm"
