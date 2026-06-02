"""Wrappers for: inv trade decision/list/log/apply/stop + inv exec monitor."""
from __future__ import annotations

from .base import ToolResult
from ._runner import run_inv

_COOLDOWN = {"NEW": 7, "ADD": 5, "REDUCE": 3, "EXIT": 3, "REBALANCE": 3, "EMERGENCY": 0}


def trade_decision(
    code: str,
    decision_type: str,
    notes: str = "",
    ic_memo: bool = False,
) -> ToolResult:
    """创建交易决策记录。decision_type: NEW|ADD|REDUCE|EXIT|REBALANCE|EMERGENCY。"""
    args = ["trade", "decision", code, "--type", decision_type.upper()]
    if notes:
        args += ["--notes", notes]
    if ic_memo:
        args.append("--ic-memo")
    success, output = run_inv(*args)
    cooldown = _COOLDOWN.get(decision_type.upper(), 3)
    if success:
        human = (
            f"决策已创建（{code} / {decision_type}）。\n"
            f"所以你该做什么：等待 {cooldown} 天冷静期后，再执行 trade_log() 记录成交。"
        )
    else:
        human = (
            f"决策创建失败（{code}）。\n"
            f"所以你该做什么：检查股票代码是否在 instruments 表中，或查看错误：\n{output[-400:]}"
        )
    return ToolResult(success=success, data={"code": code, "type": decision_type, "raw": output},
                      human_message=human, raw_output=output)


def trade_list(status: str = "active") -> ToolResult:
    """列出交易决策。status: active|all|executed|cancelled。"""
    success, output = run_inv("trade", "list", "--status", status)
    if success:
        human = (
            f"已列出 {status} 状态的交易决策。\n"
            "所以你该做什么：检查是否有冷静期已过、可以执行的决策。"
        )
    else:
        human = f"无法列出决策。\n{output[-300:]}"
    return ToolResult(success=success, data={"status": status, "raw": output},
                      human_message=human, raw_output=output)


def trade_log(
    code: str,
    shares: float,
    price: float,
    side: str,
    decision_id: str | None = None,
    fees: float = 0.0,
    notes: str = "",
    date: str | None = None,
) -> ToolResult:
    """记录成交。side: BUY|SELL。"""
    args = ["trade", "log", code, "-s", str(shares), "-p", str(price), "--side", side.upper()]
    if decision_id:
        args += ["-d", decision_id]
    if fees:
        args += ["--fees", str(fees)]
    if notes:
        args += ["--notes", notes]
    if date:
        args += ["--date", date]
    success, output = run_inv(*args)
    if success:
        trade_id = ""
        for line in output.splitlines():
            if "trade_" in line.lower() or "TRADE_" in line:
                trade_id = line.strip()
                break
        human = (
            f"成交已记录（{code} {side} {shares}股 @{price}）。\n"
            f"所以你该做什么：执行 trade_apply(trade_id) 更新持仓，trade_id 见上方输出。"
        )
        return ToolResult(success=True, data={"code": code, "side": side, "trade_id": trade_id, "raw": output},
                          human_message=human, raw_output=output)
    else:
        human = (
            f"成交记录失败（{code}）。\n"
            f"所以你该做什么：确认决策编号正确，或查看错误：\n{output[-400:]}"
        )
        return ToolResult(success=False, data={"raw": output}, human_message=human, raw_output=output)


def trade_apply(trade_id: str) -> ToolResult:
    """成交后反向更新持仓（shares + 加权均价）。"""
    success, output = run_inv("trade", "apply", trade_id)
    if success:
        human = (
            f"持仓已更新（{trade_id}）。卖出资金已自动入账到活期存款。\n"
            "所以你该做什么：运行 snapshot_pull() 刷新行情，确认持仓市值正确。"
        )
    else:
        human = (
            f"持仓更新失败（{trade_id}）。\n"
            f"所以你该做什么：确认 trade_id 存在且未被重复 apply。\n{output[-400:]}"
        )
    return ToolResult(success=success, data={"trade_id": trade_id, "raw": output},
                      human_message=human, raw_output=output)


def trade_stop(
    code: str,
    decision_id: str,
    stop_type: str,
    trigger_kind: str,
    trigger_value: float,
    action: str,
    shares: int | None = None,
    priority: int = 100,
) -> ToolResult:
    """设置止损止盈规则。stop_type: STOP_LOSS|TAKE_PROFIT|GRID_SELL|GRID_BUY|HARD_DD。"""
    args = [
        "trade", "stop", code,
        "-d", decision_id,
        "--type", stop_type,
        "--trigger-kind", trigger_kind,
        "-v", str(trigger_value),
        "-a", action,
        "--priority", str(priority),
    ]
    if shares is not None:
        args += ["--shares", str(shares)]
    success, output = run_inv(*args)
    if success:
        human = (
            f"止损/止盈规则已设置（{code} {stop_type} @ {trigger_value}）。\n"
            "所以你该做什么：每个交易日盘后运行 exec_monitor() 检查是否触发。"
        )
    else:
        human = (
            f"规则设置失败（{code}）。\n"
            f"所以你该做什么：确认决策编号和参数格式正确。\n{output[-400:]}"
        )
    return ToolResult(success=success, data={"code": code, "type": stop_type, "raw": output},
                      human_message=human, raw_output=output)


def exec_monitor() -> ToolResult:
    """检查已 arm 的止损止盈规则是否触发。"""
    success, output = run_inv("exec", "monitor")
    triggered = "触发" in output or "TRIGGERED:" in output.upper()
    if success:
        if triggered:
            human = (
                "有止损/止盈规则已触发！\n"
                "所以你该做什么：立即查看触发详情，按规则执行对应的买卖操作。"
            )
        else:
            human = (
                "所有止损/止盈规则检查完毕，无触发。\n"
                "所以你该做什么：继续持仓，明日盘后再次检查。"
            )
    else:
        human = f"执行监控失败。\n所以你该做什么：查看错误信息：\n{output[-300:]}"
    return ToolResult(
        success=success,
        data={"triggered": triggered, "raw": output},
        human_message=human,
        raw_output=output,
    )
