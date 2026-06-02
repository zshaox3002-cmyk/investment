"""Smoke tests for Phase 5: FastAPI endpoints."""
from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from investment.core.db import init_db
from investment.core.sql_migrator import run_sql_migrations
from investment.migration._10_agent_v3 import run as run_m10


# ── Override DB path for tests ────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def patch_connect(tmp_path, monkeypatch):
    """Redirect all DB connections to a temp DB for the duration of each test."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    run_sql_migrations(db_path=db_path)
    run_m10(db_path=db_path)

    import investment.core.db as db_module
    import investment.web.app as app_module
    from investment.core.db import connect as real_connect, transaction as real_tx
    from pathlib import Path
    import contextlib

    def _patched_connect(path=None):
        return real_connect(db_path)

    @contextlib.contextmanager
    def _patched_tx(path=None):
        with real_tx(db_path) as conn:
            yield conn

    monkeypatch.setattr(db_module, "connect", _patched_connect)
    monkeypatch.setattr(db_module, "transaction", _patched_tx)
    # Also patch the module-level imports inside web.app
    monkeypatch.setattr(app_module, "connect", _patched_connect)
    monkeypatch.setattr(app_module, "transaction", _patched_tx)

    return db_path


@pytest.fixture
def client(patch_connect):
    from investment.web.app import app
    return TestClient(app)


def _seed_instrument(client_db, code="600519", name="贵州茅台", tranche="C"):
    from investment.core.db import connect as real_connect, transaction as real_tx
    with real_connect(client_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO instruments (code, market, name, asset_class, tranche) "
            "VALUES (?, 'A', ?, 'STOCK', ?)",
            (code, name, tranche),
        )
        conn.commit()
    return real_connect(client_db).execute(
        "SELECT id FROM instruments WHERE code=?", (code,)
    ).fetchone()["id"]


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/operating-state/today
# ═══════════════════════════════════════════════════════════════════════════════

def test_operating_state_no_data(client):
    resp = client.get("/api/operating-state/today")
    assert resp.status_code == 200
    data = resp.json()
    assert "health_light" in data
    assert data["health_light"] == "unknown"   # no agent run yet


def test_operating_state_with_data(client, patch_connect):
    today = date.today().isoformat()
    from investment.core.db import connect as rc
    conn = rc(patch_connect)
    conn.execute(
        "INSERT INTO daily_operating_state "
        "(state_date, health_light, state_label, executable_count, confirm_count, "
        "monitor_count, blocked_count, critical_count, warning_count, top_message, evidence_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (today, "yellow", "有待确认任务", 1, 2, 3, 0, 0, 1, "测试消息", "{}"),
    )
    conn.commit()
    conn.close()

    resp = client.get("/api/operating-state/today")
    assert resp.status_code == 200
    data = resp.json()
    assert data["health_light"] == "yellow"
    assert data["executable_count"] == 1
    assert data["confirm_count"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/tasks
# ═══════════════════════════════════════════════════════════════════════════════

def test_tasks_empty(client):
    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_tasks_with_data(client, patch_connect):
    today = date.today().isoformat()
    from investment.core.db import connect as rc
    conn = rc(patch_connect)
    conn.execute(
        "INSERT INTO task_calendar (title, category, due_date, priority, decision_layer, "
        "source_module, source_ref, suggested_command) "
        "VALUES ('测试任务', 'custom', ?, 'high', 'executable', 'test', 'ref1', 'inv version')",
        (today,),
    )
    conn.commit()
    conn.close()

    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    tasks = resp.json()
    assert len(tasks) >= 1
    assert tasks[0]["title"] == "测试任务"
    assert "evidence" in tasks[0]


def test_tasks_filter_by_layer(client, patch_connect):
    today = date.today().isoformat()
    from investment.core.db import connect as rc
    conn = rc(patch_connect)
    conn.execute(
        "INSERT INTO task_calendar (title, category, due_date, priority, decision_layer, "
        "source_module, source_ref) "
        "VALUES ('可执行', 'custom', ?, 'high', 'executable', 't', 'exe1')",
        (today,),
    )
    conn.execute(
        "INSERT INTO task_calendar (title, category, due_date, priority, decision_layer, "
        "source_module, source_ref) "
        "VALUES ('仅监控', 'custom', ?, 'low', 'monitor', 't', 'mon1')",
        (today,),
    )
    conn.commit()
    conn.close()

    resp = client.get("/api/tasks?layer=executable")
    tasks = resp.json()
    assert all(t["decision_layer"] == "executable" for t in tasks)

    resp2 = client.get("/api/tasks?layer=monitor")
    tasks2 = resp2.json()
    assert all(t["decision_layer"] == "monitor" for t in tasks2)


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/portfolio/health
# ═══════════════════════════════════════════════════════════════════════════════

def test_portfolio_health_empty(client):
    resp = client.get("/api/portfolio/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "holdings" in data
    assert "calc_date" in data


def test_portfolio_health_with_position_health(client, patch_connect):
    from investment.core.db import connect as rc
    conn = rc(patch_connect)
    iid = conn.execute(
        "INSERT INTO instruments (code, market, name, asset_class, tranche) "
        "VALUES ('600519','A','贵州茅台','STOCK','C')"
    ).lastrowid
    conn.commit()

    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO position_health "
        "(calc_date, instrument_id, health_score, health_label, pnl_pct, "
        "drawdown_pct, weight_total, weight_tranche, risk_contrib_pct, "
        "thesis_score, alert_count, suggested_action, evidence_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
        (today, iid, 72.5, "healthy", 0.08, -0.03, 0.20, 0.45, 0.15, 4.0, 0, "维持", "{}"),
    )
    conn.commit()
    conn.close()

    resp = client.get("/api/portfolio/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "position_health"
    assert len(data["holdings"]) == 1
    assert data["holdings"][0]["health_label"] == "healthy"


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/risk/summary
# ═══════════════════════════════════════════════════════════════════════════════

def test_risk_summary_empty(client):
    resp = client.get("/api/risk/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "metrics" in data
    assert "rule_breaches" in data
    assert isinstance(data["rule_breaches"], list)


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/goals/progress
# ═══════════════════════════════════════════════════════════════════════════════

def test_goals_progress_empty(client):
    resp = client.get("/api/goals/progress")
    assert resp.status_code == 200
    data = resp.json()
    assert "latest" in data
    assert "series" in data
    assert data["latest"] == {}


def test_goals_progress_with_data(client, patch_connect):
    today = date.today().isoformat()
    from investment.core.db import connect as rc
    conn = rc(patch_connect)
    conn.execute(
        "INSERT INTO goal_progress "
        "(progress_date, target_annual_return, actual_ytd_return, created_at) "
        "VALUES (?, 0.10, 0.05, CURRENT_TIMESTAMP)",
        (today,),
    )
    conn.commit()
    conn.close()

    resp = client.get("/api/goals/progress")
    assert resp.status_code == 200
    data = resp.json()
    assert abs(float(data["latest"]["actual_ytd_return"]) - 0.05) < 1e-9


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/research/tasks
# ═══════════════════════════════════════════════════════════════════════════════

def test_research_tasks(client):
    resp = client.get("/api/research/tasks")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/data-quality/issues
# ═══════════════════════════════════════════════════════════════════════════════

def test_data_quality_empty_db(client):
    resp = client.get("/api/data-quality/issues")
    assert resp.status_code == 200
    data = resp.json()
    assert "issues" in data
    assert "total" in data


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/tasks/{id}/complete|snooze|skip
# ═══════════════════════════════════════════════════════════════════════════════

def _seed_task(patch_connect, layer="executable"):
    today = date.today().isoformat()
    from investment.core.db import connect as rc
    conn = rc(patch_connect)
    tid = conn.execute(
        "INSERT INTO task_calendar (title, category, due_date, priority, decision_layer, "
        "source_module, source_ref) VALUES ('测试', 'custom', ?, 'high', ?, 't', 'r1')",
        (today, layer),
    ).lastrowid
    conn.commit()
    conn.close()
    return tid


def test_complete_task(client, patch_connect):
    tid = _seed_task(patch_connect)
    resp = client.post(f"/api/tasks/{tid}/complete", json={"notes": "done"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"

    from investment.core.db import connect as rc
    row = rc(patch_connect).execute(
        "SELECT status FROM task_calendar WHERE id=?", (tid,)
    ).fetchone()
    assert row["status"] == "done"


def test_snooze_task(client, patch_connect):
    tid = _seed_task(patch_connect)
    resp = client.post(f"/api/tasks/{tid}/snooze", json={"snooze_days": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert "new_due" in data


def test_skip_task(client, patch_connect):
    tid = _seed_task(patch_connect)
    resp = client.post(f"/api/tasks/{tid}/skip", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"


def test_complete_nonexistent_task(client):
    resp = client.post("/api/tasks/99999/complete", json={})
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# GET / (index.html)
# ═══════════════════════════════════════════════════════════════════════════════

def test_root_returns_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    # Should return HTML
    assert "text/html" in resp.headers.get("content-type", "")
