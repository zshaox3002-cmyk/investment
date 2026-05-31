"""Wrappers for: inv snapshot pull / show."""
from __future__ import annotations

from .base import ToolResult
from ._runner import run_inv


def snapshot_pull() -> ToolResult:
    """拉取今日行情，写入 quotes/holdings/alerts，执行风控检查。"""
    success, output = run_inv("snapshot", "pull", timeout=90)
    if success:
        human = (
            "今日行情已拉取完成，持仓市值和告警已更新。\n"
            "所以你该做什么：运行 dashboard_render() 生成战情室，查看是否有新告警需要处理。"
        )
    else:
        human = (
            f"行情拉取失败。\n"
            f"所以你该做什么：检查网络是否可达（qt.gtimg.cn），或查看以下错误信息：\n{output[-500:]}"
        )
    return ToolResult(success=success, data={"raw": output}, human_message=human, raw_output=output)


def snapshot_show(date: str | None = None) -> ToolResult:
    """查看某日日报告。date 格式 YYYY-MM-DD，不传则查今日。"""
    args = ["snapshot", "show"]
    if date:
        args.append(date)
    success, output = run_inv(*args)
    target = date or "今日"
    if success:
        human = f"{target} 的日报告已显示。\n所以你该做什么：关注告警和持仓变化，决定是否需要操作。"
    else:
        human = f"无法显示 {target} 的报告。\n所以你该做什么：确认该日期是否已执行过 snapshot pull。\n{output[-300:]}"
    return ToolResult(success=success, data={"date": target, "raw": output}, human_message=human, raw_output=output)
