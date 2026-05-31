"""Tencent market data fetcher.

Wraps the qt.gtimg.cn API used by the legacy common.py.
Returns raw quote dicts; cache.py handles writing to the DB.
"""
from __future__ import annotations

import urllib.request
from typing import Optional


def _tencent_code(code: str, market: str) -> str:
    if market == "HK":
        return "hk" + code.zfill(5)
    return ("sh" if code.startswith(("6", "5")) else "sz") + code


def _parse_tencent_line(line: str) -> Optional[tuple[str, dict]]:
    if "=" not in line:
        return None
    data = line.split("=", 1)[1].strip().strip('"')
    fields = data.split("~")
    if len(fields) < 35 or not fields[3]:
        return None
    try:
        code = fields[2]
        price = float(fields[3])
        prev_close = float(fields[4]) if fields[4] else price
        change_pct = (price - prev_close) / prev_close if prev_close else 0.0
        return code, {
            "name": fields[1],
            "price": price,
            "prev_close": prev_close,
            "change_pct": change_pct,
            "high": float(fields[33]) if fields[33] else price,
            "low": float(fields[34]) if fields[34] else price,
            "open": float(fields[5]) if fields[5] else price,
            "volume": float(fields[6]) if fields[6] else 0.0,
            "amount": float(fields[37]) if len(fields) > 37 and fields[37] else 0.0,
        }
    except (ValueError, IndexError):
        return None


def fetch_prices_batch(
    items: list[tuple[str, str]],
    timeout: int = 15,
) -> dict[str, Optional[dict]]:
    """Fetch quotes for a list of (code, market) pairs.

    Returns {code: quote_dict | None}.
    """
    if not items:
        return {}
    tc_list = [_tencent_code(code, market) for code, market in items]
    url = "https://qt.gtimg.cn/q=" + ",".join(tc_list)
    result: dict[str, Optional[dict]] = {code: None for code, _ in items}
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("gbk")
        for line in raw.strip().splitlines():
            parsed = _parse_tencent_line(line)
            if parsed:
                code, quote = parsed
                if code in result:
                    result[code] = quote
    except Exception as e:
        print(f"  [警告] 批量行情获取失败: {e}")
    return result


def fetch_price(code: str, market: str = "A") -> Optional[dict]:
    return fetch_prices_batch([(code, market)]).get(code)
