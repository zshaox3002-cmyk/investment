"""Unit tests for Phase 2: operating_state health light logic and brief generation."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from investment.core.db import connect, init_db
from investment.core.sql_migrator import run_sql_migrations
from investment.migration._10_agent_v3 import run as run_m10
from investment.agent_orchestrator.operating_state import (
    OperatingState,
    compute_operating_state,
    save_operating_state,
    compute_and_save,
    _check_alerts,
    _check_rule_breaches,
    _check_stop_triggers,
    _check_rebalance,
    _check_pseudo_div,
    _check_high_corr,
    _check_overdue_tasks,
    _check_cooldown_expiry,
)
from investment.agent_orchestrator.brief import (
    DailyBrief,
    generate_brief,
    format_brief_text,
    _build_human_message,
    _get_portfolio_summary,
)
from investment.agent_orchestrator.runner import (
    OrchestratorResult,
    ModuleResult,
    _start_run_log,
    _finish_run_log,
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
    row = conn.execute("SELECT id FROM instruments WHERE code=?", (code,)).fetchone()
    return row["id"]


def _empty_orch_result(mode="premarket") -> OrchestratorResult:
    today = date.today().isoformat()
    r = OrchestratorResult(mode=mode, run_date=today, started_at=today + "T08:00:00Z")
    r.exec_monitor = ModuleResult("exec_monitor", ok=True, data=None)
    r.position     = ModuleResult("position",     ok=True, data=None)
    r.risk         = ModuleResult("risk",          ok=True, data=None)
    r.attribution  = ModuleResult("attribution",   ok=True, data=None)
    r.calendar     = ModuleResult("calendar",      ok=True, data=None)
    r.causal       = ModuleResult("causal",        ok=True, data=None)
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# Health light — RED conditions
# ═══════════════════════════════════════════════════════════════════════════════

def test_red_on_critical_alert(tmp_db, conn):
    today = date.today().isoformat()
    iid = _seed_instrument(conn)
    conn.execute(
        "INSERT INTO alerts (alert_date, alert_type, severity, instrument_id, message) "
        "VALUES (?, 'risk', 'critical', ?, '单股回撤超20%')",
        (today, iid),
    )
    conn.commit()

    orch = _empty_orch_result()
    state = compute_operating_state(orch, db_path=tmp_db)
    assert state.health_light == "red"
    assert state.critical_count >= 1


def test_red_on_urgent_rule_breach(tmp_db, conn):
    today = date.today().isoformat()
    iid = _seed_instrument(conn)
    # grace_period_expires = today (≤ today+3) → red
    conn.execute(
        "INSERT INTO rule_breaches (rule_path, instrument_id, current_value, threshold, breach_amount, status, grace_period_expires, detected_at) "
        "VALUES ('single_stock_max', ?, 0.30, 0.25, 0.05, 'active', ?, ?)",
        (iid, today, today + "T00:00:00Z"),
    )
    conn.commit()

    orch = _empty_orch_result()
    state = compute_operating_state(orch, db_path=tmp_db)
    assert state.health_light == "red"


def test_red_on_stop_trigger(tmp_db):
    orch = _empty_orch_result()
    mock_tool = MagicMock()
    mock_tool.data = {"triggered_rules": ["止损规则 #3"]}
    mock_tool.human_message = ""
    orch.exec_monitor = ModuleResult("exec_monitor", ok=True, data=mock_tool)

    state = compute_operating_state(orch, db_path=tmp_db)
    assert state.health_light == "red"


# ═══════════════════════════════════════════════════════════════════════════════
# Health light — YELLOW conditions
# ═══════════════════════════════════════════════════════════════════════════════

def test_yellow_on_warning_alert(tmp_db, conn):
    today = date.today().isoformat()
    iid = _seed_instrument(conn)
    conn.execute(
        "INSERT INTO alerts (alert_date, alert_type, severity, instrument_id, message) "
        "VALUES (?, 'risk', 'warning', ?, '仓位偏高')",
        (today, iid),
    )
    conn.commit()

    orch = _empty_orch_result()
    state = compute_operating_state(orch, db_path=tmp_db)
    assert state.health_light == "yellow"
    assert state.warning_count >= 1


def test_yellow_on_active_rule_breach(tmp_db, conn):
    today = date.today().isoformat()
    iid = _seed_instrument(conn)
    # grace_period_expires far in future (> today+3) → yellow not red
    far_future = (date.today() + timedelta(days=30)).isoformat()
    conn.execute(
        "INSERT INTO rule_breaches (rule_path, instrument_id, current_value, threshold, breach_amount, status, grace_period_expires, detected_at) "
        "VALUES ('theme_concentration', ?, 0.40, 0.35, 0.05, 'active', ?, ?)",
        (iid, far_future, today + "T00:00:00Z"),
    )
    conn.commit()

    orch = _empty_orch_result()
    state = compute_operating_state(orch, db_path=tmp_db)
    assert state.health_light == "yellow"


def test_yellow_on_overdue_task(tmp_db, conn):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    conn.execute(
        "INSERT INTO task_calendar (title, category, due_date, status) "
        "VALUES ('月度复盘', 'monthly', ?, 'overdue')",
        (yesterday,),
    )
    conn.commit()

    orch = _empty_orch_result()
    state = compute_operating_state(orch, db_path=tmp_db)
    assert state.health_light == "yellow"


def test_yellow_on_rebalance_needed(tmp_db):
    orch = _empty_orch_result()
    mock_pos = MagicMock()
    mock_pos.rebalance_needed = True
    mock_pos.tranches = []
    orch.position = ModuleResult("position", ok=True, data=mock_pos)

    state = compute_operating_state(orch, db_path=tmp_db)
    assert state.health_light == "yellow"


def test_yellow_on_pseudo_div(tmp_db):
    orch = _empty_orch_result()
    mock_risk = MagicMock()
    mock_pd = MagicMock()
    mock_pd.detected = True
    mock_pd.description = "科技板块占比过高"
    mock_risk.pseudo_div = mock_pd
    mock_risk.high_correlations = []
    orch.risk = ModuleResult("risk", ok=True, data=mock_risk)

    state = compute_operating_state(orch, db_path=tmp_db)
    assert state.health_light == "yellow"


def test_yellow_on_high_correlation(tmp_db):
    orch = _empty_orch_result()
    mock_risk = MagicMock()
    mock_pd = MagicMock()
    mock_pd.detected = False
    mock_risk.pseudo_div = mock_pd
    mock_risk.high_correlations = [
        {"code_a": "600519", "code_b": "000858", "name_a": "茅台", "name_b": "五粮液", "corr": 0.85}
    ]
    orch.risk = ModuleResult("risk", ok=True, data=mock_risk)

    state = compute_operating_state(orch, db_path=tmp_db)
    assert state.health_light == "yellow"


def test_yellow_on_cooldown_expiry(tmp_db, conn):
    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO task_calendar (title, category, due_date, related_code, status) "
        "VALUES ('冷静期到期 600519', 'cooldown', ?, '600519', 'pending')",
        (today,),
    )
    conn.commit()

    orch = _empty_orch_result()
    state = compute_operating_state(orch, db_path=tmp_db)
    assert state.health_light == "yellow"


# ═══════════════════════════════════════════════════════════════════════════════
# Health light — GREEN (baseline)
# ═══════════════════════════════════════════════════════════════════════════════

def test_green_baseline(tmp_db):
    orch = _empty_orch_result()
    state = compute_operating_state(orch, db_path=tmp_db)
    assert state.health_light == "green"
    assert state.state_label != ""


# ═══════════════════════════════════════════════════════════════════════════════
# RED beats YELLOW (precedence)
# ═══════════════════════════════════════════════════════════════════════════════

def test_red_beats_yellow(tmp_db, conn):
    today = date.today().isoformat()
    iid = _seed_instrument(conn)
    # Critical alert (→ red) AND rebalance needed (→ yellow)
    conn.execute(
        "INSERT INTO alerts (alert_date, alert_type, severity, instrument_id, message) "
        "VALUES (?, 'risk', 'critical', ?, '紧急：止损触发')",
        (today, iid),
    )
    conn.commit()

    orch = _empty_orch_result()
    mock_pos = MagicMock()
    mock_pos.rebalance_needed = True
    mock_pos.tranches = []
    orch.position = ModuleResult("position", ok=True, data=mock_pos)

    state = compute_operating_state(orch, db_path=tmp_db)
    assert state.health_light == "red"


# ═══════════════════════════════════════════════════════════════════════════════
# save_operating_state — upsert behaviour
# ═══════════════════════════════════════════════════════════════════════════════

def test_save_operating_state(tmp_db):
    today = date.today().isoformat()
    state = OperatingState(
        state_date=today, health_light="yellow", state_label="有待确认任务",
        executable_count=1, confirm_count=2,
    )
    ok = save_operating_state(state, db_path=tmp_db)
    assert ok

    conn = connect(tmp_db)
    row = conn.execute(
        "SELECT * FROM daily_operating_state WHERE state_date=?", (today,)
    ).fetchone()
    conn.close()
    assert row["health_light"] == "yellow"
    assert row["executable_count"] == 1


def test_save_operating_state_upsert(tmp_db):
    today = date.today().isoformat()
    state1 = OperatingState(state_date=today, health_light="green", state_label="初始")
    state2 = OperatingState(state_date=today, health_light="red",   state_label="更新后")

    save_operating_state(state1, db_path=tmp_db)
    save_operating_state(state2, db_path=tmp_db)

    conn = connect(tmp_db)
    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM daily_operating_state WHERE state_date=?", (today,)
    ).fetchone()
    row = conn.execute(
        "SELECT health_light FROM daily_operating_state WHERE state_date=?", (today,)
    ).fetchone()
    conn.close()
    assert rows["n"] == 1                  # no duplicate
    assert row["health_light"] == "red"    # overwritten


# ═══════════════════════════════════════════════════════════════════════════════
# Brief generation
# ═══════════════════════════════════════════════════════════════════════════════

def test_brief_green_baseline(tmp_db):
    orch = _empty_orch_result()
    state = OperatingState(
        state_date=date.today().isoformat(),
        health_light="green",
        state_label="组合状态正常",
    )
    brief = generate_brief(orch, state, db_path=tmp_db)
    assert brief.health_light == "green"
    assert "🟢" in brief.human_message or "green" in brief.human_message.lower() or "正常" in brief.human_message


def test_brief_includes_task_counts(tmp_db, conn):
    today = date.today().isoformat()
    # Seed one executable task
    conn.execute(
        "INSERT INTO task_calendar (title, category, due_date, priority, decision_layer, suggested_command) "
        "VALUES ('止损已触发 600519', 'custom', ?, 'high', 'executable', 'inv trade log 600519 -s 100 -p 1800 --side SELL')",
        (today,),
    )
    conn.commit()

    orch = _empty_orch_result()
    state = OperatingState(
        state_date=today, health_light="red", state_label="有紧急事项"
    )
    brief = generate_brief(orch, state, db_path=tmp_db)
    assert brief.executable_count >= 1
    assert len(brief.executable_tasks) >= 1
    assert brief.next_command != ""


def test_brief_portfolio_summary(tmp_db):
    mock_pos = MagicMock()
    mock_pos.total_portfolio_value = 500_000.0
    h1 = MagicMock()
    h1.pnl_pct = 0.05
    h1.market_value = 200_000.0
    h2 = MagicMock()
    h2.pnl_pct = -0.02
    h2.market_value = 300_000.0
    mock_pos.holdings = [h1, h2]

    total, pnl = _get_portfolio_summary(mock_pos)
    assert total == 500_000.0
    # weighted: (0.05*200k + -0.02*300k) / 500k = (10k - 6k) / 500k = 0.008
    assert abs(pnl - 0.008) < 1e-9


def test_brief_human_message_structure(tmp_db):
    state = OperatingState(
        state_date=date.today().isoformat(),
        health_light="yellow",
        state_label="有待确认任务",
    )
    brief = DailyBrief(
        brief_date=date.today().isoformat(),
        health_light="yellow",
        state_label="有待确认任务",
        executable_count=1,
        confirm_count=2,
        monitor_count=3,
        executable_tasks=[{"title": "减仓贵州茅台", "command": "inv trade log 600519"}],
        next_action="减仓贵州茅台",
        next_command="inv trade log 600519",
    )
    msg = _build_human_message(brief)
    assert "🟡" in msg
    assert "可执行 1" in msg
    assert "减仓贵州茅台" in msg
    assert "inv trade log 600519" in msg


# ═══════════════════════════════════════════════════════════════════════════════
# Runner — log writing
# ═══════════════════════════════════════════════════════════════════════════════

def test_runner_writes_agent_run_log(tmp_db):
    today = date.today().isoformat()
    log_id = _start_run_log("premarket", today, today + "T08:00:00Z", db_path=tmp_db)
    assert log_id is not None

    conn = connect(tmp_db)
    row = conn.execute(
        "SELECT * FROM agent_run_log WHERE id=?", (log_id,)
    ).fetchone()
    conn.close()
    assert row["status"] == "running"
    assert row["mode"] == "premarket"


def test_runner_finish_log(tmp_db):
    today = date.today().isoformat()
    log_id = _start_run_log("postmarket", today, today + "T16:00:00Z", db_path=tmp_db)

    orch = _empty_orch_result()
    _finish_run_log(log_id, today + "T16:05:00Z", "completed", orch, db_path=tmp_db)

    conn = connect(tmp_db)
    row = conn.execute("SELECT * FROM agent_run_log WHERE id=?", (log_id,)).fetchone()
    conn.close()
    assert row["status"] == "completed"
    assert row["finished_at"] is not None


# ═══════════════════════════════════════════════════════════════════════════════
# _check_* unit tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_check_alerts_empty(conn):
    critical, warning, msgs = _check_alerts(conn, date.today().isoformat())
    assert critical == 0
    assert warning == 0
    assert msgs == []


def test_check_stop_triggers_none():
    triggered, msgs = _check_stop_triggers(None)
    assert not triggered


def test_check_stop_triggers_via_human_message():
    tool = MagicMock()
    tool.data = {}
    tool.human_message = "止损规则已触发，请立即处理"
    triggered, msgs = _check_stop_triggers(tool)
    assert triggered


def test_check_rebalance_none():
    needed, msgs = _check_rebalance(None)
    assert not needed


def test_check_pseudo_div_none():
    detected, msgs = _check_pseudo_div(None)
    assert not detected


def test_check_high_corr_empty_list():
    mock_risk = MagicMock()
    mock_risk.high_correlations = []
    has_hc, msgs = _check_high_corr(mock_risk)
    assert not has_hc
