"""Unit tests for pricing/tencent.py — parser and code mapping."""
import pytest
from investment.pricing.tencent import _tencent_code, _parse_tencent_line


# ── Test 1: _tencent_code ─────────────────────────────────────────────────

def test_tencent_code_sh():
    assert _tencent_code("600219", "A") == "sh600219"


def test_tencent_code_sz():
    assert _tencent_code("002594", "A") == "sz002594"


def test_tencent_code_etf_sh():
    # ETF starting with 5 → sh
    assert _tencent_code("513010", "A") == "sh513010"


def test_tencent_code_etf_sz():
    # ETF starting with 1 → sz
    assert _tencent_code("159941", "A") == "sz159941"


def test_tencent_code_hk():
    assert _tencent_code("03690", "HK") == "hk03690"


def test_tencent_code_hk_padding():
    # Short HK code should be zero-padded to 5 digits
    assert _tencent_code("3690", "HK") == "hk03690"


# ── Test 2: _parse_tencent_line ───────────────────────────────────────────

def _make_line(code="600219", name="南山铝业", price="5.50", prev_close="5.40",
               open_="5.42", volume="1000000", high="5.60", low="5.38",
               amount="5500000"):
    """Build a minimal tencent response line with 38 fields.

    Real tencent format: v_shCODE="~name~code~price~...~";
    The trailing "; must be outside the quoted data so strip('"') leaves clean data.
    """
    fields = [""] * 38
    fields[1] = name
    fields[2] = code
    fields[3] = price
    fields[4] = prev_close
    fields[5] = open_
    fields[6] = volume
    fields[33] = high
    fields[34] = low
    fields[37] = amount
    data = "~".join(fields)
    # Correct format: value ends with "; outside the quoted string
    return f'v_sh{code}="{data}"'


def test_parse_tencent_line_basic():
    line = _make_line()
    result = _parse_tencent_line(line)
    assert result is not None
    code, quote = result
    assert code == "600219"
    assert quote["price"] == pytest.approx(5.50)
    assert quote["prev_close"] == pytest.approx(5.40)
    assert quote["name"] == "南山铝业"


def test_parse_tencent_line_change_pct():
    line = _make_line(price="5.50", prev_close="5.00")
    _, quote = _parse_tencent_line(line)
    assert quote["change_pct"] == pytest.approx(0.10)


def test_parse_tencent_line_no_equals():
    result = _parse_tencent_line("no_equals_sign_here")
    assert result is None


def test_parse_tencent_line_empty_price():
    fields = [""] * 38
    fields[1] = "X"
    fields[2] = "600001"
    fields[3] = ""  # empty price
    data = "~".join(fields)
    line = f'v_sh600001="{data}";'
    result = _parse_tencent_line(line)
    assert result is None


def test_parse_tencent_line_too_few_fields():
    line = 'v_sh600001="a~b~c";'
    result = _parse_tencent_line(line)
    assert result is None
