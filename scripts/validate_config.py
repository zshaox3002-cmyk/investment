#!/usr/bin/env python3
"""
validate_config.py — 配置自检脚本

用法:
    python scripts/validate_config.py          # 完整检查
    python scripts/validate_config.py --quick  # 仅 rules.yaml 结构检查

检查三类一致性：
  1. rules.yaml 结构完整性（必需路径、阈值范围、action 合法性）
  2. 数据文件完整性（holdings.csv / core_etf.csv 列、值、去重）
  3. 代码→配置交叉验证（daily_snapshot.py 读取的键是否在 rules.yaml 中存在）
"""

import csv
import os
import re
import sys
from pathlib import Path
from typing import Optional

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
CONFIG_DIR = ROOT_DIR / "config"
RULES_PATH = CONFIG_DIR / "rules.yaml"
HOLDINGS_PATH = CONFIG_DIR / "holdings.csv"
CORE_ETF_PATH = CONFIG_DIR / "core_etf.csv"
SNAPSHOT_PATH = SCRIPT_DIR / "daily_snapshot.py"

VALID_ACTIONS = {
    "force_reduce", "force_review", "force_exit",
    "trigger_ic_memo", "warning", "alert_only",
}
VALID_SEVERITIES = {"critical", "warning", "info"}


# ═══════════════════════════════════════════════════════════════════════
# 1. rules.yaml 结构完整性
# ═══════════════════════════════════════════════════════════════════════

REQUIRED_YAML_PATHS = [
    # (path_as_list, expected_type, value_constraint)
    # portfolio_rules - concentration
    (["portfolio_rules", "concentration", "single_stock_max", "threshold"], (int, float), (0, 1)),
    (["portfolio_rules", "concentration", "single_stock_max", "action"], str, VALID_ACTIONS),
    # portfolio_rules - sector
    (["portfolio_rules", "sector_concentration", "single_sector_max", "threshold"], (int, float), (0, 1)),
    # portfolio_rules - theme concentration (new)
    (["portfolio_rules", "theme_concentration", "new_energy_and_power_chain", "threshold"], (int, float), (0, 1)),
    # portfolio_rules - drawdown (three tiers)
    (["portfolio_rules", "drawdown_control", "level_1_alert", "threshold"], (int, float), (0, 1)),
    (["portfolio_rules", "drawdown_control", "level_2_control", "threshold"], (int, float), (0, 1)),
    (["portfolio_rules", "drawdown_control", "level_3_hard", "threshold"], (int, float), (0, 1)),
    # portfolio_rules - C 仓位总量
    (["portfolio_rules", "active_position_total", "hard_max"], (int, float), (0, 1)),
    # stock_rules - stop_loss (three tiers)
    (["stock_rules", "stop_loss", "level_1_alert", "threshold"], (int, float), (-1, 0)),
    (["stock_rules", "stop_loss", "level_2_review", "threshold"], (int, float), (-1, 0)),
    (["stock_rules", "stop_loss", "level_3_hard", "threshold"], (int, float), (-1, 0)),
    # active_position
    (["active_position", "holding_count_min"], int, (1, 50)),
    (["active_position", "holding_count_max"], int, (1, 50)),
    (["active_position", "single_stock_max"], (int, float), (0, 1)),
    (["active_position", "single_industry_max"], (int, float), (0, 1)),
    # monitoring
    (["monitoring", "etf_drawdown_warn"], (int, float), (0, 1)),
    (["monitoring", "etf_drift_threshold"], (int, float), (0, 1)),
]


