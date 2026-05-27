"""Unit tests for rules/checker.py — all branches, no DB required."""
import pytest
from investment.rules.checker import (
    check_stock_drawdown,
    check_stock_position,
    check_account_drawdown,
    check_theme_concentration,
    check_etf_drawdown,
    check_etf_drift,
    check_meituan_rsu,
)

# ── Fixtures ──────────────────────────────────────────────────────────────

RULES = {
    "stock_rules": {
        "stop_loss": {
            "level_1_alert": {"threshold": -0.10, "action": "alert_only"},
            "level_2_review": {"threshold": -0.20, "action": "trigger_ic_memo"},
            "level_3_hard": {"threshold": -0.30, "action": "force_review"},
        }
    },
    "portfolio_rules": {
        "concentration": {
            "single_stock_max": {"threshold": 0.25, "action": "force_reduce"}
        },
        "drawdown_control": {
            "level_1_alert": {"threshold": 0.10, "action": "alert_only"},
            "level_2_control": {"threshold": 0.15, "action": "force_review"},
            "level_3_hard": {"threshold": 0.20, "action": "force_reduce"},
        },
        "theme_concentration": {
            "new_energy_and_power_chain": {
                "includes": ["新能源", "电力"],
                "threshold": 0.35,
                "action": "warning",
            }
        },
    },
    "monitoring": {
        "etf_drawdown_warn": 0.20,
        "etf_drift_threshold": 0.05,
    },
}


def _pos(code, name, pnl_pct, market_value=10000, cost_total=None, industry=""):
    cost_total = cost_total if cost_total is not None else market_value / (1 + pnl_pct)
    return dict(
        code=code, name=name, pnl_pct=pnl_pct,
        market_value=market_value, cost_total=cost_total,
        industry=industry,
    )


# ── Test 1: stock drawdown — no alert ─────────────────────────────────────

def test_stock_drawdown_no_alert():
    pos = [_pos("600001", "A股", -0.05)]
    alerts = check_stock_drawdown(RULES, pos)
    assert alerts == []


# ── Test 2: stock drawdown — L1 ───────────────────────────────────────────

def test_stock_drawdown_l1():
    pos = [_pos("600001", "A股", -0.12)]
    alerts = check_stock_drawdown(RULES, pos)
    assert len(alerts) == 1
    assert alerts[0]["type"] == "single_stock_drawdown_l1"
    assert alerts[0]["severity"] == "info"


# ── Test 3: stock drawdown — L2 ───────────────────────────────────────────

def test_stock_drawdown_l2():
    pos = [_pos("600001", "A股", -0.22)]
    alerts = check_stock_drawdown(RULES, pos)
    assert len(alerts) == 1
    assert alerts[0]["type"] == "single_stock_drawdown_l2"
    assert alerts[0]["severity"] == "warning"


# ── Test 4: stock drawdown — L3 (only L3, not L2+L3) ─────────────────────

def test_stock_drawdown_l3_only():
    pos = [_pos("600001", "A股", -0.35)]
    alerts = check_stock_drawdown(RULES, pos)
    assert len(alerts) == 1
    assert alerts[0]["type"] == "single_stock_drawdown_l3"


# ── Test 5: single-stock position limit ───────────────────────────────────

def test_stock_position_over_limit():
    pos = [_pos("600219", "南山铝业", -0.05, market_value=30000)]
    alerts = check_stock_position(RULES, pos, total_c_value=100000)
    assert len(alerts) == 1
    assert alerts[0]["type"] == "single_stock_position"
    assert alerts[0]["severity"] == "critical"


def test_stock_position_within_limit():
    pos = [_pos("600219", "南山铝业", -0.05, market_value=20000)]
    alerts = check_stock_position(RULES, pos, total_c_value=100000)
    assert alerts == []


def test_stock_position_zero_total():
    pos = [_pos("600219", "南山铝业", -0.05, market_value=20000)]
    alerts = check_stock_position(RULES, pos, total_c_value=0)
    assert alerts == []


# ── Test 6: account drawdown ──────────────────────────────────────────────

