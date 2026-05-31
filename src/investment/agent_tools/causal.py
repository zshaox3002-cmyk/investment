"""Wrappers for: inv causal * commands."""
from __future__ import annotations

from .base import ToolResult
from ._runner import run_inv


def causal_daily(dry_run: bool = False) -> ToolResult:
    """每日一键：scan → lifecycle → assess。"""
    args = ["causal", "daily"]
    if dry_run:
        args.append("--dry-run")
    success, output = run_inv(*args, timeout=180)
    if success:
        human = (
            "今日因果推理流水线已完成（扫描→生命周期→评估）。\n"
            "所以你该做什么：查看输出中的 L3+ 评估结论，关注对持仓有直接影响的信号。"
        )
    else:
        human = (
            f"因果推理流水线失败。\n"
            f"所以你该做什么：检查 ANTHROPIC_API_KEY 是否配置，或分步运行 causal_scan/causal_assess。\n{output[-400:]}"
        )
    return ToolResult(success=success, data={"dry_run": dry_run, "raw": output},
                      human_message=human, raw_output=output)


def causal_scan(dry_run: bool = False) -> ToolResult:
    """新闻抓取 → 去重 → LLM 分类 → 写信号表。"""
    args = ["causal", "scan"]
    if dry_run:
        args.append("--dry-run")
    success, output = run_inv(*args, timeout=120)
    if success:
        human = "新闻信号扫描完成，已写入 news_signals 表。\n所以你该做什么：运行 causal_assess() 评估信号对持仓的影响。"
    else:
        human = f"新闻扫描失败。\n所以你该做什么：检查网络和 API Key。\n{output[-400:]}"
    return ToolResult(success=success, data={"raw": output}, human_message=human, raw_output=output)


def causal_assess(code: str | None = None, explain: bool = False) -> ToolResult:
    """评估今日信号对持仓的因果影响（L3+ 写 DB）。"""
    args = ["causal", "assess"]
    if code:
        args += ["--code", code]
    if explain:
        args.append("--explain")
    success, output = run_inv(*args, timeout=120)
    if success:
        human = (
            "因果影响评估完成。\n"
            "所以你该做什么：关注输出中标注为 HIGH 或 MEDIUM 影响的信号，决定是否需要调整持仓。"
        )
    else:
        human = f"因果评估失败。\n{output[-400:]}"
    return ToolResult(success=success, data={"code": code, "raw": output},
                      human_message=human, raw_output=output)


def causal_discover(code: str, event: str) -> ToolResult:
    """LLM 自动发现因果路径。"""
    success, output = run_inv("causal", "discover", "--code", code, "--event", event, timeout=120)
    if success:
        human = (
            f"已为 {code} 发现因果路径（事件：{event}）。\n"
            "所以你该做什么：运行 causal_review_list() 审批新发现的边，批准后才会生效。"
        )
    else:
        human = f"因果路径发现失败（{code}）。\n{output[-400:]}"
    return ToolResult(success=success, data={"code": code, "event": event, "raw": output},
                      human_message=human, raw_output=output)


def causal_discover_auto() -> ToolResult:
    """自动扫描波动持仓并发现路径。"""
    success, output = run_inv("causal", "discover-auto", timeout=180)
    if success:
        human = (
            "自动路径发现完成。\n"
            "所以你该做什么：运行 causal_review_list() 查看待审批的新边。"
        )
    else:
        human = f"自动路径发现失败。\n{output[-400:]}"
    return ToolResult(success=success, data={"raw": output}, human_message=human, raw_output=output)


def causal_graph(code: str, fmt: str = "mermaid", hops: int = 2) -> ToolResult:
    """可视化因果子图。fmt: mermaid|json。"""
    success, output = run_inv("causal", "graph", "--code", code, "--format", fmt, "--hops", str(hops))
    if success:
        human = f"{code} 的因果子图（{hops} 跳，{fmt} 格式）已生成。\n所以你该做什么：查看图谱，理解影响该股的主要因果链路。"
    else:
        human = f"因果图谱生成失败（{code}）。\n{output[-300:]}"
    return ToolResult(success=success, data={"code": code, "format": fmt, "raw": output},
                      human_message=human, raw_output=output)


def causal_node_add(name: str, node_type: str, layer: str, description: str = "", keywords: str = "") -> ToolResult:
    """添加因果节点。"""
    args = ["causal", "node", "add", "--name", name, "--type", node_type, "--layer", layer]
    if description:
        args += ["--description", description]
    if keywords:
        args += ["--keywords", keywords]
    success, output = run_inv(*args)
    if success:
        human = f"因果节点 '{name}' 已添加（{layer}）。\n所以你该做什么：运行 causal_edge_add() 建立与其他节点的关联。"
    else:
        human = f"节点添加失败（{name}）。\n{output[-300:]}"
    return ToolResult(success=success, data={"name": name, "raw": output}, human_message=human, raw_output=output)


