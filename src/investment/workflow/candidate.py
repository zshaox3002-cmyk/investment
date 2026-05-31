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


def _patch_requests_proxy() -> None:
    """Patch requests to bypass proxy for CN data sources (eastmoney, gtimg, qq)."""
    import re
    import requests.utils
    if getattr(requests.utils, "_cn_proxy_patched", False):
        return
    _orig = requests.utils.get_environ_proxies
    def _patched(url, no_proxy=None):
        if re.search(r"(eastmoney|gtimg|qq\.com|10jqka|ths\.com)", url or ""):
            return {}
        return _orig(url, no_proxy)
    requests.utils.get_environ_proxies = _patched
    requests.utils._cn_proxy_patched = True


def _fetch_ths_financials(code: str, ak, timeout_sec: int = 8) -> dict:
    """Fetch latest ROE from THS (同花顺) for a single stock.

    Anti-scraping measures:
    - 8s hard timeout via SIGALRM
    - Returns {} on any failure (timeout / rate-limit / parse error)
    - Caller is responsible for inter-request delay
    """
    import signal

    def _handler(signum, frame):
        raise TimeoutError()

    signal.signal(signal.SIGALRM, _handler)
    signal.alarm(timeout_sec)
    try:
        df = ak.stock_financial_abstract_ths(symbol=code)
        signal.alarm(0)
        if df is None or df.empty:
            return {}
        # Get most recent row with valid ROE
        for _, row in df.iterrows():
            roe_raw = row.get("净资产收益率-摊薄", "")
            if roe_raw and str(roe_raw) not in ("False", "", "nan"):
                try:
                    roe = float(str(roe_raw).replace("%", "")) / 100
                    growth_raw = row.get("净利润同比增长率", "")
                    growth = None
                    if growth_raw and str(growth_raw) not in ("False", "", "nan"):
                        growth = float(str(growth_raw).replace("%", "")) / 100
                    return {"roe_latest": roe, "net_profit_growth": growth}
                except (ValueError, TypeError):
                    continue
        return {}
    except TimeoutError:
        signal.alarm(0)
        return {}
    except Exception:
        signal.alarm(0)
        return {}


