"""Wrappers for: inv data tables."""
from __future__ import annotations

from .base import ToolResult
from ._runner import run_inv


def data_tables() -> ToolResult:
    """列出数据库中所有表和视图。"""
    success, output = run_inv("data", "tables")
    tables: list[str] = []
    if success:
        for line in output.splitlines():
            # Only pick lines that are table rows: start with │ after stripping
            raw = line.strip()
            if raw.startswith("│"):
                cell = raw.strip("│").strip()
                if cell and cell != "name":
                    tables.append(cell)
        human = (
            f"数据库共有 {len(tables)} 个对象（表 + 视图）。\n"
            "所以你该做什么：确认业务表数量 ≥ 25，视图数量 ≥ 3，否则可能需要重新执行 migrate_run()。"
        )
    else:
        human = f"无法读取数据库表清单。\n所以你该做什么：检查数据库文件是否存在，或运行 migrate_run() 初始化。\n{output[-300:]}"
    return ToolResult(
        success=success,
        data={"tables": tables, "count": len(tables)},
        human_message=human,
        raw_output=output,
    )
