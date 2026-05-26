#!/usr/bin/env python3
"""
trade_log.py — 交易冷静期日志

用法:
    python scripts/trade_log.py                  # 显示所有决策冷静期状态
    python scripts/trade_log.py status           # 仅显示今日可执行项
    python scripts/trade_log.py log <决策编号> <股数> <价格> [备注]
        例: python scripts/trade_log.py log 002 2050 5.30 "第一档第1笔"

职责:
  1. 扫描 trades/decision_*.md，解析创建日期和冷静期
  2. 显示哪些决策已过冷静期、可以执行
  3. 创建交易执行日志文件 trades/log_{编号}_{日期}.md
"""

import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
TRADES_DIR = ROOT_DIR / "trades"

# 从 rules.yaml 读取冷静期天数
def _load_cooling_days() -> tuple[int, int, int]:
    rules_path = ROOT_DIR / "config" / "rules.yaml"
    try:
        with open(rules_path, encoding="utf-8") as f:
            rules = yaml.safe_load(f)
        cp = rules.get("trading_rules", {}).get("cooling_period", {})
        return (
            cp.get("sell_decision_cooling_days", 3),
            cp.get("new_position_cooling_days", 7),
            cp.get("averaging_down_cooling_days", 5),
        )
    except Exception:
        return 3, 7, 5

COOLING_SELL, COOLING_NEW, COOLING_AVGDOWN = _load_cooling_days()

# 从 rules.yaml 读取减仓回笼资金分配比例
def _load_fund_routing() -> tuple[float, float]:
    rules_path = ROOT_DIR / "config" / "rules.yaml"
    try:
        with open(rules_path, encoding="utf-8") as f:
            rules = yaml.safe_load(f)
        alloc = rules.get("trading_rules", {}).get("fund_routing", {}).get("reduction_proceeds_allocation", {})
        return (
            alloc.get("B_core_etf", 0.70),
            alloc.get("A_cash_or_short_bond", 0.30),
        )
    except Exception:
        return 0.70, 0.30

FUND_B_ALLOC, FUND_A_ALLOC = _load_fund_routing()

# 决策类型关键词 → 冷静期
_TYPE_PATTERNS = [
    (re.compile(r"强制降权|强制减仓|减持执行|双减执行"), "sell", COOLING_SELL),
    (re.compile(r"新建仓|新买入|建仓"), "new", COOLING_NEW),
    (re.compile(r"补仓|向下补仓"), "avgdown", COOLING_AVGDOWN),
]
_DEFAULT_COOLING = COOLING_SELL  # 未识别类型默认按卖出冷静期


# ── Parsing ───────────────────────────────────────────────────────────

