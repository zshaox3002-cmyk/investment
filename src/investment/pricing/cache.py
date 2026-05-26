"""Write fetched quotes into the DB quotes table."""
from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from investment.core.db import transaction
from investment.migration.utils import instrument_id_by_code


def save_quotes(
    quotes: dict[str, Optional[dict]],
    items: list[tuple[str, str]],
    quote_date: Optional[str] = None,
    db_path=None,
) -> int:
    """Upsert quotes into the quotes table.

    items: list of (code, market) matching the keys in quotes.
    Returns number of rows inserted/replaced.
    """
    if not quotes:
        return 0
    today = quote_date or date.today().isoformat()
    fetched_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    inserted = 0

    market_map = {code: market for code, market in items}

    with transaction(db_path) as conn:
        for code, q in quotes.items():
            if q is None:
                continue
            market = market_map.get(code, "A")
            iid = instrument_id_by_code(conn, code, market)
            if iid is None:
                continue
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO quotes
                       (instrument_id, quote_date, open, high, low, close,
                        prev_close, change_pct, volume, amount, fetched_at, source)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (iid, today,
                     q.get("open"), q.get("high"), q.get("low"),
                     q["price"],
                     q.get("prev_close"), q.get("change_pct"),
                     q.get("volume"), q.get("amount"),
                     fetched_at, "tencent"),
                )
                inserted += conn.execute("SELECT changes()").fetchone()[0]
            except Exception as e:
                print(f"  [警告] 写入行情失败 {code}: {e}")
    return inserted
