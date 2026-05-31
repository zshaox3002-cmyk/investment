"""Unit tests for core/db.py — init_db idempotency and stop_rules trigger logic."""
import sqlite3
import tempfile
from pathlib import Path

import pytest

from investment.core.db import init_db, list_tables, connect
from investment.rules.checker import check_stop_rules


# ── Test 1: init_db idempotency ───────────────────────────────────────────

def test_init_db_creates_tables():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        version = init_db(db_path)
        assert version >= 1
        tables = list_tables(db_path)
        assert "instruments" in tables
        assert "holdings" in tables
        assert "alerts" in tables
        assert "stop_rules" in tables
    finally:
        db_path.unlink(missing_ok=True)


def test_init_db_idempotent():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        v1 = init_db(db_path)
        v2 = init_db(db_path)  # second call must not raise
        assert v1 == v2
        tables_after = list_tables(db_path)
        assert "instruments" in tables_after
    finally:
        db_path.unlink(missing_ok=True)


# ── Test 2: stop_rules trigger logic ─────────────────────────────────────

def _setup_stop_rules_db(db_path: Path) -> None:
    """Seed a minimal DB with one instrument and four GRID_SELL stop rules."""
    init_db(db_path)
    conn = connect(db_path)
    conn.execute(
        "INSERT INTO instruments(code, market, name, asset_class, tranche) VALUES (?,?,?,?,?)",
        ("600219", "A", "南山铝业", "STOCK", "C"),
    )
    iid = conn.execute(
        "SELECT id FROM instruments WHERE code='600219'"
    ).fetchone()["id"]

    # stop_rules requires a decision_id (NOT NULL FK) — seed a stub decision
    conn.execute(
        """INSERT INTO decisions
           (decision_no, decision_date, decision_type, primary_instrument_id,
            body_path, ic_memo_passed, status)
           VALUES (?,?,?,?,?,?,?)""",
        ("decision_001", "2026-05-27", "REDUCE", iid, "trades/decision_001.md", 1, "active"),
    )
    did = conn.execute("SELECT id FROM decisions WHERE decision_no='decision_001'").fetchone()["id"]

    # Four GRID_SELL rules at 5.81 / 6.34 / 6.86 / 7.39
    for price in (5.81, 6.34, 6.86, 7.39):
        conn.execute(
            """INSERT INTO stop_rules
               (decision_id, instrument_id, rule_type, trigger_kind,
                trigger_value, action, status, priority)
               VALUES (?,?,?,?,?,?,?,?)""",
            (did, iid, "GRID_SELL", "PRICE_ABS", price, "REDUCE", "armed", 2),
        )
    conn.commit()
    conn.close()


def test_stop_rules_none_triggered():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        _setup_stop_rules_db(db_path)
        conn = connect(db_path)
        positions = [{"code": "600219", "current_price": 5.50}]
        alerts = check_stop_rules(conn, positions)
        conn.close()
        assert alerts == []
    finally:
        db_path.unlink(missing_ok=True)


def test_stop_rules_first_triggered():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        _setup_stop_rules_db(db_path)
        conn = connect(db_path)
        positions = [{"code": "600219", "current_price": 5.85}]
        alerts = check_stop_rules(conn, positions)
        conn.close()
        # Only the 5.81 rule fires; 6.34/6.86/7.39 do not
        assert len(alerts) == 1
        assert alerts[0]["type"] == "stop_rule_grid_sell"
        assert alerts[0]["code"] == "600219"
    finally:
        db_path.unlink(missing_ok=True)


def test_stop_rules_all_triggered():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        _setup_stop_rules_db(db_path)
        conn = connect(db_path)
        positions = [{"code": "600219", "current_price": 8.00}]
        alerts = check_stop_rules(conn, positions)
        conn.close()
        assert len(alerts) == 4
    finally:
        db_path.unlink(missing_ok=True)


def test_stop_rules_missing_price():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    try:
        _setup_stop_rules_db(db_path)
        conn = connect(db_path)
        # Position list doesn't include 600219 → no price → no alert
        alerts = check_stop_rules(conn, [])
        conn.close()
        assert alerts == []
    finally:
        db_path.unlink(missing_ok=True)