def _extract_date(text: str) -> date | None:
    """从决策文件中提取创建日期。"""
    patterns = [
        r"创建时间[：:]\s*(\d{4}-\d{2}-\d{2})",
        r"决策日期[：:]\s*(\d{4}-\d{2}-\d{2})",
        r"日期[：:]\s*(\d{4}-\d{2}-\d{2})",
        r"(\d{4}-\d{2}-\d{2})",  # fallback: first date in file
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            try:
                return datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except ValueError:
                continue
    return None


def _extract_cooling(text: str) -> tuple[str, int]:
    """识别决策类型，返回 (type_label, cooling_days)。"""
    for pattern, label, days in _TYPE_PATTERNS:
        if pattern.search(text[:500]):  # 只看文件头部
            return label, days
    # 检查是否有明确的冷静期声明
    m = re.search(r"冷静期[：:][^\n]*?(\d+)\s*(?:小时|天|日)", text[:500])
    if m:
        n = int(m.group(1))
        unit_match = re.search(r"冷静期[：:][^\n]*?(\d+)\s*(小时|天|日)", text[:500])
        if unit_match and "小时" in unit_match.group(2):
            return "sell", max(1, n // 24)
        return "sell", n
    return "sell", _DEFAULT_COOLING


def _extract_title(text: str, filename: str) -> str:
    """提取决策标题（第一个 # 标题）。"""
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else filename


def parse_decision_files() -> list[dict]:
    decisions = []
    for f in sorted(TRADES_DIR.glob("decision_*.md")):
        text = f.read_text(encoding="utf-8")
        created = _extract_date(text)
        type_label, cooling = _extract_cooling(text)
        title = _extract_title(text, f.stem)

        if created:
            ready_date = created + timedelta(days=cooling)
        else:
            ready_date = None

        decisions.append({
            "file": f,
            "name": f.stem,
            "title": title,
            "created": created,
            "cooling_days": cooling,
            "type": type_label,
            "ready_date": ready_date,
        })
    return decisions


# ── Status display ────────────────────────────────────────────────────

_TYPE_LABELS = {"sell": "减持/卖出", "new": "新建仓", "avgdown": "补仓"}
_COOLING_LABELS = {"sell": f"卖出冷静期 {COOLING_SELL}天", "new": f"新建仓冷静期 {COOLING_NEW}天",
                   "avgdown": f"补仓冷静期 {COOLING_AVGDOWN}天"}


def show_status(decisions: list[dict], today: date, only_ready=False):
    print(f"\n{'='*60}")
    print(f"交易冷静期状态 — {today}")
    print(f"{'='*60}")

    ready = [d for d in decisions if d["ready_date"] and d["ready_date"] <= today]
    cooling = [d for d in decisions if d["ready_date"] and d["ready_date"] > today]
    unknown = [d for d in decisions if not d["ready_date"]]

    if not only_ready:
        if cooling:
            print(f"\n🔒 冷静期中（{len(cooling)} 项）")
            for d in cooling:
                remaining = (d["ready_date"] - today).days
                print(f"  {d['name']:20s}  创建 {d['created']}  "
                      f"解锁 {d['ready_date']}（还需 {remaining} 天）")
                print(f"    └─ {d['title'][:60]}")

    if ready:
        print(f"\n✅ 可执行（{len(ready)} 项）")
        for d in ready:
            days_since = (today - d["ready_date"]).days
            flag = "  ⚠ 已逾期" if days_since > 7 else ""
            print(f"  {d['name']:20s}  创建 {d['created']}  "
                  f"解锁 {d['ready_date']}{flag}")
            print(f"    └─ {d['title'][:60]}")
    elif only_ready:
        print("  今日无可执行决策")

    if unknown and not only_ready:
        print(f"\n❓ 无法解析日期（{len(unknown)} 项）")
        for d in unknown:
            print(f"  {d['name']:20s}  {d['file'].name}")

    print()


# ── Log creation ──────────────────────────────────────────────────────

LOG_TEMPLATE = """\
# 交易执行日志 · {decision_id} · {date}

- 执行日期：{date}
- 关联决策：trades/{decision_file}
- 执行档位/批次：（请填写）
- 计划股数：{shares} 股
- 实际股数：{shares} 股
- 成交均价：¥{price}
- 回笼资金：¥{proceeds:,.2f}
- 资金分配：B 档 ¥{b_alloc:,.2f} / A 档 ¥{a_alloc:,.2f}
- 执行后仓位占比：（请填写）
- 执行后 active_breaches 状态：（请填写）
- 执行偏差说明：无
- 情绪记录：（请填写执行时的真实情绪）
- 备注：{note}
"""


def create_log(decision_id: str, shares: int, price: float, note: str = ""):
    today = date.today()
    date_str = today.strftime("%Y-%m-%d")

    # 找到对应的 decision 文件
    matches = list(TRADES_DIR.glob(f"decision_{decision_id}*.md"))
    if not matches:
        print(f"❌ 未找到 decision_{decision_id}*.md")
        return

    decision_file = matches[0].name
    proceeds = shares * price
    b_alloc = proceeds * FUND_B_ALLOC
    a_alloc = proceeds * FUND_A_ALLOC

    content = LOG_TEMPLATE.format(
        decision_id=decision_id,
        date=date_str,
        decision_file=decision_file,
        shares=shares,
        price=f"{price:.2f}",
        proceeds=proceeds,
        b_alloc=b_alloc,
        a_alloc=a_alloc,
        note=note or "无",
    )

    log_path = TRADES_DIR / f"log_{decision_id}_{date_str}.md"
    if log_path.exists():
        # 避免覆盖，加序号
        for i in range(2, 10):
            candidate = TRADES_DIR / f"log_{decision_id}_{date_str}_{i}.md"
            if not candidate.exists():
                log_path = candidate
                break

    log_path.write_text(content, encoding="utf-8")
    print(f"✅ 执行日志已创建: {log_path}")
    print(f"   {shares} 股 × ¥{price:.2f} = ¥{proceeds:,.2f}")
    print(f"   B 档 ¥{b_alloc:,.2f} / A 档 ¥{a_alloc:,.2f}")
    print(f"\n请补充填写日志中的空白字段后保存。")


# ── Main ──────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    today = date.today()
    decisions = parse_decision_files()

    if not args or args[0] == "status":
        only_ready = args[0] == "status" if args else False
        show_status(decisions, today, only_ready=only_ready)
        return

    if args[0] == "log":
        if len(args) < 4:
            print("用法: python scripts/trade_log.py log <决策编号> <股数> <价格> [备注]")
            print("  例: python scripts/trade_log.py log 002 2050 5.30 '第一档第1笔'")
            sys.exit(1)
        decision_id = args[1]
        try:
            shares = int(args[2])
            price = float(args[3])
        except ValueError:
            print("❌ 股数和价格必须为数字")
            sys.exit(1)
        note = args[4] if len(args) > 4 else ""
        create_log(decision_id, shares, price, note)
        return

    print(f"未知命令: {args[0]}")
    print("用法: python scripts/trade_log.py [status | log <编号> <股数> <价格> [备注]]")
    sys.exit(1)


if __name__ == "__main__":
    main()
