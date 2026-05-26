"""Candidate pool workflow.

inv candidate scan --source akshare|manual
inv candidate list [--priority N]
inv candidate promote ID --to ic_memo|rejected
"""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from investment.core.db import connect, transaction
from investment.core.settings import CONFIG_DIR, DATA_DIR

SCREENING_RULES_PATH = CONFIG_DIR / "screening_rules.yaml"


def _load_screening_rules() -> dict:
    import yaml
    if not SCREENING_RULES_PATH.exists():
        return _default_screening_rules()
    with open(SCREENING_RULES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or _default_screening_rules()


def _default_screening_rules() -> dict:
    return {
        "hard_filters": {
            "pe_ttm": {"max": 25},
            "roe_3y_avg": {"min": 0.10},
        },
        "compliance_check": {
            "exclude_st": True,
            "min_market_cap": 5_000_000_000,
        },
    }


def _check_compliance(row: dict, rules: dict, blocked_themes: set) -> tuple[bool, str]:
    """Return (passed, blocked_by_reason)."""
    comp = rules.get("compliance_check", {})
    if comp.get("exclude_st") and row.get("name", "").startswith("ST"):
        return False, "ST股"
    min_cap = comp.get("min_market_cap", 0)
    cap = row.get("market_cap") or 0
    if cap and cap < min_cap:
        return False, f"市值 {cap/1e8:.1f}亿 < {min_cap/1e8:.0f}亿"
    theme = row.get("theme", "") or ""
    if theme in blocked_themes:
        return False, f"主题超限: {theme}"
    return True, ""


def _score_candidate(row: dict, rules: dict) -> float:
    """Simple composite score 0-5 based on available metrics."""
    score = 0.0
    weights = rules.get("priority_weights", {
        "fundamental": 0.30, "valuation": 0.30,
        "complementarity": 0.20, "risk": 0.20,
    })

    # Fundamental: ROE
    roe = row.get("roe_3y_avg") or 0
    if roe >= 0.20:
        score += 5 * weights.get("fundamental", 0.3)
    elif roe >= 0.15:
        score += 4 * weights.get("fundamental", 0.3)
    elif roe >= 0.10:
        score += 3 * weights.get("fundamental", 0.3)

    # Valuation: PE
    pe = row.get("pe_ttm") or 999
    if pe <= 10:
        score += 5 * weights.get("valuation", 0.3)
    elif pe <= 15:
        score += 4 * weights.get("valuation", 0.3)
    elif pe <= 20:
        score += 3 * weights.get("valuation", 0.3)
    elif pe <= 25:
        score += 2 * weights.get("valuation", 0.3)

    # Dividend yield
    div = row.get("dividend_yield") or 0
    if div >= 0.04:
        score += 5 * weights.get("complementarity", 0.2)
    elif div >= 0.02:
        score += 3 * weights.get("complementarity", 0.2)

    return round(min(score, 5.0), 2)


def _get_blocked_themes(db_path=None) -> set:
    """Get themes blocked by active rule_breaches."""
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT notes FROM rule_breaches WHERE rule_path='theme_concentration' AND status IN ('active','remediating')"
    ).fetchall()
    conn.close()
    # Simple heuristic: extract theme names from notes
    blocked = set()
    for r in rows:
        notes = r["notes"] or ""
        if "新能源" in notes:
            blocked.add("新能源/电力链")
    return blocked