def _get_nested(d: dict, path: list):
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def check_rules_structure(rules: dict) -> list[str]:
    """检查 rules.yaml 必需路径、类型、值范围。返回错误列表。"""
    errors = []

    for path, expected_type, constraint in REQUIRED_YAML_PATHS:
        val = _get_nested(rules, path)
        path_str = " → ".join(path)

        if val is None:
            errors.append(f"缺少必需键: {path_str}")
            continue

        if not isinstance(val, expected_type):
            errors.append(f"{path_str}: 类型错误，期望 {expected_type}，实际 {type(val).__name__}")
            continue

        if isinstance(constraint, tuple) and len(constraint) == 2:
            lo, hi = constraint
            if not (lo <= val <= hi):
                errors.append(f"{path_str}: 值 {val} 超出范围 [{lo}, {hi}]")
        elif isinstance(constraint, set):
            if val not in constraint:
                errors.append(f"{path_str}: 非法值 '{val}'，允许: {constraint}")

    # 交叉验证：active_position 的阈值应与 portfolio_rules 一致
    ap_single = _get_nested(rules, ["active_position", "single_stock_max"])
    pr_single = _get_nested(rules, ["portfolio_rules", "concentration", "single_stock_max", "threshold"])
    if ap_single is not None and pr_single is not None and ap_single != pr_single:
        errors.append(f"active_position.single_stock_max ({ap_single}) 与 "
                      f"portfolio_rules.concentration.single_stock_max.threshold ({pr_single}) 不一致")

    ap_industry = _get_nested(rules, ["active_position", "single_industry_max"])
    pr_industry = _get_nested(rules, ["portfolio_rules", "sector_concentration", "single_sector_max", "threshold"])
    if ap_industry is not None and pr_industry is not None and ap_industry != pr_industry:
        errors.append(f"active_position.single_industry_max ({ap_industry}) 与 "
                      f"portfolio_rules.sector_concentration.single_sector_max.threshold ({pr_industry}) 不一致")

    return errors


# ═══════════════════════════════════════════════════════════════════════
# 2. 数据文件完整性
# ═══════════════════════════════════════════════════════════════════════

def _read_csv(path: Path, required_cols: list[str]) -> tuple[list[dict], list[str]]:
    """读取 CSV，校验必需列。返回 (rows, errors)。"""
    errors = []
    if not path.exists():
        errors.append(f"文件不存在: {path}")
        return [], errors

    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        actual_cols = set(reader.fieldnames or [])
        missing = [c for c in required_cols if c not in actual_cols]
        if missing:
            errors.append(f"{path.name}: 缺少列 {missing}")
        rows = list(reader)

    return rows, errors


def check_holdings_csv() -> list[str]:
    """校验 holdings.csv。"""
    errors = []
    rows, errs = _read_csv(HOLDINGS_PATH, ["code", "name", "shares", "cost_price"])
    errors.extend(errs)
    if not rows:
        return errors

    codes_seen = set()
    for i, r in enumerate(rows, start=2):  # line 2+ (header is line 1)
        code = r.get("code", "").strip()
        if not code:
            continue
        if code in codes_seen:
            errors.append(f"holdings.csv 第 {i} 行: 重复代码 {code}")
        codes_seen.add(code)

        try:
            shares = float(r.get("shares", 0))
            if shares <= 0:
                errors.append(f"holdings.csv 第 {i} 行 ({code}): shares 必须 > 0")
        except ValueError:
            errors.append(f"holdings.csv 第 {i} 行 ({code}): shares 不是有效数字")

        try:
            cost = float(r.get("cost_price", 0))
            if cost <= 0:
                errors.append(f"holdings.csv 第 {i} 行 ({code}): cost_price 必须 > 0")
        except ValueError:
            errors.append(f"holdings.csv 第 {i} 行 ({code}): cost_price 不是有效数字")

    return errors


