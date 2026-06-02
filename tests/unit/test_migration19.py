"""Unit tests for Phase 1 (v3): migration 19 idempotency and schema correctness."""
from __future__ import annotations

import json

import pytest

from investment.core.db import connect, init_db
from investment.core.sql_migrator import run_sql_migrations
from investment.migration._10_agent_v3 import run as run_m10


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """Fresh DB with all migrations applied."""
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


# ── 1. New tables exist ────────────────────────────────────────────────────────

@pytest.mark.parametrize("table", [
    "daily_operating_state",
    "goal_progress",
    "position_health",
    "agent_run_log",
])
def test_new_tables_exist(conn, table):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    assert row is not None, f"Table {table!r} not found"


# ── 2. task_calendar new columns exist ────────────────────────────────────────

@pytest.mark.parametrize("col", [
    "source_module", "source_ref", "action_type", "decision_layer",
    "evidence_json", "blocking_reason", "suggested_command", "confidence",
])
def test_task_calendar_new_columns(conn, col):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(task_calendar)").fetchall()}
    assert col in cols, f"Column {col!r} missing from task_calendar"


# ── 3. Idempotency: running m10 twice does not error ──────────────────────────

def test_m10_idempotent(tmp_db):
    first = run_m10(db_path=tmp_db)
    second = run_m10(db_path=tmp_db)
    # first run may add columns; second run must add 0 more
    assert second == 0, "Second run of _10_agent_v3 should add 0 columns"


# ── 4. Idempotency: running SQL migration 19 twice does not error ──────────────

def test_sql_migration_19_idempotent(tmp_path):
    db_path = tmp_path / "idem.db"
    init_db(db_path)
    r1 = run_sql_migrations(db_path=db_path)
    r2 = run_sql_migrations(db_path=db_path)
    assert r2.get("19_agent_v3.sql") == "skipped", (
        "Second apply of 19_agent_v3.sql should be skipped (same checksum)"
    )


# ── 5. daily_operating_state: insert and read ─────────────────────────────────

def test_daily_operating_state_insert(conn):
    conn.execute(
        "INSERT INTO daily_operating_state "
        "(state_date, health_light, state_label, executable_count, top_message, evidence_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("2026-06-02", "yellow", "有待确认任务", 2, "需处理2项任务", json.dumps({"test": True})),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM daily_operating_state WHERE state_date='2026-06-02'"
    ).fetchone()
    assert row["health_light"] == "yellow"
    assert row["executable_count"] == 2
    assert json.loads(row["evidence_json"])["test"] is True


# ── 6. daily_operating_state: health_light CHECK constraint ───────────────────

def test_daily_operating_state_check_constraint(conn):
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO daily_operating_state (state_date, health_light, state_label) "
            "VALUES ('2026-06-03', 'blue', 'invalid')"
        )
        conn.commit()


# ── 7. goal_progress: insert and read ─────────────────────────────────────────

def test_goal_progress_insert(conn):
    conn.execute(
        "INSERT INTO goal_progress "
        "(progress_date, target_annual_return, actual_ytd_return, target_ytd_return, "
        " progress_gap, required_return_remaining, max_drawdown, portfolio_value) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("2026-06-02", 0.10, 0.03, 0.042, -0.012, 0.112, -0.05, 500000.0),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM goal_progress WHERE progress_date='2026-06-02'"
    ).fetchone()
    assert abs(row["target_annual_return"] - 0.10) < 1e-9
    assert abs(row["progress_gap"] - (-0.012)) < 1e-9


# ── 8. position_health: insert with instrument FK skipped (no instruments) ────

def test_position_health_schema(conn):
    # Just verify the table can be created/selected without error
    rows = conn.execute("SELECT * FROM position_health").fetchall()
    assert rows == []


# ── 9. agent_run_log: insert and read ─────────────────────────────────────────

def test_agent_run_log_insert(conn):
    conn.execute(
        "INSERT INTO agent_run_log (run_date, mode, started_at, status, summary) "
        "VALUES (?, ?, ?, ?, ?)",
        ("2026-06-02", "premarket", "2026-06-02T08:00:00", "completed", "test run"),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM agent_run_log WHERE run_date='2026-06-02'"
    ).fetchone()
    assert row["mode"] == "premarket"
    assert row["status"] == "completed"


# ── 10. agent_run_log: mode CHECK constraint ──────────────────────────────────

def test_agent_run_log_mode_constraint(conn):
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO agent_run_log (run_date, mode, started_at, status, summary) "
            "VALUES ('2026-06-03', 'invalid_mode', '2026-06-03T08:00:00', 'running', '')"
        )
        conn.commit()


# ── 11. task_calendar: new columns have correct defaults ──────────────────────

def test_task_calendar_column_defaults(conn):
    conn.execute(
        "INSERT INTO task_calendar (title, category, due_date) "
        "VALUES ('测试任务', 'custom', '2026-06-02')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM task_calendar WHERE title='测试任务'"
    ).fetchone()
    assert row["decision_layer"] == "monitor"
    assert json.loads(row["evidence_json"]) == {}
    assert abs(row["confidence"] - 1.0) < 1e-9


# ── 12. dedup index exists ────────────────────────────────────────────────────

def test_task_calendar_source_index_exists(conn):
    indexes = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='task_calendar'"
        ).fetchall()
    }
    assert "idx_task_calendar_source" in indexes