def test_account_drawdown_l1():
    pos = [_pos("600001", "A", -0.12, market_value=88000, cost_total=100000)]
    alerts = check_account_drawdown(RULES, pos)
    assert len(alerts) == 1
    assert alerts[0]["type"] == "account_drawdown_l1"


def test_account_drawdown_l3():
    pos = [_pos("600001", "A", -0.25, market_value=75000, cost_total=100000)]
    alerts = check_account_drawdown(RULES, pos)
    assert len(alerts) == 1
    assert alerts[0]["type"] == "account_drawdown_l3"


def test_account_drawdown_no_alert():
    pos = [_pos("600001", "A", 0.05, market_value=105000, cost_total=100000)]
    alerts = check_account_drawdown(RULES, pos)
    assert alerts == []


# ── Test 7: theme concentration ───────────────────────────────────────────

def test_theme_concentration_triggered():
    pos = [
        _pos("002594", "比亚迪", -0.05, market_value=40000, industry="新能源"),
        _pos("601012", "隆基", -0.10, market_value=20000, industry="新能源"),
        _pos("600001", "其他", 0.0, market_value=10000, industry="银行"),
    ]
    alerts = check_theme_concentration(RULES, pos, total_c_value=70000)
    assert len(alerts) == 1
    assert alerts[0]["type"] == "theme_concentration"


def test_theme_concentration_not_triggered():
    pos = [
        _pos("002594", "比亚迪", -0.05, market_value=20000, industry="新能源"),
        _pos("600001", "其他", 0.0, market_value=80000, industry="银行"),
    ]
    alerts = check_theme_concentration(RULES, pos, total_c_value=100000)
    assert alerts == []


# ── Test 8: ETF drawdown & drift ──────────────────────────────────────────

def test_etf_drawdown_triggered():
    etf = [{"code": "159941", "name": "纳指100", "pnl_pct": -0.22}]
    alerts = check_etf_drawdown(RULES, etf)
    assert len(alerts) == 1
    assert alerts[0]["type"] == "etf_drawdown"


def test_etf_drawdown_not_triggered():
    etf = [{"code": "159941", "name": "纳指100", "pnl_pct": -0.10}]
    alerts = check_etf_drawdown(RULES, etf)
    assert alerts == []


def test_etf_drift_triggered():
    etf = [{"code": "563360", "name": "A500", "drift_raw": 0.08, "target_ratio": 0.20}]
    alerts = check_etf_drift(RULES, etf)
    assert len(alerts) == 1
    assert alerts[0]["type"] == "etf_drift"


def test_etf_drift_not_triggered():
    etf = [{"code": "563360", "name": "A500", "drift_raw": 0.03, "target_ratio": 0.20}]
    alerts = check_etf_drift(RULES, etf)
    assert alerts == []


# ── Test 9: Meituan RSU ───────────────────────────────────────────────────

def test_meituan_rsu_warning():
    capital = {"meituan_rsu_shares": 1000, "meituan_rsu_value": 200000}
    alerts = check_meituan_rsu(capital, current_hk_price=160.0)  # -20%
    assert len(alerts) == 1
    assert alerts[0]["type"] == "meituan_rsu_drawdown"
    assert alerts[0]["severity"] == "warning"


def test_meituan_rsu_daily_drop():
    capital = {"meituan_rsu_shares": 1000, "meituan_rsu_value": 200000}
    alerts = check_meituan_rsu(capital, current_hk_price=188.0)  # -6%
    assert len(alerts) == 1
    assert alerts[0]["type"] == "meituan_rsu_daily_drop"


def test_meituan_rsu_no_alert():
    capital = {"meituan_rsu_shares": 1000, "meituan_rsu_value": 200000}
    alerts = check_meituan_rsu(capital, current_hk_price=202.0)  # +1%
    assert alerts == []


def test_meituan_rsu_no_shares():
    capital = {"meituan_rsu_shares": 0, "meituan_rsu_value": 0}
    alerts = check_meituan_rsu(capital, current_hk_price=200.0)
    assert alerts == []
