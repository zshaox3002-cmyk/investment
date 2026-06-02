"""Unit tests for Phase 3: task_generator and prioritizer."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from investment.core.db import connect, init_db
from investment.core.sql_migrator import run_sql_migrations
from investment.migration._10_agent_v3 import run as run_m10
from investment.agent_orchestrator.task_generator import (
    _upsert_task,
    _from_position_monitor,
    _from_risk_engine,
    _from_exec_monitor,
    _from_trade_decisions,
    _from_theses,
    _from_calendar,
    _from_causal,
    _from_attribution,
    generate_tasks,
)
from investment.agent_orchestrator.prioritizer import (
    task_exists,
    prioritize_tasks,
    prioritize_all_pending,
    LAYER_ORDER,
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


def _seed_instrument(conn, code="600519", name="贵州茅台", tranche="C"):
    conn.execute(
        "INSERT OR IGNORE INTO instruments (code, market, name, asset_class, tranche) "
        "VALUES (?, 'A', ?, 'STOCK', ?)",
        (code, name, tranche),
    )
    conn.commit()
    return conn.execute("SELECT id FROM instruments WHERE code=?", (code,)).fetchone()["id"]


def _empty_orch(tmp_db) -> OrchestratorResult:
    today = date.today().isoformat()
    r = OrchestratorResult(mode="premarket", run_date=today, started_at=today + "T08:00:00Z")
    r.exec_monitor = ModuleResult("exec_monitor", ok=True, data=None)
    r.position     = ModuleResult("position",     ok=True, data=None)
    r.risk         = ModuleResult("risk",         ok=True, data=None)
    r.attribution  = ModuleResult("attribution",  ok=True, data=None)
    r.calendar     = ModuleResult("calendar",     ok=True, data=None)
    r.causal       = ModuleResult("causal",       ok=True, data=None)
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# _upsert_task
# ═══════════════════════════════════════════════════════════════════════════════

def test_upsert_task_creates_row(tmp_db):
    today = date.today().isoformat()
    tid = _upsert_task(
        title="测试任务", category="custom", due_date=today,
        priority="high", decision_layer="executable",
        source_module="test", source_ref="ref_001",
        suggested_command="inv version",
        db_path=tmp_db,
    )
    assert tid is not None
    conn = connect(tmp_db)
    row = conn.execute("SELECT * FROM task_calendar WHERE id=?", (tid,)).fetchone()
    conn.close()
    assert row["title"] == "测试任务"
    assert row["decision_layer"] == "executable"
    assert row["suggested_command"] == "inv version"


def test_upsert_task_dedup_same_key(tmp_db):
    today = date.today().isoformat()
    kwargs = dict(
        title="任务", category="custom", due_date=today,
        priority="medium", decision_layer="monitor",
        source_module="test", source_ref="ref_dup",
        db_path=tmp_db,
    )
    tid1 = _upsert_task(**kwargs)
    tid2 = _upsert_task(**kwargs)
    assert tid1 is not None
    assert tid2 is None       # dedup → skip


def test_upsert_task_different_date_allowed(tmp_db):
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    tid1 = _upsert_task(
        title="任务", category="custom", due_date=today,
        priority="low", decision_layer="info",
        source_module="test", source_ref="ref_date",
        db_path=tmp_db,
    )
    tid2 = _upsert_task(
        title="任务", category="custom", due_date=tomorrow,
        priority="low", decision_layer="info",
        source_module="test", source_ref="ref_date",
        db_path=tmp_db,
    )
    assert tid1 is not None
    assert tid2 is not None  # different date → new row


# ═══════════════════════════════════════════════════════════════════════════════
# task_exists
# ═══════════════════════════════════════════════════════════════════════════════

def test_task_exists_false_initially(tmp_db):
    assert not task_exists("mod", "ref", date.today().isoformat(), db_path=tmp_db)


def test_task_exists_true_after_insert(tmp_db):
    today = date.today().isoformat()
    _upsert_task(
        title="x", category="custom", due_date=today,
        priority="low", decision_layer="info",
        source_module="mod", source_ref="ref_x",
        db_path=tmp_db,
    )
    assert task_exists("mod", "ref_x", today, db_path=tmp_db)


def test_task_exists_false_after_done(tmp_db, conn):
    today = date.today().isoformat()
    tid = _upsert_task(
        title="y", category="custom", due_date=today,
        priority="low", decision_layer="monitor",
        source_module="mod", source_ref="ref_done",
        db_path=tmp_db,
    )
    conn.execute("UPDATE task_calendar SET status='done' WHERE id=?", (tid,))
    conn.commit()
    # done tasks are excluded from dedup — new task can be created
    tid2 = _upsert_task(
        title="y", category="custom", due_date=today,
        priority="low", decision_layer="monitor",
        source_module="mod", source_ref="ref_done",
        db_path=tmp_db,
    )
    assert tid2 is not None  # not deduplicated against done row


# ═══════════════════════════════════════════════════════════════════════════════
# _from_position_monitor
# ═══════════════════════════════════════════════════════════════════════════════

def test_from_position_monitor_rebalance(tmp_db):
    pos = MagicMock()
    pos.rebalance_needed = True
    pos.rule_breaches = []
    pos.holdings = []
    today = date.today().isoformat()
    ids = _from_position_monitor(pos, today, tmp_db)
    assert len(ids) == 1
    conn = connect(tmp_db)
    row = conn.execute("SELECT * FROM task_calendar WHERE id=?", (ids[0],)).fetchone()
    conn.close()
    assert row["decision_layer"] == "confirm"
    assert row["source_module"] == "position_monitor"


def test_from_position_monitor_rebalance_idempotent(tmp_db):
    pos = MagicMock()
    pos.rebalance_needed = True
    pos.rule_breaches = []
    pos.holdings = []
    today = date.today().isoformat()
    ids1 = _from_position_monitor(pos, today, tmp_db)
    ids2 = _from_position_monitor(pos, today, tmp_db)
    assert len(ids1) == 1
    assert len(ids2) == 0   # dedup


def test_from_position_monitor_drawdown_review(tmp_db):
    pos = MagicMock()
    pos.rebalance_needed = False
    pos.rule_breaches = []
    h = MagicMock()
    h.pnl_pct = -0.20
    h.code = "600519"
    h.name = "贵州茅台"
    h.market_value = 100_000
    pos.holdings = [h]
    today = date.today().isoformat()
    ids = _from_position_monitor(pos, today, tmp_db)
    assert len(ids) == 1
    conn = connect(tmp_db)
    row = conn.execute("SELECT * FROM task_calendar WHERE id=?", (ids[0],)).fetchone()
    conn.close()
    assert "回撤" in row["title"]
    assert row["related_code"] == "600519"
    assert row["decision_layer"] == "confirm"


# ═══════════════════════════════════════════════════════════════════════════════
# _from_risk_engine
# ═══════════════════════════════════════════════════════════════════════════════

def test_from_risk_pseudo_div(tmp_db):
    risk = MagicMock()
    risk.pseudo_div = MagicMock(
        detected=True, concentrated_theme="科技",
        top_contributor_code="000001", top_contributor_pct=0.45,
    )
    risk.high_correlations = []
    today = date.today().isoformat()
    ids = _from_risk_engine(risk, today, tmp_db)
    assert len(ids) == 1
    conn = connect(tmp_db)
    row = conn.execute("SELECT * FROM task_calendar WHERE id=?", (ids[0],)).fetchone()
    conn.close()
    assert row["source_module"] == "risk_engine"
    assert row["decision_layer"] == "confirm"


def test_from_risk_high_corr(tmp_db):
    risk = MagicMock()
    risk.pseudo_div = MagicMock(detected=False)
    risk.high_correlations = [
        {"code_a": "600519", "code_b": "000858", "name_a": "茅台", "name_b": "五粮液", "corr": 0.87}
    ]
    today = date.today().isoformat()
    ids = _from_risk_engine(risk, today, tmp_db)
    assert len(ids) == 1
    conn = connect(tmp_db)
    row = conn.execute("SELECT * FROM task_calendar WHERE id=?", (ids[0],)).fetchone()
    conn.close()
    assert row["decision_layer"] == "monitor"
    assert "高相关" in row["title"]


# ═══════════════════════════════════════════════════════════════════════════════
# _from_exec_monitor
# ═══════════════════════════════════════════════════════════════════════════════

def test_from_exec_monitor_trigger(tmp_db):
    tool = MagicMock()
    tool.data = {"triggered_rules": ["止损规则 600519 @1800"]}
    tool.human_message = ""
    today = date.today().isoformat()
    ids = _from_exec_monitor(tool, today, tmp_db)
    assert len(ids) == 1
    conn = connect(tmp_db)
    row = conn.execute("SELECT * FROM task_calendar WHERE id=?", (ids[0],)).fetchone()
    conn.close()
    assert row["decision_layer"] == "executable"
    assert row["source_module"] == "exec_monitor"


def test_from_exec_monitor_no_trigger(tmp_db):
    tool = MagicMock()
    tool.data = {"triggered_rules": []}
    today = date.today().isoformat()
    ids = _from_exec_monitor(tool, today, tmp_db)
    assert ids == []


# ═══════════════════════════════════════════════════════════════════════════════
# _from_trade_decisions
# ═══════════════════════════════════════════════════════════════════════════════

def test_from_trade_decisions_cooldown_expired(tmp_db, conn):
    iid = _seed_instrument(conn)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    conn.execute(
        "INSERT INTO decisions (decision_no, decision_date, decision_type, body_path, "
        "cooling_until, status, primary_instrument_id) "
        "VALUES ('decision_001', ?, 'NEW', '/tmp/d001.md', ?, 'active', ?)",
        (date.today().isoformat(), yesterday, iid),
    )
    conn.commit()
    today = date.today().isoformat()
    ids = _from_trade_decisions(today, tmp_db)
    assert len(ids) >= 1
    row = connect(tmp_db).execute(
        "SELECT * FROM task_calendar WHERE source_module='trade_decisions' AND action_type='cooldown_expired'"
    ).fetchone()
    assert row is not None
    assert row["decision_layer"] == "executable"


def test_from_trade_decisions_still_cooling(tmp_db, conn):
    iid = _seed_instrument(conn, code="000858", name="五粮液")
    future = (date.today() + timedelta(days=3)).isoformat()
    conn.execute(
        "INSERT INTO decisions (decision_no, decision_date, decision_type, body_path, "
        "cooling_until, status, primary_instrument_id) "
        "VALUES ('decision_002', ?, 'ADD', '/tmp/d002.md', ?, 'active', ?)",
        (date.today().isoformat(), future, iid),
    )
    conn.commit()
    today = date.today().isoformat()
    ids = _from_trade_decisions(today, tmp_db)
    assert len(ids) >= 1
    row = connect(tmp_db).execute(
        "SELECT * FROM task_calendar WHERE source_module='trade_decisions' AND action_type='cooldown_pending'"
    ).fetchone()
    assert row["decision_layer"] == "monitor"
    assert row["blocking_reason"] is not None


# ═══════════════════════════════════════════════════════════════════════════════
# _from_theses
# ═══════════════════════════════════════════════════════════════════════════════

def test_from_theses_stale(tmp_db, conn):
    iid = _seed_instrument(conn)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO theses (instrument_id, version, body_path, next_review_date, updated_at) "
        "VALUES (?, '1.0', '/tmp/t.md', ?, ?)",
        (iid, yesterday, yesterday),
    )
    conn.commit()
    today = date.today().isoformat()
    ids = _from_theses(today, tmp_db)
    assert len(ids) == 1
    conn2 = connect(tmp_db)
    row = conn2.execute("SELECT * FROM task_calendar WHERE source_module='theses'").fetchone()
    conn2.close()
    assert "论点" in row["title"]
    assert row["related_code"] == "600519"


def test_from_theses_not_stale(tmp_db, conn):
    iid = _seed_instrument(conn)
    future = (date.today() + timedelta(days=30)).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO theses (instrument_id, version, body_path, next_review_date, updated_at) "
        "VALUES (?, '1.0', '/tmp/t.md', ?, ?)",
        (iid, future, future),
    )
    conn.commit()
    today = date.today().isoformat()
    ids = _from_theses(today, tmp_db)
    assert ids == []


# ═══════════════════════════════════════════════════════════════════════════════
# _from_causal
# ═══════════════════════════════════════════════════════════════════════════════

def test_from_causal_actionable(tmp_db):
    causal = MagicMock()
    ins = MagicMock()
    ins.holding_code = "600519"
    ins.credibility_tier = "A"
    ins.direction_label = "利空"
    ins.narrative = "关税政策收紧影响出口"
    ins.assessment_id = 42
    causal.actionable = [ins]
    today = date.today().isoformat()
    ids = _from_causal(causal, today, tmp_db)
    assert len(ids) == 1
    row = connect(tmp_db).execute(
        "SELECT * FROM task_calendar WHERE source_module='causal'"
    ).fetchone()
    assert row["decision_layer"] == "confirm"
    assert row["confidence"] == pytest.approx(0.9)


# ═══════════════════════════════════════════════════════════════════════════════
# _from_attribution
# ═══════════════════════════════════════════════════════════════════════════════

def test_from_attribution_underperf(tmp_db):
    attr = MagicMock()
    attr.excess_return = -0.05
    attr.insufficient_data = False
    attr.total_return = 0.01
    attr.benchmark_return = 0.06
    today = date.today().isoformat()
    ids = _from_attribution(attr, today, tmp_db)
    assert len(ids) == 1
    row = connect(tmp_db).execute(
        "SELECT * FROM task_calendar WHERE source_module='attribution'"
    ).fetchone()
    assert "业绩归因" in row["title"]


def test_from_attribution_ok_no_task(tmp_db):
    attr = MagicMock()
    attr.excess_return = 0.02
    attr.insufficient_data = False
    today = date.today().isoformat()
    ids = _from_attribution(attr, today, tmp_db)
    assert ids == []


def test_from_attribution_insufficient_data_no_task(tmp_db):
    attr = MagicMock()
    attr.excess_return = -0.10
    attr.insufficient_data = True
    today = date.today().isoformat()
    ids = _from_attribution(attr, today, tmp_db)
    assert ids == []


# ═══════════════════════════════════════════════════════════════════════════════
# prioritize_tasks
# ═══════════════════════════════════════════════════════════════════════════════

def test_prioritize_tasks_groups_by_layer(tmp_db):
    today = date.today().isoformat()
    id_exe = _upsert_task(
        title="可执行任务", category="custom", due_date=today, priority="high",
        decision_layer="executable", source_module="t", source_ref="exe1", db_path=tmp_db,
    )
    id_conf = _upsert_task(
        title="待确认任务", category="custom", due_date=today, priority="medium",
        decision_layer="confirm", source_module="t", source_ref="conf1", db_path=tmp_db,
    )
    id_mon = _upsert_task(
        title="监控任务", category="custom", due_date=today, priority="low",
        decision_layer="monitor", source_module="t", source_ref="mon1", db_path=tmp_db,
    )

    grouped = prioritize_tasks([id_exe, id_conf, id_mon], db_path=tmp_db)
    assert len(grouped["executable"]) == 1
    assert len(grouped["confirm"]) == 1
    assert len(grouped["monitor"]) == 1
    assert grouped["executable"][0]["id"] == id_exe


def test_prioritize_tasks_sorts_within_layer(tmp_db):
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    id_low = _upsert_task(
        title="低优先级", category="custom", due_date=today, priority="low",
        decision_layer="confirm", source_module="t", source_ref="s_low", db_path=tmp_db,
    )
    id_high = _upsert_task(
        title="高优先级", category="custom", due_date=tomorrow, priority="high",
        decision_layer="confirm", source_module="t", source_ref="s_high", db_path=tmp_db,
    )
    grouped = prioritize_tasks([id_low, id_high], db_path=tmp_db)
    confirm = grouped["confirm"]
    assert confirm[0]["id"] == id_high   # high priority comes first


def test_prioritize_all_pending_returns_dict(tmp_db):
    grouped = prioritize_all_pending(db_path=tmp_db)
    assert set(grouped.keys()) == set(LAYER_ORDER)


# ═══════════════════════════════════════════════════════════════════════════════
# generate_tasks (full integration)
# ═══════════════════════════════════════════════════════════════════════════════

def test_generate_tasks_empty_orch(tmp_db):
    orch = _empty_orch(tmp_db)
    ids = generate_tasks(orch, db_path=tmp_db)
    # No data in modules → no tasks generated (except possibly from trade_decisions/theses)
    assert isinstance(ids, list)


def test_generate_tasks_idempotent(tmp_db):
    orch = _empty_orch(tmp_db)
    pos = MagicMock()
    pos.rebalance_needed = True
    pos.rule_breaches = []
    pos.holdings = []
    orch.position = ModuleResult("position", ok=True, data=pos)

    ids1 = generate_tasks(orch, db_path=tmp_db)
    ids2 = generate_tasks(orch, db_path=tmp_db)
    # Second run: same-day same-ref → no new tasks
    assert len(ids1) >= 1
    assert len(ids2) == 0


def test_generate_tasks_produces_correct_layers(tmp_db):
    today = date.today().isoformat()
    orch = _empty_orch(tmp_db)

    # Rebalance → confirm
    pos = MagicMock()
    pos.rebalance_needed = True
    pos.rule_breaches = []
    pos.holdings = []
    orch.position = ModuleResult("position", ok=True, data=pos)

    # Stop trigger → executable
    tool = MagicMock()
    tool.data = {"triggered_rules": ["止损 600519"]}
    tool.human_message = ""
    orch.exec_monitor = ModuleResult("exec_monitor", ok=True, data=tool)

    ids = generate_tasks(orch, db_path=tmp_db)
    assert len(ids) >= 2

    conn = connect(tmp_db)
    layers = {
        r["decision_layer"]
        for r in conn.execute(
            f"SELECT decision_layer FROM task_calendar WHERE id IN ({','.join('?'*len(ids))})",
            ids,
        ).fetchall()
    }
    conn.close()
    assert "executable" in layers
    assert "confirm" in layers
