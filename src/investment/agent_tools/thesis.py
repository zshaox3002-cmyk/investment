"""Wrappers for: inv thesis sync/list/score/stale."""
from __future__ import annotations

from .base import ToolResult
from ._runner import run_inv


def thesis_sync() -> ToolResult:
    """同步 theses/*.md frontmatter → DB。"""
    success, output = run_inv("thesis", "sync")
    if success:
        human = (
            "论点文件已同步到数据库。\n"
            "所以你该做什么：运行 thesis_stale() 检查哪些论点超期未更新。"
        )
    else:
        human = f"论点同步失败。\n所以你该做什么：检查 theses/ 目录下的 Markdown 文件格式。\n{output[-300:]}"
    return ToolResult(success=success, data={"raw": output}, human_message=human, raw_output=output)


def thesis_list() -> ToolResult:
    """列出所有论点及评分。"""
    success, output = run_inv("thesis", "list")
    if success:
        human = "已列出所有持仓论点和当前评分。\n所以你该做什么：关注评分低于 2.5 的持仓，考虑是否需要重新评估。"
    else:
        human = f"无法列出论点。\n{output[-300:]}"
    return ToolResult(success=success, data={"raw": output}, human_message=human, raw_output=output)


def thesis_score(code: str, score: float, dimension: str | None = None, rationale: str = "") -> ToolResult:
    """写入月度评分。score: 1.0-5.0。"""
    args = ["thesis", "score", code, "--score", str(score)]
    if dimension:
        args += ["--dimension", dimension]
    if rationale:
        args += ["--rationale", rationale]
    success, output = run_inv(*args)
    if success:
        human = (
            f"{code} 论点评分已更新为 {score}。\n"
            "所以你该做什么：如评分低于 2.5，考虑减仓或重新审查 thesis。"
        )
    else:
        human = f"评分更新失败（{code}）。\n所以你该做什么：确认股票代码存在于 theses/ 目录。\n{output[-300:]}"
    return ToolResult(success=success, data={"code": code, "score": score, "raw": output},
                      human_message=human, raw_output=output)


def thesis_stale(days: int = 30) -> ToolResult:
    """列出超期未更新的论点。"""
    success, output = run_inv("thesis", "stale", "--days", str(days))
    stale_count = output.count("│") // 4 if "│" in output else 0
    if success:
        if stale_count > 0:
            human = (
                f"有 {stale_count} 个论点超过 {days} 天未更新。\n"
                "所以你该做什么：逐一运行 thesis_score() 更新评分，或重新审查对应的 thesis.md 文件。"
            )
        else:
            human = f"所有论点均在 {days} 天内更新过，无需操作。"
    else:
        human = f"无法检查过期论点。\n{output[-300:]}"
    return ToolResult(success=success, data={"days": days, "raw": output},
                      human_message=human, raw_output=output)
