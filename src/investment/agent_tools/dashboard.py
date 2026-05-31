"""Wrappers for: inv dashboard render."""
from __future__ import annotations

from .base import ToolResult
from ._runner import run_inv


def dashboard_render(mode: str = "post-market") -> ToolResult:
    """生成战情室 HTML。mode: 'post-market'（默认）或 'pre-market'。"""
    if mode not in ("post-market", "pre-market"):
        return ToolResult(
            success=False,
            data={},
            human_message=f"mode 参数无效：{mode}。请使用 'post-market' 或 'pre-market'。",
        )
    success, output = run_inv("dashboard", "render", "--mode", mode, timeout=60)
    if success:
        human = (
            f"战情室（{mode}）已生成：DASHBOARD.html。\n"
            "所以你该做什么：用浏览器打开 DASHBOARD.html 查看完整持仓状态和告警。"
        )
    else:
        human = (
            f"战情室生成失败（{mode}）。\n"
            f"所以你该做什么：先确认已执行 snapshot_pull()，再查看错误：\n{output[-400:]}"
        )
    return ToolResult(success=success, data={"mode": mode, "raw": output}, human_message=human, raw_output=output)
