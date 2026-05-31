"""Wrappers for: inv candidate scan/list/refresh/promote."""
from __future__ import annotations

from .base import ToolResult
from ._runner import run_inv


def candidate_scan(quick: bool = False, codes: str | None = None) -> ToolResult:
    """扫描候选池。quick=True 使用 akshare 快速模式。"""
    args = ["candidate", "scan"]
    if quick:
        args.append("--quick")
    if codes:
        args += ["--codes", codes]
    success, output = run_inv(*args, timeout=180)
    if success:
        human = (
            "候选池扫描完成。\n"
            "所以你该做什么：运行 candidate_list() 查看结果，对感兴趣的标的运行 /ic-memo 做深度分析。"
        )
    else:
        human = (
            f"候选池扫描失败。\n"
            f"所以你该做什么：检查网络连接，或使用 --quick 模式重试。\n{output[-400:]}"
        )
    return ToolResult(success=success, data={"raw": output}, human_message=human, raw_output=output)


def candidate_list() -> ToolResult:
    """查看候选池列表。"""
    success, output = run_inv("candidate", "list")
    if success:
        human = "已列出当前候选池。\n所以你该做什么：对评分高的候选标的运行 candidate_promote() 进入 IC Memo 研究流程。"
    else:
        human = f"无法列出候选池。\n{output[-300:]}"
    return ToolResult(success=success, data={"raw": output}, human_message=human, raw_output=output)


def candidate_refresh(codes: str | None = None, delay: int = 1) -> ToolResult:
    """刷新候选池 PE/市值/市净率数据。"""
    args = ["candidate", "refresh", "--delay", str(delay)]
    if codes:
        args += ["--codes", codes]
    success, output = run_inv(*args, timeout=120)
    if success:
        human = "候选池数据已刷新。\n所以你该做什么：重新查看 candidate_list() 确认估值数据已更新。"
    else:
        human = f"候选池刷新失败。\n{output[-300:]}"
    return ToolResult(success=success, data={"raw": output}, human_message=human, raw_output=output)


def candidate_promote(candidate_id: int) -> ToolResult:
    """将候选标的晋升为 IC Memo 研究对象。"""
    success, output = run_inv("candidate", "promote", str(candidate_id))
    if success:
        human = (
            f"候选标的 #{candidate_id} 已晋升为研究对象。\n"
            "所以你该做什么：运行 /ic-memo 完成买入决策备忘录，通过后才能建仓。"
        )
    else:
        human = f"晋升失败（#{candidate_id}）。\n所以你该做什么：确认 ID 存在于候选池。\n{output[-300:]}"
    return ToolResult(success=success, data={"candidate_id": candidate_id, "raw": output},
                      human_message=human, raw_output=output)
