#!/usr/bin/env python3
"""Tests for alert checking functions in common.py."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import check_alerts, check_etf_alerts, check_meituan_rsu_alerts


def _make_rules(overrides=None):
    """Build a minimal valid rules dict with optional overrides."""
    rules = {
        "portfolio_rules": {
            "concentration": {
                "single_stock_max": {"threshold": 0.25, "action": "force_reduce"},
            },
            "drawdown_control": {
                "level_1_alert": {"threshold": 0.10, "action": "alert_only"},
                "level_2_control": {"threshold": 0.15, "action": "force_review"},
                "level_3_hard": {"threshold": 0.20, "action": "force_reduce"},
            },
            "theme_concentration": {
                "new_energy_and_power_chain": {
                    "threshold": 0.35,
                    "action": "warning",
                    "includes": ["新能源汽车", "光伏/太阳能"],
                },
            },
        },
        "stock_rules": {
            "stop_loss": {
                "level_1_alert": {"threshold": -0.10, "action": "alert_only"},
                "level_2_review": {"threshold": -0.20, "action": "trigger_ic_memo"},
                "level_3_hard": {"threshold": -0.30, "action": "force_review"},
            },
        },
    }
    if overrides:
        for key, val in overrides.items():
            parts = key.split(".")
            d = rules
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            d[parts[-1]] = val
    return rules


def _pos(code, name, pnl_pct, market_value, cost_total, industry=""):
    return {
        "code": code, "name": name,
        "pnl_pct": pnl_pct,
        "market_value": market_value,
        "cost_total": cost_total,
        "industry": industry,
    }


class TestCheckAlertsNoTriggers(unittest.TestCase):
    def test_all_healthy(self):
        rules = _make_rules()
        positions = [
            _pos("600219", "南山铝业", -0.05, 73500, 77368, "有色金属/铝加工"),
            _pos("002594", "比亚迪", -0.03, 65000, 67010, "新能源汽车"),
            _pos("000568", "泸州老窖", 0.02, 60000, 58824, "白酒"),
            _pos("601318", "中国平安", -0.04, 55000, 57292, "保险"),
            _pos("001280", "中国铀业", -0.06, 46500, 49468, "核电/核燃料"),
        ]
        total_c = 300000  # each <= 24.5%, total drawdown ~2.9%
        alerts = check_alerts(rules, positions, total_c)
        self.assertEqual(alerts, [])


class TestCheckAlertsDrawdown(unittest.TestCase):
    def test_l1_drawdown(self):
        rules = _make_rules()
        positions = [_pos("600219", "南山铝业", -0.15, 280000, 329400)]
        alerts = check_alerts(rules, positions, 280000)
        l1 = [a for a in alerts if a["type"] == "single_stock_drawdown_l1"]
        self.assertEqual(len(l1), 1)
        self.assertEqual(l1[0]["severity"], "info")

    def test_l2_drawdown(self):
        rules = _make_rules()
        positions = [_pos("600219", "南山铝业", -0.22, 280000, 358974)]
        alerts = check_alerts(rules, positions, 280000)
        l2 = [a for a in alerts if a["type"] == "single_stock_drawdown_l2"]
        self.assertEqual(len(l2), 1)
        self.assertEqual(l2[0]["severity"], "warning")
        # L2 should supersede L1 (no duplicate)
        l1 = [a for a in alerts if a["type"] == "single_stock_drawdown_l1"]
        self.assertEqual(len(l1), 0)

    def test_l3_drawdown(self):
        rules = _make_rules()
        positions = [_pos("600219", "南山铝业", -0.35, 280000, 430769)]
        alerts = check_alerts(rules, positions, 280000)
        l3 = [a for a in alerts if a["type"] == "single_stock_drawdown_l3"]
        self.assertEqual(len(l3), 1)
        self.assertEqual(l3[0]["severity"], "critical")


class TestCheckAlertsPosition(unittest.TestCase):
    def test_position_exceeded(self):
        rules = _make_rules()
        positions = [_pos("600219", "南山铝业", -0.05, 90000, 100000)]
        total_c = 100000  # 90% of total — well over 25%
        alerts = check_alerts(rules, positions, total_c)
        pos = [a for a in alerts if a["type"] == "single_stock_position"]
        self.assertEqual(len(pos), 1)
        self.assertEqual(pos[0]["severity"], "critical")

    def test_position_ok(self):
        rules = _make_rules()
        positions = [_pos("600219", "南山铝业", -0.05, 20000, 22000)]
        total_c = 100000  # 20% — within limit
        alerts = check_alerts(rules, positions, total_c)
        pos = [a for a in alerts if a["type"] == "single_stock_position"]
        self.assertEqual(len(pos), 0)


class TestCheckAlertsAccountDrawdown(unittest.TestCase):
    def test_l1_account(self):
        rules = _make_rules()
        positions = [_pos("600219", "南山铝业", -0.12, 88000, 100000)]
        alerts = check_alerts(rules, positions, 88000)
        ad = [a for a in alerts if a["type"] == "account_drawdown_l1"]
        self.assertEqual(len(ad), 1)

    def test_l2_account(self):
        rules = _make_rules()
        positions = [_pos("600219", "南山铝业", -0.16, 84000, 100000)]
        alerts = check_alerts(rules, positions, 84000)
        ad = [a for a in alerts if a["type"] == "account_drawdown_l2"]
        self.assertEqual(len(ad), 1)

    def test_l3_account(self):
        rules = _make_rules()
        positions = [_pos("600219", "南山铝业", -0.25, 75000, 100000)]
        alerts = check_alerts(rules, positions, 75000)
        ad = [a for a in alerts if a["type"] == "account_drawdown_l3"]
        self.assertEqual(len(ad), 1)


class TestCheckAlertsThemeConcentration(unittest.TestCase):
    def test_theme_exceeded(self):
        rules = _make_rules()
        positions = [
            _pos("002594", "比亚迪", -0.05, 20000, 21000, "新能源汽车"),
            _pos("601012", "隆基绿能", -0.10, 20000, 22000, "光伏/太阳能"),
        ]
        total_c = 40000  # theme = 100% of C
        alerts = check_alerts(rules, positions, total_c)
        theme = [a for a in alerts if a["type"] == "theme_concentration"]
        self.assertEqual(len(theme), 1)

    def test_theme_ok(self):
        rules = _make_rules()
        positions = [
            _pos("002594", "比亚迪", -0.05, 5000, 5500, "新能源汽车"),
            _pos("000568", "泸州老窖", -0.05, 15000, 16000, "白酒"),
        ]
        total_c = 20000  # theme = 25% — under 35%
        alerts = check_alerts(rules, positions, total_c)
        theme = [a for a in alerts if a["type"] == "theme_concentration"]
        self.assertEqual(len(theme), 0)


class TestCheckEtfAlerts(unittest.TestCase):
    def test_etf_drawdown(self):
        rules = _make_rules()
        etf_positions = [
            {"code": "513180", "name": "恒生科技 ETF", "pnl_pct": -0.25,
             "market_value": 80000, "target_ratio": 0.5, "drift_raw": None},
        ]
        alerts = check_etf_alerts(rules, etf_positions, 80000)
        dd = [a for a in alerts if a["type"] == "etf_drawdown"]
        self.assertEqual(len(dd), 1)

    def test_etf_drift(self):
        rules = _make_rules()
        etf_positions = [
            {"code": "513180", "name": "恒生科技 ETF", "pnl_pct": -0.05,
             "market_value": 50000, "target_ratio": 0.25, "drift_raw": 0.35},
        ]
        alerts = check_etf_alerts(rules, etf_positions, 50000)
        drift = [a for a in alerts if a["type"] == "etf_drift"]
        self.assertEqual(len(drift), 1)

    def test_no_alerts(self):
        rules = _make_rules()
        etf_positions = [
            {"code": "513180", "name": "恒生科技 ETF", "pnl_pct": -0.05,
             "market_value": 25000, "target_ratio": 0.25, "drift_raw": 0.0},
        ]
        alerts = check_etf_alerts(rules, etf_positions, 25000)
        self.assertEqual(alerts, [])


class TestCheckMeituanRsuAlerts(unittest.TestCase):
    def test_no_rsu_shares(self):
        capital = {"meituan_rsu_shares": 0}
        alerts = check_meituan_rsu_alerts(capital, 100)
        self.assertEqual(alerts, [])

    def test_info_alert_5pct_drop(self):
        capital = {"meituan_rsu_shares": 37000, "meituan_rsu_value": 3000000}
        # 5% drop from ¥3M → ¥2.85M
        price = 2850000 / 37000  # ≈ 77.03
        alerts = check_meituan_rsu_alerts(capital, price)
        self.assertTrue(any(a["type"] == "meituan_rsu_daily_drop" for a in alerts))

    def test_warning_alert_15pct_drop(self):
        capital = {"meituan_rsu_shares": 37000, "meituan_rsu_value": 3000000}
        # 15% drop
        price = 2550000 / 37000
        alerts = check_meituan_rsu_alerts(capital, price)
        self.assertTrue(any(a["type"] == "meituan_rsu_drawdown" for a in alerts))

    def test_no_alert_small_change(self):
        capital = {"meituan_rsu_shares": 37000, "meituan_rsu_value": 3000000}
        price = 2970000 / 37000  # ~1% drop
        alerts = check_meituan_rsu_alerts(capital, price)
        self.assertEqual(alerts, [])


if __name__ == "__main__":
    unittest.main()
