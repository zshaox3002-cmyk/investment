#!/usr/bin/env python3
"""
common.py — 投资系统共享模块

本模块提供所有脚本共用的：路径常量、配置加载器、行情数据获取、
持仓计算、告警检查、thesis 同步检查。

所有可变配置从 config/rules.yaml 和 config/capital.yaml 读取，
不在此文件中硬编码任何阈值。
"""

import csv
import re
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import yaml

# ═══════════════════════════════════════════════════════════════════════════
# Paths
# ═══════════════════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
CONFIG_DIR = ROOT_DIR / "config"
SCRIPTS_DIR = ROOT_DIR / "scripts"
RULES_PATH = CONFIG_DIR / "rules.yaml"
HOLDINGS_PATH = CONFIG_DIR / "holdings.csv"
CORE_ETF_PATH = CONFIG_DIR / "core_etf.csv"
CASH_POSITIONS_PATH = CONFIG_DIR / "cash_positions.csv"
CAPITAL_PATH = CONFIG_DIR / "capital.yaml"
THESES_DIR = ROOT_DIR / "theses"
DATA_DIR = ROOT_DIR / "data"
PORTFOLIO_TS_PATH = DATA_DIR / "portfolio_ts.csv"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════
# Action → severity mapping
# ═══════════════════════════════════════════════════════════════════════════

ACTION_SEVERITY_MAP = {
    "force_reduce": "critical",
    "force_review": "critical",
    "force_exit": "critical",
    "trigger_ic_memo": "warning",
    "warning": "warning",
    "alert_only": "info",
}

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _is_ashare(code: str) -> bool:
    return code.strip().isdigit()


