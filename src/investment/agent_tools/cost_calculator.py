"""Trade cost calculator — Phase 7 Skill ⑦.

Computes A-share and HK-share transaction costs:
  - Commission (broker fee)
  - Stamp duty (印花税)
  - Transfer fee (过户费, 沪市 only)
  - Other fees (港股结算费等)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from investment.core.db import connect, transaction


# ── Market detection ──────────────────────────────────────────────────────────

def detect_market(code: str) -> str:
    """Detect market from stock code."""
    code = code.strip().upper()
    # HK: 5-digit or starts with 0/1/2/3/6/8/9 with .HK suffix
    if code.endswith(".HK") or (len(code) == 5 and code.isdigit()):
        return "HK"
    # A-share
    if code.startswith(("60", "68", "900")):
        return "A_SH"
    if code.startswith(("00", "30", "200")):
        return "A_SZ"
    if code.startswith(("43", "83", "87", "88")):
        return "A_BJ"
    # ETF codes
    if code.startswith(("51", "58", "56", "15")):
        return "A_SH" if code.startswith(("51", "58")) else "A_SZ"
    return "A_SH"  # default


# ── Cost model loading ────────────────────────────────────────────────────────

_DEFAULT_MODELS = {
    "A_SH": dict(commission_rate=0.00025, commission_min=5.0,
                 stamp_duty_sell=0.001, stamp_duty_buy=0.0,
                 transfer_fee_rate=0.00002, transfer_fee_min=0.0,
                 settlement_fee_rate=0.0, platform_fee=0.0),
    "A_SZ": dict(commission_rate=0.00025, commission_min=5.0,
                 stamp_duty_sell=0.001, stamp_duty_buy=0.0,
                 transfer_fee_rate=0.0, transfer_fee_min=0.0,
                 settlement_fee_rate=0.0, platform_fee=0.0),
    "A_BJ": dict(commission_rate=0.00025, commission_min=5.0,
                 stamp_duty_sell=0.001, stamp_duty_buy=0.0,
                 transfer_fee_rate=0.0, transfer_fee_min=0.0,
                 settlement_fee_rate=0.0, platform_fee=0.0),
    "HK":   dict(commission_rate=0.0003, commission_min=50.0,
                 stamp_duty_sell=0.001, stamp_duty_buy=0.001,
                 transfer_fee_rate=0.0, transfer_fee_min=0.0,
                 settlement_fee_rate=0.00002, platform_fee=15.0),
}


def _load_cost_model(market: str, db_path=None) -> dict:
    try:
        conn = connect(db_path)
        row = conn.execute(
            "SELECT * FROM cost_model WHERE market=?", (market,)
        ).fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception:
        pass
    return _DEFAULT_MODELS.get(market, _DEFAULT_MODELS["A_SH"])


# ── Cost calculation ──────────────────────────────────────────────────────────

@dataclass
class CostBreakdown:
    market: str
    side: str
    shares: float
    price: float
    gross_amount: float
    commission: float
    stamp_duty: float
    transfer_fee: float
    other_fees: float
    total_cost: float
    net_amount: float
    cost_rate: float
    human_message: str


def calc_cost(
    code: str,
    shares: float,
    price: float,
    side: str,
    broker_commission_rate: Optional[float] = None,
    db_path=None,
) -> CostBreakdown:
    """Calculate full transaction cost breakdown."""
    market = detect_market(code)
    model = _load_cost_model(market, db_path)

    gross = shares * price
    rate = broker_commission_rate if broker_commission_rate is not None else model["commission_rate"]

    # Commission
    commission = max(gross * rate, model["commission_min"])

    # Stamp duty
    if side.upper() == "SELL":
        stamp = gross * model["stamp_duty_sell"]
    else:
        stamp = gross * model["stamp_duty_buy"]

    # Transfer fee (沪市 only)
    transfer = max(gross * model["transfer_fee_rate"], model["transfer_fee_min"])

    # Other fees (港股 settlement + platform)
    other = gross * model["settlement_fee_rate"] + model["platform_fee"]

    total = commission + stamp + transfer + other
    cost_rate = total / gross if gross > 0 else 0.0

    if side.upper() == "BUY":
        net = gross + total  # total outlay
    else:
        net = gross - total  # net proceeds

    breakdown = CostBreakdown(
        market=market, side=side.upper(),
        shares=shares, price=price, gross_amount=gross,
        commission=commission, stamp_duty=stamp,
        transfer_fee=transfer, other_fees=other,
        total_cost=total, net_amount=net,
        cost_rate=cost_rate,
        human_message="",
    )
    breakdown.human_message = _build_human_message(breakdown)
    return breakdown


def _build_human_message(b: CostBreakdown) -> str:
    market_names = {"A_SH": "沪市A股", "A_SZ": "深市A股", "A_BJ": "北交所", "HK": "港股"}
    side_label = "买入" if b.side == "BUY" else "卖出"
    lines = [
        f"## 交易成本估算\n",
        f"**{side_label}** {b.shares:.0f} 股 @ ¥{b.price:.3f}（{market_names.get(b.market, b.market)}）\n",
        "### 费用明细",
        "| 费用项 | 金额 | 说明 |",
        "|--------|------|------|",
        f"| 券商佣金 | ¥{b.commission:.2f} | 万{b.commission/b.gross_amount*10000:.1f}（最低5元） |",
    ]
    if b.stamp_duty > 0:
        lines.append(f"| 印花税 | ¥{b.stamp_duty:.2f} | {side_label}时收取 0.1% |")
    if b.transfer_fee > 0:
        lines.append(f"| 过户费 | ¥{b.transfer_fee:.2f} | 沪市收取 0.002% |")
    if b.other_fees > 0:
        lines.append(f"| 其他费用 | ¥{b.other_fees:.2f} | 港股结算/平台费 |")
    lines += [
        f"| **合计** | **¥{b.total_cost:.2f}** | 综合费率 {b.cost_rate*100:.3f}% |",
        "",
        f"### 实际{'支出' if b.side == 'BUY' else '到手'}",
        f"¥{b.net_amount:,.2f}",
        "",
        f"所以你该做什么：这笔交易的摩擦成本为 {b.cost_rate*100:.3f}%，"
        f"需要股价{'上涨' if b.side == 'BUY' else '下跌'} {b.cost_rate*100:.2f}% 才能回本。",
    ]
    return "\n".join(lines)


def save_cost_log(
    breakdown: CostBreakdown,
    trade_id: Optional[int] = None,
    db_path=None,
) -> int:
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with transaction(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO trade_cost_log
               (trade_id, calc_date, market, side, shares, price, gross_amount,
                commission, stamp_duty, transfer_fee, other_fees,
                total_cost, net_amount, cost_rate, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (trade_id, now[:10], breakdown.market, breakdown.side,
             breakdown.shares, breakdown.price, breakdown.gross_amount,
             breakdown.commission, breakdown.stamp_duty, breakdown.transfer_fee,
             breakdown.other_fees, breakdown.total_cost, breakdown.net_amount,
             breakdown.cost_rate, now),
        )
        return cur.lastrowid