def check_core_etf_csv() -> list[str]:
    """校验 core_etf.csv。"""
    errors = []
    rows, errs = _read_csv(CORE_ETF_PATH, ["code", "name", "shares", "cost_price", "target_ratio"])
    errors.extend(errs)
    if not rows:
        return errors

    codes_seen = set()
    total_target = 0.0
    for i, r in enumerate(rows, start=2):
        code = r.get("code", "").strip()
        if not code:
            continue
        if code in codes_seen:
            errors.append(f"core_etf.csv 第 {i} 行: 重复代码 {code}")
        codes_seen.add(code)

        try:
            target = float(r.get("target_ratio", 0))
            total_target += target
        except ValueError:
            errors.append(f"core_etf.csv 第 {i} 行 ({code}): target_ratio 不是有效数字")

        try:
            shares = float(r.get("shares", 0))
            cost = float(r.get("cost_price", 0))
            if shares > 0 and cost <= 0:
                errors.append(f"core_etf.csv 第 {i} 行 ({code}): 有持仓但 cost_price 无效")
        except ValueError:
            errors.append(f"core_etf.csv 第 {i} 行 ({code}): shares/cost_price 不是有效数字")

    if abs(total_target - 1.0) > 0.02:
        errors.append(f"core_etf.csv: target_ratio 总和 {total_target:.2f}，偏离 1.0 超过 2%")

    return errors


def check_capital_yaml() -> list[str]:
    """校验 config/capital.yaml。"""
    errors = []
    path = CONFIG_DIR / "capital.yaml"
    if not path.exists():
        errors.append("capital.yaml 不存在，无法计算 C 仓位分配额和占比")
        return errors
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            errors.append("capital.yaml: 格式错误，应为 YAML 字典")
            return errors
        total = data.get("total_bc_capital", 0)
        if not isinstance(total, (int, float)) or total <= 0:
            errors.append(f"capital.yaml: total_bc_capital 必须为正数，当前值 {total}")
    except Exception as e:
        errors.append(f"capital.yaml: 解析失败 - {e}")
    return errors


def check_thesis_frontmatter() -> list[str]:
    """校验 theses/ 目录下所有 thesis 文件的 YAML frontmatter。"""
    errors = []
    theses_dir = ROOT_DIR / "theses"
    if not theses_dir.exists():
        return errors

    required_fields = ["code", "name", "score", "rating", "action"]
    for f in sorted(theses_dir.glob("*.md")):
        if f.name.startswith("_"):
            continue
        text = f.read_text(encoding="utf-8")
        if not text.startswith("---"):
            errors.append(f"{f.name}: 缺少 YAML frontmatter（文件应以 --- 开头）")
            continue
        end = text.find("---", 3)
        if end == -1:
            errors.append(f"{f.name}: YAML frontmatter 未闭合（缺少结尾的 ---）")
            continue
        try:
            fm = yaml.safe_load(text[3:end])
        except Exception as e:
            errors.append(f"{f.name}: frontmatter YAML 解析失败 - {e}")
            continue
        if not isinstance(fm, dict):
            errors.append(f"{f.name}: frontmatter 应为 YAML 字典")
            continue
        for field in required_fields:
            if field not in fm or fm[field] is None:
                errors.append(f"{f.name}: frontmatter 缺少必需字段 '{field}'")
        score = fm.get("score")
        if score is not None and not (0 <= score <= 5):
            errors.append(f"{f.name}: score 应为 0-5 之间的数值，当前值 {score}")

    return errors


# ═══════════════════════════════════════════════════════════════════════
# 3. 代码→配置交叉验证
# ═══════════════════════════════════════════════════════════════════════

# 解析 daily_snapshot.py 中使用的 rules.yaml 键路径（通过正则匹配）
# 匹配模式: rules.get("X", ...).get("Y", ...).get("Z", ...)
RULES_GET_PATTERN = re.compile(
    r'rules(?:\["([^"]+)"\]|\.get\("([^"]+)"[^)]*\))'
)

