#!/usr/bin/env python3
"""Tests for core calculation and helper functions in common.py."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import calc_holding, _fmt_pct, _is_ashare, _tencent_code


class TestFmtPct(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(_fmt_pct(0.153), "15.30%")

    def test_negative(self):
        self.assertEqual(_fmt_pct(-0.2131), "-21.31%")

    def test_zero(self):
        self.assertEqual(_fmt_pct(0.0), "0.00%")

    def test_none(self):
        self.assertEqual(_fmt_pct(None), "N/A")

    def test_one(self):
        self.assertEqual(_fmt_pct(1.0), "100.00%")


class TestIsAshare(unittest.TestCase):
    def test_digits(self):
        self.assertTrue(_is_ashare("600219"))

    def test_leading_zero(self):
        self.assertTrue(_is_ashare("000568"))

    def test_hk_ticker_digits(self):
        # HK tickers are also all-digit; _is_ashare only checks isdigit()
        self.assertTrue(_is_ashare("02015"))

    def test_empty(self):
        self.assertFalse(_is_ashare(""))


class TestTencentCode(unittest.TestCase):
    def test_shanghai(self):
        self.assertEqual(_tencent_code("600219", "A"), "sh600219")

    def test_shenzhen(self):
        self.assertEqual(_tencent_code("000568", "A"), "sz000568")

    def test_hk(self):
        self.assertEqual(_tencent_code("03690", "HK"), "hk03690")

    def test_hk_five_digit(self):
        self.assertEqual(_tencent_code("2015", "HK"), "hk02015")


class TestCalcHolding(unittest.TestCase):
    def setUp(self):
        self.holding = {
            "code": "600219",
            "market": "A",
            "name": "南山铝业",
            "shares": 53200,
            "cost_price": 6.71,
            "current_price": 5.28,
            "added_date": "2025-01-01",
            "industry": "有色金属/铝加工",
            "reason": "",
        }

    def test_no_quote_uses_csv_price(self):
        result = calc_holding(self.holding, None)
        self.assertEqual(result["current_price"], 5.28)
        self.assertAlmostEqual(result["market_value"], 53200 * 5.28)
        self.assertAlmostEqual(result["pnl_pct"], (5.28 - 6.71) / 6.71)

    def test_with_quote_overrides_price(self):
        quote = {"price": 5.50, "change_pct": 0.02, "name": "南山铝业"}
        result = calc_holding(self.holding, quote)
        self.assertEqual(result["current_price"], 5.50)
        self.assertAlmostEqual(result["market_value"], 53200 * 5.50)

    def test_zero_cost(self):
        h = {**self.holding, "cost_price": 0.0}
        result = calc_holding(h, None)
        self.assertEqual(result["pnl_pct"], 0.0)

    def test_pnl_fields(self):
        result = calc_holding(self.holding, None)
        self.assertIn("market_value", result)
        self.assertIn("cost_total", result)
        self.assertIn("pnl", result)
        self.assertIn("pnl_pct", result)
        self.assertAlmostEqual(result["pnl"], result["market_value"] - result["cost_total"])


if __name__ == "__main__":
    unittest.main()