def scan_akshare(month: Optional[str] = None, quick: bool = False, db_path=None) -> int:
    """Scan A-share market using akshare. Returns number of candidates inserted."""
    try:
        import akshare as ak
    except ImportError:
        print("  [错误] akshare 未安装，请运行: pip install akshare")
        return 0

    scan_date = month or date.today().isoformat()[:7] + "-01"
    rules = _load_screening_rules()
    blocked_themes = _get_blocked_themes(db_path)
    hard = rules.get("hard_filters", {})

    print("  正在从 akshare 拉取 A 股基本面数据...")
    try:
        # Get stock list with basic financials
        df = ak.stock_zh_a_spot_em()
        if quick:
            df = df.head(200)  # quick mode: sample only
        print(f"  获取到 {len(df)} 只股票")
    except Exception as e:
        print(f"  [错误] akshare 数据获取失败: {e}")
        return 0

    inserted = 0
    today = date.today().isoformat()

    with transaction(db_path) as conn:
        for _, row in df.iterrows():
            try:
                code = str(row.get("代码", "")).strip()
                name = str(row.get("名称", "")).strip()
                if not code or not name:
                    continue

                # Basic hard filters using available columns
                pe = None
                try:
                    pe_val = row.get("市盈率-动态")
                    pe = float(pe_val) if pe_val and str(pe_val) not in ("None", "-", "") else None
                except (ValueError, TypeError):
                    pe = None

                # PE hard filter
                pe_max = hard.get("pe_ttm", {}).get("max", 25)
                if pe is not None and (pe <= 0 or pe > pe_max):
                    continue

                # Market cap filter
                cap = None
                try:
                    cap_val = row.get("总市值")
                    cap = float(cap_val) if cap_val else None
                except (ValueError, TypeError):
                    cap = None

                min_cap = rules.get("compliance_check", {}).get("min_market_cap", 5e9)
                if cap is not None and cap < min_cap:
                    continue

                # ST filter
                if name.startswith("ST") or name.startswith("*ST"):
                    continue

                # Compute composite score
                candidate_row = {
                    "code": code, "name": name,
                    "pe_ttm": pe, "market_cap": cap,
                    "roe_3y_avg": None,  # not available in spot data
                    "dividend_yield": None,
                    "theme": None,
                }
                comp_score = _score_candidate(candidate_row, rules)
                passed, blocked_by = _check_compliance(candidate_row, rules, blocked_themes)

                conn.execute(
                    """INSERT OR IGNORE INTO candidates
                       (scan_date, code, market, name, market_cap, pe_ttm,
                        composite_score, compliance_passed, compliance_blocked_by,
                        status, source_scan)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (today, code, "A", name, cap, pe,
                     comp_score, 1 if passed else 0,
                     blocked_by or None, "candidate", "akshare_spot"),
                )
                inserted += conn.execute("SELECT changes()").fetchone()[0]
            except Exception:
                continue

    print(f"  写入候选池: {inserted} 条")
    return inserted


def scan_manual(csv_path: str, db_path=None) -> int:
    """Load candidates from a CSV file (manual mode)."""
    path = Path(csv_path)
    if not path.exists():
        print(f"  [错误] 文件不存在: {csv_path}")
        return 0

    today = date.today().isoformat()
    inserted = 0
    rules = _load_screening_rules()
    blocked_themes = _get_blocked_themes(db_path)

    with transaction(db_path) as conn:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                code = row.get("code", "").strip()
                name = row.get("name", "").strip()
                if not code or not name:
                    continue
                try:
                    pe = float(row["pe_ttm"]) if row.get("pe_ttm") else None
                    roe = float(row["roe_3y_avg"]) if row.get("roe_3y_avg") else None
                    div = float(row["dividend_yield"]) if row.get("dividend_yield") else None
                    cap = float(row["market_cap"]) if row.get("market_cap") else None
                except (ValueError, TypeError):
                    pe = roe = div = cap = None

                candidate_row = {
                    "code": code, "name": name, "pe_ttm": pe,
                    "roe_3y_avg": roe, "dividend_yield": div,
                    "market_cap": cap, "theme": row.get("theme", ""),
                }
                comp_score = _score_candidate(candidate_row, rules)
                passed, blocked_by = _check_compliance(candidate_row, rules, blocked_themes)

                conn.execute(
                    """INSERT OR IGNORE INTO candidates
                       (scan_date, code, market, name, market_cap, pe_ttm,
                        roe_3y_avg, dividend_yield, composite_score,
                        compliance_passed, compliance_blocked_by, status, source_scan)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (today, code, row.get("market", "A"), name, cap, pe,
                     roe, div, comp_score, 1 if passed else 0,
                     blocked_by or None, "candidate", "manual_csv"),
                )
                inserted += conn.execute("SELECT changes()").fetchone()[0]

    print(f"  写入候选池: {inserted} 条")
    return inserted


def list_candidates(
    priority: Optional[int] = None,
    status: str = "candidate",
    limit: int = 50,
    db_path=None,
) -> list[dict]:
    conn = connect(db_path)
    where = f"WHERE c.status='{status}'" if status != "all" else "WHERE 1=1"
    if priority is not None:
        where += f" AND c.priority<={priority}"
    rows = conn.execute(
        f"""SELECT c.id, c.scan_date, c.code, c.name, c.market,
                   c.pe_ttm, c.roe_3y_avg, c.dividend_yield,
                   c.composite_score, c.compliance_passed,
                   c.compliance_blocked_by, c.status, c.priority
            FROM candidates c
            {where}
            ORDER BY c.composite_score DESC NULLS LAST, c.scan_date DESC
            LIMIT {limit}"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def promote_candidate(candidate_id: int, to_status: str, db_path=None) -> bool:
    valid = {"ic_memo", "accepted", "rejected", "researching", "expired"}
    if to_status not in valid:
        raise ValueError(f"Invalid status: {to_status}. Must be one of {valid}")
    with transaction(db_path) as conn:
        conn.execute(
            "UPDATE candidates SET status=? WHERE id=?",
            (to_status, candidate_id),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0