def causal_node_list(layer: str | None = None) -> ToolResult:
    """列出因果节点。"""
    args = ["causal", "node", "list"]
    if layer:
        args += ["--layer", layer]
    success, output = run_inv(*args)
    if success:
        human = "已列出因果节点。\n所以你该做什么：确认 L3_holding 层包含所有持仓标的的节点。"
    else:
        human = f"无法列出节点。\n{output[-300:]}"
    return ToolResult(success=success, data={"layer": layer, "raw": output}, human_message=human, raw_output=output)


def causal_edge_add(
    from_node: str,
    to_node: str,
    direction: str,
    d1: int, d2: int, d3: int, d4: int, d5: int,
) -> ToolResult:
    """添加因果边（5 维评分）。direction: POSITIVE|NEGATIVE|BIDIRECTIONAL。"""
    args = [
        "causal", "edge", "add",
        "--from", from_node, "--to", to_node,
        "--direction", direction,
        "--d1", str(d1), "--d2", str(d2), "--d3", str(d3), "--d4", str(d4), "--d5", str(d5),
    ]
    success, output = run_inv(*args)
    if success:
        human = (
            f"因果边已添加（{from_node} → {to_node}，{direction}）。\n"
            "所以你该做什么：运行 causal_review_list() 审批此边，批准后才会参与评估。"
        )
    else:
        human = f"边添加失败。\n{output[-300:]}"
    return ToolResult(success=success, data={"from": from_node, "to": to_node, "raw": output},
                      human_message=human, raw_output=output)


def causal_edge_list(layer: str | None = None) -> ToolResult:
    """列出因果边。"""
    args = ["causal", "edge", "list"]
    if layer:
        args += ["--layer", layer]
    success, output = run_inv(*args)
    if success:
        human = "已列出因果边。\n所以你该做什么：检查是否有待审批的边（pending 状态）。"
    else:
        human = f"无法列出边。\n{output[-300:]}"
    return ToolResult(success=success, data={"layer": layer, "raw": output}, human_message=human, raw_output=output)


def causal_review_list() -> ToolResult:
    """查看待审批边。"""
    success, output = run_inv("causal", "review", "list")
    pending_count = output.count("pending") if success else 0
    if success:
        if pending_count > 0:
            human = (
                f"有 {pending_count} 条边待审批。\n"
                "所以你该做什么：逐条运行 causal_review_approve() 或 causal_review_reject() 处理。"
            )
        else:
            human = "没有待审批的因果边，图谱已是最新状态。"
    else:
        human = f"无法获取待审批列表。\n{output[-300:]}"
    return ToolResult(success=success, data={"pending_count": pending_count, "raw": output},
                      human_message=human, raw_output=output)


def causal_review_approve(edge_id: int) -> ToolResult:
    """批准待审批边。"""
    success, output = run_inv("causal", "review", "approve", str(edge_id))
    if success:
        human = f"边 #{edge_id} 已批准，将参与后续因果评估。\n所以你该做什么：继续处理其他待审批边。"
    else:
        human = f"批准失败（#{edge_id}）。\n{output[-300:]}"
    return ToolResult(success=success, data={"edge_id": edge_id, "raw": output},
                      human_message=human, raw_output=output)


def causal_review_reject(edge_id: int, reason: str = "") -> ToolResult:
    """拒绝待审批边。"""
    args = ["causal", "review", "reject", str(edge_id)]
    if reason:
        args += ["--reason", reason]
    success, output = run_inv(*args)
    if success:
        human = f"边 #{edge_id} 已拒绝。\n所以你该做什么：继续处理其他待审批边。"
    else:
        human = f"拒绝失败（#{edge_id}）。\n{output[-300:]}"
    return ToolResult(success=success, data={"edge_id": edge_id, "raw": output},
                      human_message=human, raw_output=output)


def causal_lifecycle_update() -> ToolResult:
    """应用衰减 + 状态迁移（active→dormant→archived）。"""
    success, output = run_inv("causal", "lifecycle", "update")
    if success:
        human = (
            "因果节点生命周期已更新（衰减 + 状态迁移）。\n"
            "所以你该做什么：运行 causal_node_list() 查看是否有节点变为 dormant，考虑是否需要重新激活。"
        )
    else:
        human = f"生命周期更新失败。\n{output[-300:]}"
    return ToolResult(success=success, data={"raw": output}, human_message=human, raw_output=output)
