"""Wrappers for: inv review log/stats."""
from __future__ import annotations

from .base import ToolResult
from ._runner import run_inv


def review_log(trade_id: int, error_code: str | None = None, notes: str = "") -> ToolResult:
    """记录单笔交易复盘。"""
    args = ["review", "log", "--trade-id", str(trade_id)]
    if error_code:
        args += ["--error-code", error_code]
    if notes:
        args += ["--notes", notes]
    success, output = run_inv(*args)
    if success:
        human = (
            f"交易 #{trade_id} 复盘已记录。\n"
            "所以你该做什么：定期运行 review_stats() 查看错误归因频次，识别自己的交易模式。"
        )
    else:
        human = f"复盘记录失败（#{trade_id}）。\n所以你该做什么：确认 trade_id 存在。\n{output[-300:]}"
    return ToolResult(success=success, data={"trade_id": trade_id, "raw": output},
                      human_message=human, raw_output=output)


def review_stats(months: int = 3) -> ToolResult:
    """错误归因频次统计。"""
    success, output = run_inv("review", "stats", "--months", str(months))
    if success:
        human = (
            f"过去 {months} 个月的交易错误归因统计已生成。\n"
            "所以你该做什么：关注频次最高的错误类型，在下次交易前主动检查是否重蹈覆辙。"
        )
    else:
        human = f"统计生成失败。\n{output[-300:]}"
    return ToolResult(success=success, data={"months": months, "raw": output},
                      human_message=human, raw_output=output)
