#!/usr/bin/env python3
"""Tests for config validation functions."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from validate_config import check_rules_structure, _get_nested


class TestGetNested(unittest.TestCase):
    def test_simple_path(self):
        d = {"a": {"b": 42}}
        self.assertEqual(_get_nested(d, ["a", "b"]), 42)

    def test_missing_key(self):
        d = {"a": {}}
        self.assertIsNone(_get_nested(d, ["a", "b", "c"]))

    def test_non_dict_mid_path(self):
        d = {"a": 1}
        self.assertIsNone(_get_nested(d, ["a", "b"]))

    def test_empty_dict(self):
        self.assertIsNone(_get_nested({}, ["a"]))


class TestCheckRulesStructure(unittest.TestCase):
    def setUp(self):
        self.valid_rules = {
            "portfolio_rules": {
                "concentration": {
                    "single_stock_max": {"threshold": 0.25, "action": "force_reduce"},
                },
                "sector_concentration": {
                    "single_sector_max": {"threshold": 0.40, "action": "warning"},
                },
                "theme_concentration": {
                    "new_energy_and_power_chain": {
                        "threshold": 0.35, "action": "warning",
                        "includes": ["新能源汽车"],
                    },
                },
                "drawdown_control": {
                    "level_1_alert": {"threshold": 0.10, "action": "alert_only"},
                    "level_2_control": {"threshold": 0.15, "action": "force_review"},
                    "level_3_hard": {"threshold": 0.20, "action": "force_reduce"},
                },
                "active_position_total": {"target": 0.25, "hard_max": 0.30},
            },
            "stock_rules": {
                "stop_loss": {
                    "level_1_alert": {"threshold": -0.10, "action": "alert_only"},
                    "level_2_review": {"threshold": -0.20, "action": "trigger_ic_memo"},
                    "level_3_hard": {"threshold": -0.30, "action": "force_review"},
                },
            },
            "active_position": {
                "holding_count_min": 5,
                "holding_count_max": 8,
                "single_stock_max": 0.25,
                "single_industry_max": 0.40,
            },
            "monitoring": {
                "etf_drawdown_warn": 0.20,
                "etf_drift_threshold": 0.05,
            },
        }

    def test_valid_rules_passes(self):
        errors = check_rules_structure(self.valid_rules)
        self.assertEqual(errors, [])

    def test_missing_threshold(self):
        rules = {
            "portfolio_rules": {
                "concentration": {
                    "single_stock_max": {"action": "force_reduce"},
                },
            },
        }
        errors = check_rules_structure(rules)
        self.assertTrue(any("single_stock_max" in e and "threshold" in e for e in errors))

    def test_out_of_range_threshold(self):
        rules = {
            **self.valid_rules,
            "portfolio_rules": {
                **self.valid_rules["portfolio_rules"],
                "concentration": {
                    "single_stock_max": {"threshold": 1.5, "action": "force_reduce"},
                },
            },
        }
        errors = check_rules_structure(rules)
        self.assertTrue(any("single_stock_max" in e and "超出范围" in e for e in errors))

    def test_negative_drawdown_threshold_positive(self):
        """stop_loss thresholds must be negative. Positive value should error."""
        rules = {
            **self.valid_rules,
            "stock_rules": {
                "stop_loss": {
                    "level_1_alert": {"threshold": 0.10, "action": "alert_only"},
                    "level_2_review": {"threshold": -0.20, "action": "trigger_ic_memo"},
                    "level_3_hard": {"threshold": -0.30, "action": "force_review"},
                },
            },
        }
        errors = check_rules_structure(rules)
        self.assertTrue(any("level_1_alert" in e and "超出范围" in e for e in errors))

    def test_invalid_action_value(self):
        rules = {
            **self.valid_rules,
            "portfolio_rules": {
                **self.valid_rules["portfolio_rules"],
                "concentration": {
                    "single_stock_max": {"threshold": 0.25, "action": "invalid_action"},
                },
            },
        }
        errors = check_rules_structure(rules)
        self.assertTrue(any("非法值" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
