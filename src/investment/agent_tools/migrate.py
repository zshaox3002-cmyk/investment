"""Wrappers for: inv migrate run / verify."""
from __future__ import annotations

from .base import ToolResult
from ._runner import run_inv


def migrate_run() -> ToolResult:
    """执行所有迁移脚本（幂等）。"""
    success, output = run_inv("migrate", "run", timeout=120)
    if success:
        human = (
            "数据库迁移已完成。\n"
            "所以你该做什么：运行 migrate_verify() 确认数据对账通过。"
        )
    else:
        human = (
            f"迁移执行失败。\n"
            f"所以你该做什么：查看以下错误信息，确认数据库文件权限和 SQL 语法：\n{output[-500:]}"
        )
    return ToolResult(success=success, data={"raw": output}, human_message=human, raw_output=output)


def migrate_verify() -> ToolResult:
    """执行 5 项数据对账，生成 diff_report.md。"""
    success, output = run_inv("migrate", "verify", timeout=60)
    if success:
        human = (
            "数据对账全部通过。\n"
            "所以你该做什么：底座数据完整，可以继续执行业务操作。"
        )
    else:
        human = (
            "部分对账项未通过，详见 data/migration_diff_report.md。\n"
            "所以你该做什么：打开报告查看具体差异，判断是否为预期偏差（如行情更新导致市值变化）。\n"
            f"输出摘要：\n{output[-400:]}"
        )
    return ToolResult(success=success, data={"raw": output}, human_message=human, raw_output=output)