# 代码中应从 rules.yaml 读取的键路径 → 期望的默认值（用于一致性对比）
EXPECTED_CONFIG_KEYS = {
    # check_alerts() — single stock position
    "portfolio_rules.concentration.single_stock_max.threshold": 0.25,
    "portfolio_rules.concentration.single_stock_max.action": "force_reduce",
    # check_alerts() — stop_loss (three tiers)
    "stock_rules.stop_loss.level_1_alert.threshold": -0.10,
    "stock_rules.stop_loss.level_2_review.threshold": -0.20,
    "stock_rules.stop_loss.level_3_hard.threshold": -0.30,
    # check_alerts() — drawdown (three tiers)
    "portfolio_rules.drawdown_control.level_1_alert.threshold": 0.10,
    "portfolio_rules.drawdown_control.level_2_control.threshold": 0.15,
    "portfolio_rules.drawdown_control.level_3_hard.threshold": 0.20,
    # check_alerts() — theme concentration
    "portfolio_rules.theme_concentration.new_energy_and_power_chain.threshold": 0.35,
    # generate_report() 合规检查
    "active_position.holding_count_min": 5,
    "active_position.holding_count_max": 8,
    "portfolio_rules.concentration.single_stock_max.threshold": 0.25,
    "portfolio_rules.sector_concentration.single_sector_max.threshold": 0.40,
    # check_etf_alerts()
    "monitoring.etf_drawdown_warn": 0.20,
    "monitoring.etf_drift_threshold": 0.05,
}


def check_code_config_consistency() -> list[str]:
    """验证代码中引用的配置键在 rules.yaml 中存在且值一致。"""
    errors = []
    if not RULES_PATH.exists():
        return [f"rules.yaml 不存在: {RULES_PATH}"]

    with open(RULES_PATH, encoding="utf-8") as f:
        rules = yaml.safe_load(f)

    for key_path, expected_default in EXPECTED_CONFIG_KEYS.items():
        parts = key_path.split(".")
        val = _get_nested(rules, parts)
        if val is None:
            errors.append(f"代码引用 {key_path}，但 rules.yaml 中不存在此路径")
            continue
        # 类型宽松对比：YAML 中的 int/float vs 期望值
        if isinstance(expected_default, float) and isinstance(val, (int, float)):
            if abs(val - expected_default) > 0.001:
                errors.append(f"{key_path}: YAML 值 {val} ≠ 代码默认值 {expected_default}")
        elif val != expected_default:
            errors.append(f"{key_path}: YAML 值 {val} ≠ 代码默认值 {expected_default}")

    return errors


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main():
    quick_mode = "--quick" in sys.argv

    print("=" * 60)
    print("配置自检")
    print("=" * 60)

    all_errors: list[tuple[str, list[str]]] = []

    # 1) rules.yaml 结构
    if RULES_PATH.exists():
        with open(RULES_PATH, encoding="utf-8") as f:
            rules = yaml.safe_load(f)
        errors = check_rules_structure(rules)
        all_errors.append(("rules.yaml 结构完整性", errors))
    else:
        all_errors.append(("rules.yaml 结构完整性", [f"文件不存在: {RULES_PATH}"]))

    if quick_mode:
        errors = check_rules_structure(rules) if RULES_PATH.exists() else []
        all_errors = [("rules.yaml 结构完整性 (--quick)", errors)]
    else:
        # 2) 数据文件
        all_errors.append(("holdings.csv", check_holdings_csv()))
        all_errors.append(("core_etf.csv", check_core_etf_csv()))
        all_errors.append(("capital.yaml", check_capital_yaml()))
        all_errors.append(("thesis frontmatter", check_thesis_frontmatter()))

        # 3) 代码→配置一致性
        all_errors.append(("代码→配置交叉验证", check_code_config_consistency()))

    # ── 输出 ──
    total = 0
    for section, errors in all_errors:
        if errors:
            print(f"\n❌ {section} ({len(errors)} 个问题):")
            for e in errors:
                print(f"  - {e}")
            total += len(errors)
        else:
            print(f"\n✅ {section}: 通过")

    print(f"\n{'=' * 60}")
    if total == 0:
        print("🎉 全部检查通过")
    else:
        print(f"⚠ 共发现 {total} 个问题")
    print(f"{'=' * 60}")

    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