def _fmt_pct(val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    return f"{val * 100:.2f}%"


_warned_keys: set[str] = set()

def _warn_once(key_path: str) -> None:
    """配置键缺失时打印一次告警（同键不重复）。"""
    if key_path not in _warned_keys:
        _warned_keys.add(key_path)
        print(f"  ⚠ 配置缺失: rules.yaml 中未找到 '{key_path}'，使用硬编码默认值",
              file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════════
# Loaders
# ═══════════════════════════════════════════════════════════════════════════

def load_rules() -> dict:
    with open(RULES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_holdings() -> list[dict]:
    rows = []
    with open(HOLDINGS_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            code = r["code"].strip()
            if not code or code == "示例":
                continue
            rows.append({
                "code": code,
                "market": r.get("market", "A").strip(),
                "name": r["name"].strip(),
                "shares": float(r["shares"]),
                "cost_price": float(r["cost_price"]),
                "current_price": float(r["current_price"]),
                "added_date": r["added_date"].strip(),
                "industry": r.get("industry", "").strip(),
                "reason": r.get("reason", "").strip(),
            })
    return rows


def load_etf_config() -> list[dict]:
    rows = []
    with open(CORE_ETF_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            code = r["code"].strip()
            if not code:
                continue
            rows.append({
                "code": code,
                "name": r["name"].strip(),
                "shares": float(r["shares"]),
                "cost_price": float(r["cost_price"]),
                "target_ratio": float(r["target_ratio"]),
                "rebalance_freq": r["rebalance_freq"].strip(),
            })
    return rows


def load_capital() -> dict:
    if not CAPITAL_PATH.exists():
        return {"total_bc_capital": 0}
    with open(CAPITAL_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_cash_positions() -> list[dict]:
    rows = []
    if not CASH_POSITIONS_PATH.exists():
        return rows
    with open(CASH_POSITIONS_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            code = r["code"].strip()
            if not code:
                continue
            rows.append({
                "code": code,
                "type": r.get("type", "").strip(),
                "name": r["name"].strip(),
                "balance": float(r["balance"]),
                "annual_rate": r.get("annual_rate", "0%").strip(),
                "current_value": float(r["current_value"]),
                "status": r.get("status", "").strip(),
                "notes": r.get("notes", "").strip(),
            })
    return rows


def load_thesis_scores() -> dict[str, dict]:
    scores: dict[str, dict] = {}
    if not THESES_DIR.exists():
        return scores

    for f in THESES_DIR.glob("*.md"):
        if f.name.startswith("_"):
            continue
        text = f.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        if fm and fm.get("code"):
            code = fm["code"].split(".")[0] if "." in fm["code"] else fm["code"]
            scores[code] = {
                "score": fm.get("score"),
                "rating": fm.get("rating", ""),
                "action": fm.get("action", ""),
                "alert_context": fm.get("alert_context", ""),
                "updated": fm.get("updated", ""),
            }
    return scores


def _parse_frontmatter(text: str) -> Optional[dict]:
    if not text.startswith("---"):
        return None
    end = text.find("---", 3)
    if end == -1:
        return None
    try:
        return yaml.safe_load(text[3:end])
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Thesis-Analysis sync
# ═══════════════════════════════════════════════════════════════════════════

def _parse_analysis_date(text: str) -> Optional[str]:
    m = re.search(r"\*\*分析日期\*\*[：:]\s*(\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else None


def _parse_linked_theses(text: str) -> list[str]:
    codes = []
    for m in re.finditer(r"theses/(\d+)_thesis\.md", text):
        codes.append(m.group(1))
    return codes


def _check_thesis_analysis_sync(thesis_scores: dict[str, dict]) -> list[dict]:
    stale = []
    trades_dir = ROOT_DIR / "trades"
    if not trades_dir.exists():
        return stale

    for f in sorted(trades_dir.glob("agenda_analysis_*.md")):
        text = f.read_text(encoding="utf-8")
        analysis_date = _parse_analysis_date(text)
        if not analysis_date:
            continue

        linked_codes = _parse_linked_theses(text)
        if not linked_codes:
            continue

        for code in linked_codes:
            t = thesis_scores.get(code)
            if not t or not t.get("updated"):
                continue
            thesis_updated = t["updated"]
            if isinstance(thesis_updated, str):
                thesis_updated = thesis_updated.strip()
            if str(thesis_updated) > str(analysis_date):
                stale.append({
                    "agenda_file": f.name,
                    "thesis_code": code,
                    "thesis_updated": str(thesis_updated),
                    "analysis_date": str(analysis_date),
                    "thesis_score": t.get("score"),
                    "alert_context": t.get("alert_context", ""),
                })

    return stale


# ═══════════════════════════════════════════════════════════════════════════
# Market Data
# ═══════════════════════════════════════════════════════════════════════════

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
        change_pct = (price - prev_close) / prev_close if prev_close else 0
        name = fields[1]
        return code, {
            "name": name,
            "price": price,
            "change_pct": change_pct,
            "high": float(fields[33]) if fields[33] else price,
            "low": float(fields[34]) if fields[34] else price,
            "volume": 0,
            "amount": 0,
        }
    except (ValueError, IndexError):
        return None


def fetch_prices_batch(items: list[tuple[str, str]]) -> dict[str, Optional[dict]]:
    if not items:
        return {}
    tc_list = [_tencent_code(code, market) for code, market in items]
    url = "https://qt.gtimg.cn/q=" + ",".join(tc_list)
    result: dict[str, Optional[dict]] = {code: None for code, _ in items}
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
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
    result = fetch_prices_batch([(code, market)])
    return result.get(code)


# ═══════════════════════════════════════════════════════════════════════════
# Calculations
# ═══════════════════════════════════════════════════════════════════════════

def calc_holding(holding: dict, quote: Optional[dict]) -> dict:
    shares = holding["shares"]
    cost = holding["cost_price"]
    price = quote["price"] if quote else holding["current_price"]

    market_value = shares * price
    cost_total = shares * cost
    pnl = market_value - cost_total
    pnl_pct = (price - cost) / cost if cost else 0.0

    return {
        **holding,
        "current_price": price,
        "market_value": market_value,
        "cost_total": cost_total,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "quote": quote,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Alert Checking
# ═══════════════════════════════════════════════════════════════════════════

def _get_severity(action: str) -> str:
    return ACTION_SEVERITY_MAP.get(action, "info")


def check_alerts(rules: dict, c_positions: list[dict], total_c_value: float) -> list[dict]:
    triggered = []

    pr = rules.get("portfolio_rules", {})
    sr = rules.get("stock_rules", {})

    # ── 1) 单股回撤（三档） ──
    sl = sr.get("stop_loss", {})
    l1_cfg = sl.get("level_1_alert", {})
    l2_cfg = sl.get("level_2_review", {})
    l3_cfg = sl.get("level_3_hard", {})

    for p in c_positions:
        pnl = p["pnl_pct"]

        l3_thresh = l3_cfg.get("threshold")
        if l3_thresh is None:
            _warn_once("stock_rules.stop_loss.level_3_hard.threshold")
            l3_thresh = -0.30

        if pnl <= l3_thresh:
            l3_action = l3_cfg.get("action")
            if l3_action is None:
                _warn_once("stock_rules.stop_loss.level_3_hard.action")
                l3_action = "force_review"
            triggered.append({
                "type": "single_stock_drawdown_l3",
                "severity": _get_severity(l3_action),
                "code": p["code"], "name": p["name"],
                "message": f"{p['name']}({p['code']}) 回撤 {_fmt_pct(pnl)}，"
                f"触发 L3 强制线 {_fmt_pct(l3_thresh)}，"
                f"thesis<3.0 需减仓 50%，thesis≥3.0 只维持不补仓",
            })
            continue

        l2_thresh = l2_cfg.get("threshold")
        if l2_thresh is None:
            _warn_once("stock_rules.stop_loss.level_2_review.threshold")
            l2_thresh = -0.20

        if pnl <= l2_thresh:
            l2_action = l2_cfg.get("action")
            if l2_action is None:
                _warn_once("stock_rules.stop_loss.level_2_review.action")
                l2_action = "trigger_ic_memo"
            triggered.append({
                "type": "single_stock_drawdown_l2",
                "severity": _get_severity(l2_action),
                "code": p["code"], "name": p["name"],
                "message": f"{p['name']}({p['code']}) 回撤 {_fmt_pct(pnl)}，"
                f"触发 L2 审视线 {_fmt_pct(l2_thresh)}，"
                f"需 7 天内更新 thesis 并给出决策",
            })
            continue

        l1_thresh = l1_cfg.get("threshold")
        if l1_thresh is None:
            _warn_once("stock_rules.stop_loss.level_1_alert.threshold")
            l1_thresh = -0.10

        if pnl <= l1_thresh:
            l1_action = l1_cfg.get("action")
            if l1_action is None:
                _warn_once("stock_rules.stop_loss.level_1_alert.action")
                l1_action = "alert_only"
            triggered.append({
                "type": "single_stock_drawdown_l1",
                "severity": _get_severity(l1_action),
                "code": p["code"], "name": p["name"],
                "message": f"{p['name']}({p['code']}) 回撤 {_fmt_pct(pnl)}，"
                f"触发 L1 关注线 {_fmt_pct(l1_thresh)}，"
                f"记录原因，补仓需 thesis ≥ 4.0",
            })

    # ── 2) 单股仓位超标 ──
    pos_cfg = pr.get("concentration", {}).get("single_stock_max", {})
    pos_threshold = pos_cfg.get("threshold")
    if pos_threshold is None:
        _warn_once("portfolio_rules.concentration.single_stock_max.threshold")
        pos_threshold = 0.25
    pos_action = pos_cfg.get("action")
    if pos_action is None:
        _warn_once("portfolio_rules.concentration.single_stock_max.action")
        pos_action = "force_reduce"

    if total_c_value > 0:
        for p in c_positions:
            ratio = p["market_value"] / total_c_value
            if ratio > pos_threshold:
                triggered.append({
                    "type": "single_stock_position",
                    "severity": _get_severity(pos_action),
                    "code": p["code"], "name": p["name"],
                    "message": f"{p['name']}({p['code']}) 仓位 {_fmt_pct(ratio)}，"
                    f"超阈值 {_fmt_pct(pos_threshold)}，需强制降权",
                })

    # ── 3) 账户总回撤（三档） ──
    total_cost = sum(p["cost_total"] for p in c_positions)
    total_value = sum(p["market_value"] for p in c_positions)
    if total_cost > 0:
        account_dd = (total_value - total_cost) / total_cost
        dc = pr.get("drawdown_control", {})
        d1 = dc.get("level_1_alert", {})
        d2 = dc.get("level_2_control", {})
        d3 = dc.get("level_3_hard", {})

        d3_thresh = d3.get("threshold")
        if d3_thresh is None:
            _warn_once("portfolio_rules.drawdown_control.level_3_hard.threshold")
            d3_thresh = 0.20

        if account_dd <= -abs(d3_thresh):
            d3_action = d3.get("action")
            if d3_action is None:
                _warn_once("portfolio_rules.drawdown_control.level_3_hard.action")
                d3_action = "force_reduce"
            triggered.append({
                "type": "account_drawdown_l3",
                "severity": _get_severity(d3_action),
                "code": "", "name": "账户",
                "message": f"C 仓位总回撤 {_fmt_pct(account_dd)}，触发 L3 硬刹车 "
                f"{_fmt_pct(-abs(d3_thresh))}，"
                f"停止所有新建仓 60 天，C 仓位降至 ≤25%",
            })
        else:
            d2_thresh = d2.get("threshold")
            if d2_thresh is None:
                _warn_once("portfolio_rules.drawdown_control.level_2_control.threshold")
                d2_thresh = 0.15

            if account_dd <= -abs(d2_thresh):
                d2_action = d2.get("action")
                if d2_action is None:
                    _warn_once("portfolio_rules.drawdown_control.level_2_control.action")
                    d2_action = "force_review"
                triggered.append({
                    "type": "account_drawdown_l2",
                    "severity": _get_severity(d2_action),
                    "code": "", "name": "账户",
                    "message": f"C 仓位总回撤 {_fmt_pct(account_dd)}，触发 L2 控制线 "
                    f"{_fmt_pct(-abs(d2_thresh))}，"
                    f"暂停新建个股 30 天，禁止补仓",
                })
            else:
                d1_thresh = d1.get("threshold")
                if d1_thresh is None:
                    _warn_once("portfolio_rules.drawdown_control.level_1_alert.threshold")
                    d1_thresh = 0.10

                if account_dd <= -abs(d1_thresh):
                    d1_action = d1.get("action")
                    if d1_action is None:
                        _warn_once("portfolio_rules.drawdown_control.level_1_alert.action")
                        d1_action = "alert_only"
                    triggered.append({
                        "type": "account_drawdown_l1",
                        "severity": _get_severity(d1_action),
                        "code": "", "name": "账户",
                        "message": f"C 仓位总回撤 {_fmt_pct(account_dd)}，触发 L1 预警线 "
                        f"{_fmt_pct(-abs(d1_thresh))}，"
                        f"组合复盘，补仓需 thesis ≥ 4.0",
                    })

    # ── 4) 主题集中度 ──
    theme_cfg = pr.get("theme_concentration", {})
    if theme_cfg and total_c_value > 0:
        energy_theme = theme_cfg.get("new_energy_and_power_chain", {})
        energy_industries = set(energy_theme.get("includes", []))
        energy_threshold = energy_theme.get("threshold")
        if energy_threshold is None:
            _warn_once("portfolio_rules.theme_concentration.new_energy_and_power_chain.threshold")
            energy_threshold = 0.35
        energy_action = energy_theme.get("action")
        if energy_action is None:
            _warn_once("portfolio_rules.theme_concentration.new_energy_and_power_chain.action")
            energy_action = "warning"

        energy_value = 0.0
        for p in c_positions:
            if p.get("industry", "") in energy_industries:
                energy_value += p["market_value"]
        if energy_value > 0:
            energy_ratio = energy_value / total_c_value
            if energy_ratio > energy_threshold:
                triggered.append({
                    "type": "theme_concentration",
                    "severity": _get_severity(energy_action),
                    "code": "", "name": "新能源/电力链",
                    "message": f"新能源/电力链主题占 C 仓位 {_fmt_pct(energy_ratio)}，"
                    f"超阈值 {_fmt_pct(energy_threshold)}，涵盖 {len([p for p in c_positions if p.get('industry','') in energy_industries])} 只个股",
                })

    return triggered


def check_etf_alerts(rules: dict, etf_positions: list[dict], total_etf_value: float) -> list[dict]:
    triggered = []
    monitoring = rules.get("monitoring", {})

    dd_warn = monitoring.get("etf_drawdown_warn")
    if dd_warn is None:
        _warn_once("monitoring.etf_drawdown_warn")
        dd_warn = 0.20
    dd_threshold = -abs(dd_warn)

    drift_threshold = monitoring.get("etf_drift_threshold")
    if drift_threshold is None:
        _warn_once("monitoring.etf_drift_threshold")
        drift_threshold = 0.05

    for e in etf_positions:
        pnl_pct = e.get("pnl_pct")
        if pnl_pct is not None and pnl_pct <= dd_threshold:
            triggered.append({
                "type": "etf_drawdown",
                "severity": "warning",
                "code": e["code"],
                "name": e["name"],
                "message": f"{e['name']}({e['code']}) B档 ETF 回撤 {_fmt_pct(pnl_pct)}，"
                f"超关注线 {_fmt_pct(dd_threshold)}，建议关注是否触发再平衡",
            })

        drift = e.get("drift_raw")
        if drift is not None and abs(drift) > drift_threshold:
            direction = "超配" if drift > 0 else "低配"
            triggered.append({
                "type": "etf_drift",
                "severity": "info",
                "code": e["code"],
                "name": e["name"],
                "message": f"{e['name']}({e['code']}) {direction} {_fmt_pct(abs(drift))}，"
                f"偏离目标 {_fmt_pct(e['target_ratio'])}，建议关注再平衡时机",
            })

    return triggered


# ═══════════════════════════════════════════════════════════════════════════
# Meituan RSU Monitoring
# ═══════════════════════════════════════════════════════════════════════════

def check_meituan_rsu_alerts(capital: dict, current_hk_price: float) -> list[dict]:
    triggered = []
    rsu_shares = capital.get("meituan_rsu_shares", 0)
    if rsu_shares <= 0:
        return triggered

    prev_value = capital.get("meituan_rsu_value", 0)
    current_value = rsu_shares * current_hk_price

    if prev_value > 0:
        change_pct = (current_value - prev_value) / prev_value

        if change_pct <= -0.15:
            triggered.append({
                "type": "meituan_rsu_drawdown",
                "severity": "warning",
                "code": "03690",
                "name": "美团RSU",
                "message": (
                    f"美团RSU市值变动 {_fmt_pct(change_pct)}，"
                    f"从 ¥{prev_value:,.0f} 降至 ¥{current_value:,.0f}，"
                    f"超过 15% 关注线"
                ),
            })
        elif change_pct <= -0.05:
            triggered.append({
                "type": "meituan_rsu_daily_drop",
                "severity": "info",
                "code": "03690",
                "name": "美团RSU",
                "message": f"美团RSU单日变动 {_fmt_pct(change_pct)}，关注",
            })

    return triggered


# ═══════════════════════════════════════════════════════════════════════════
# Historical Time-Series
# ═══════════════════════════════════════════════════════════════════════════

def append_portfolio_ts(date_str: str, etf_positions: list[dict],
                         c_positions: list[dict], cash_positions: list[dict],
                         meituan_price: float = 0) -> None:
    headers = ["date", "code", "type", "name", "shares", "price", "market_value", "pnl_pct"]
    file_exists = PORTFOLIO_TS_PATH.exists()

    with open(PORTFOLIO_TS_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(headers)

        for e in etf_positions:
            writer.writerow([
                date_str, e["code"], "B", e["name"],
                e.get("shares", 0),
                f"{e.get('price', 0):.3f}" if e.get("price") else "",
                f"{e.get('market_value', 0):.2f}" if e.get("market_value") else "",
                f"{e.get('pnl_pct', 0) * 100:.2f}" if e.get("pnl_pct") is not None else "",
            ])

        for p in c_positions:
            writer.writerow([
                date_str, p["code"], "C", p["name"],
                p.get("shares", 0),
                f"{p.get('current_price', 0):.3f}",
                f"{p.get('market_value', 0):.2f}",
                f"{p.get('pnl_pct', 0) * 100:.2f}",
            ])

        for r in cash_positions:
            pos_type = "D" if r.get("code") == "MTRSU" else "A"
            if r.get("code") == "MTRSU" and meituan_price > 0:
                shares = r.get("balance", 0)
                mv = shares * meituan_price
                price_str = f"{meituan_price:.3f}"
            else:
                shares = r.get("balance", 0)
                price_str = ""
                mv = r.get("current_value", 0)
            writer.writerow([
                date_str, r["code"], pos_type, r["name"],
                shares, price_str, f"{mv:.2f}", "",
            ])