def scan_akshare(month: Optional[str] = None, quick: bool = False, db_path=None) -> int:
    """Scan A-share market using akshare. Returns number of candidates inserted.

    Data sources:
      Primary:  stock_info_a_code_name (新浪多源) — full code+name list, stable
      Financial: stock_financial_abstract_ths (同花顺) — ROE per stock, on-demand
      Fallback: manual CSV via scan_manual()

    quick=True: sample 20 stocks, fetch financials for all of them (validation mode)
    quick=False: full 5500+ stocks, fetch financials only for non-ST candidates
    """
    import time
    _patch_requests_proxy()

    try:
        import akshare as ak
    except ImportError:
        print("  [错误] akshare 未安装，请运行: pip install akshare")
        return 0

    scan_date = month or date.today().isoformat()[:7] + "-01"
    rules = _load_screening_rules()
    blocked_themes = _get_blocked_themes(db_path)

    # ── Step 1: fetch full code+name list ─────────────────────────────────
    print("  [主] stock_info_a_code_name 拉取股票列表...")
    try:
        import signal
        def _h(s, f): raise TimeoutError()
        signal.signal(signal.SIGALRM, _h)
        signal.alarm(30)
        df = ak.stock_info_a_code_name()
        signal.alarm(0)
    except TimeoutError:
        signal.alarm(0)
        print("  [错误] 股票列表拉取超时(>30s)")
        return 0
    except Exception as e:
        print(f"  [错误] 股票列表拉取失败: {e}")
        return 0

    if quick:
        df = df.head(20)
    print(f"  获取到 {len(df)} 只股票，开始处理...")

    # ── Step 2: filter + fetch financials ─────────────────────────────────
    import random
    inserted = 0
    today = scan_date
    fetch_limit = len(df)

    # Anti-scraping: track consecutive failures for backoff
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 5  # stop fetching financials if too many timeouts

    with transaction(db_path) as conn:
        fetched = 0
        for _, row in df.iterrows():
            code = str(row.get("code", row.get("代码", ""))).strip()
            name = str(row.get("name", row.get("名称", ""))).strip()
            if not code or not name:
                continue
            if name.startswith("ST") or name.startswith("*ST") or name.startswith("XD"):
                continue

            # Fetch financials from THS (with rate limiting + backoff)
            financials = {}
            if fetched < fetch_limit and consecutive_failures < MAX_CONSECUTIVE_FAILURES:
                financials = _fetch_ths_financials(code, ak)
                fetched += 1
                if financials:
                    consecutive_failures = 0  # reset on success
                else:
                    consecutive_failures += 1

                if fetched % 5 == 0:
                    print(f"    财务数据进度: {fetched}/{fetch_limit}，连续失败: {consecutive_failures}")

                if not quick:
                    # Random delay 0.8-1.5s to avoid rate limiting
                    time.sleep(0.8 + random.random() * 0.7)
                else:
                    # Quick mode: minimal delay
                    time.sleep(0.1 + random.random() * 0.2)

            elif consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                # Too many failures — skip remaining financial fetches
                pass

            roe = financials.get("roe_latest")
            candidate_row = {
                "code": code, "name": name,
                "pe_ttm": None, "market_cap": None,
                "roe_3y_avg": roe,
                "dividend_yield": None, "theme": None,
            }
            comp_score = _score_candidate(candidate_row, rules)
            passed, blocked_by = _check_compliance(candidate_row, rules, blocked_themes)

            try:
                conn.execute(
                    """INSERT OR IGNORE INTO candidates
                       (scan_date, code, market, name, roe_3y_avg,
                        composite_score, compliance_passed, compliance_blocked_by,
                        status, source_scan)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (today, code, "A", name, roe,
                     comp_score, 1 if passed else 0,
                     blocked_by or None, "candidate", "akshare+ths"),
                )
                inserted += conn.execute("SELECT changes()").fetchone()[0]
            except Exception as e:
                print(f"  [warn] {code}: {e}")
                continue

    print(f"  写入候选池: {inserted} 条（财务数据拉取: {fetched} 只）")
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


def refresh_valuations(codes: list[str] | None = None, delay: float = 1.5, db_path=None) -> dict:
    """Refresh PE(TTM), market_cap, PB for candidates using stock_value_em (东方财富单股).

    Args:
        codes: specific codes to refresh; None = all candidates with status='candidate'
        delay: seconds between requests to avoid rate limiting
        db_path: override DB path

    Returns:
        dict with keys: updated, skipped, failed, errors
    """
    import time

    _patch_requests_proxy()

    try:
        import akshare as ak
    except ImportError:
        print("  [错误] akshare 未安装，请运行: pip install akshare")
        return {"updated": 0, "skipped": 0, "failed": 0, "errors": []}

    conn = connect(db_path)
    if codes:
        placeholders = ",".join("?" * len(codes))
        rows = conn.execute(
            f"SELECT id, code, name FROM candidates WHERE code IN ({placeholders})",
            codes,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, code, name FROM candidates WHERE status='candidate' AND market='A'"
        ).fetchall()
    conn.close()

    if not rows:
        print("  候选池为空，无需刷新")
        return {"updated": 0, "skipped": 0, "failed": 0, "errors": []}

    rules = _load_screening_rules()
    blocked_themes = _get_blocked_themes(db_path)

    updated = skipped = failed = 0
    errors: list[str] = []

    print(f"  刷新 {len(rows)} 只候选标的估值数据...")

    for i, row in enumerate(rows):
        cid, code, name = row["id"], row["code"], row["name"]
        # stock_value_em only supports 6-digit A-share codes
        if not code or len(code) != 6 or not code.isdigit():
            skipped += 1
            continue

        import signal

        def _handler(signum, frame):
            raise TimeoutError()

        signal.signal(signal.SIGALRM, _handler)
        signal.alarm(10)
        try:
            df = ak.stock_value_em(symbol=code)
            signal.alarm(0)
        except TimeoutError:
            signal.alarm(0)
            msg = f"{code} {name}: 超时"
            print(f"  [warn] {msg}")
            errors.append(msg)
            failed += 1
            continue
        except Exception as e:
            signal.alarm(0)
            msg = f"{code} {name}: {e}"
            print(f"  [warn] {msg}")
            errors.append(msg)
            failed += 1
            continue

        if df is None or df.empty:
            skipped += 1
            continue

        latest = df.iloc[-1]
        pe = latest.get("PE(TTM)")
        cap = latest.get("总市值")
        pb = latest.get("市净率")

        # Recompute composite score with refreshed data
        conn2 = connect(db_path)
        existing = conn2.execute(
            "SELECT roe_3y_avg, dividend_yield, theme FROM candidates WHERE id=?", (cid,)
        ).fetchone()
        conn2.close()

        roe = existing["roe_3y_avg"] if existing else None
        div = existing["dividend_yield"] if existing else None
        theme = existing["theme"] if existing else None

        candidate_row = {
            "code": code, "name": name,
            "pe_ttm": float(pe) if pe is not None else None,
            "roe_3y_avg": roe,
            "dividend_yield": div,
            "market_cap": float(cap) if cap is not None else None,
            "theme": theme,
        }
        new_score = _score_candidate(candidate_row, rules)
        passed, blocked_by = _check_compliance(candidate_row, rules, blocked_themes)

        with transaction(db_path) as conn3:
            conn3.execute(
                """UPDATE candidates
                   SET pe_ttm=?, market_cap=?, pb=?,
                       composite_score=?, compliance_passed=?, compliance_blocked_by=?,
                       scan_date=date('now')
                   WHERE id=?""",
                (
                    float(pe) if pe is not None else None,
                    float(cap) if cap is not None else None,
                    float(pb) if pb is not None else None,
                    new_score,
                    1 if passed else 0,
                    blocked_by or None,
                    cid,
                ),
            )

        updated += 1
        pe_str = f"{pe:.2f}" if pe is not None else "N/A"
        cap_str = f"{cap/1e8:.0f}亿" if cap is not None else "N/A"
        print(f"  [{i+1}/{len(rows)}] {code} {name}: PE={pe_str}, 市值={cap_str}, 评分={new_score:.2f}")

        if i < len(rows) - 1:
            time.sleep(delay)

    print(f"  完成: 更新={updated}, 跳过={skipped}, 失败={failed}")
    return {"updated": updated, "skipped": skipped, "failed": failed, "errors": errors}


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
