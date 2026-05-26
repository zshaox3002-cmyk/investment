#!/usr/bin/env python3
"""
alert_monitor.py — 告警处理与推送

用法:
    python scripts/alert_monitor.py <alert_type> <code>
        例: python scripts/alert_monitor.py single_stock_position 600219

    也可由 daily_snapshot.py 自动调用。

职责:
  1. 接收告警类型和标的代码
  2. 写入结构化告警文件到 alerts/
  3. 可选：通过 Bark / webhook 推送手机通知
       设置环境变量 BARK_URL=https://api.day.app/<key> 启用 Bark 推送
       设置环境变量 WEBHOOK_URL=<url> 启用通用 webhook 推送

告警类型（来自 daily_snapshot.py）:
  single_stock_drawdown_l1/l2/l3  单股回撤
  single_stock_position            单股仓位超标
  account_drawdown_l1/l2/l3       账户回撤
  theme_concentration              主题集中度
  etf_drawdown / etf_drift         ETF 告警
"""

import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
ALERTS_DIR = ROOT_DIR / "alerts"
ALERTS_DIR.mkdir(parents=True, exist_ok=True)

# 告警严重级别映射
_SEVERITY_MAP = {
    "single_stock_drawdown_l1": ("info", "单股回撤 L1 关注"),
    "single_stock_drawdown_l2": ("warning", "单股回撤 L2 审视"),
    "single_stock_drawdown_l3": ("critical", "单股回撤 L3 强制"),
    "single_stock_position": ("critical", "单股仓位超标"),
    "account_drawdown_l1": ("info", "账户回撤 L1 预警"),
    "account_drawdown_l2": ("warning", "账户回撤 L2 控制"),
    "account_drawdown_l3": ("critical", "账户回撤 L3 硬刹车"),
    "theme_concentration": ("warning", "主题集中度超标"),
    "etf_drawdown": ("warning", "ETF 回撤关注"),
    "etf_drift": ("info", "ETF 偏离提醒"),
    "thesis_stale_analysis": ("warning", "Thesis 更新待重新审视分析"),
}

_SEVERITY_ICONS = {"critical": "🔴", "warning": "🟡", "info": "🟢"}


# ── Alert file ────────────────────────────────────────────────────────

def write_alert_file(alert_type: str, code: str, message: str = "", context: str = "",
                     force: bool = False) -> Path | None:
    severity, label = _SEVERITY_MAP.get(alert_type, ("info", alert_type))
    icon = _SEVERITY_ICONS[severity]
    date_str = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    code_part = f"_{code}" if code else ""
    filename = f"{date_str}{code_part}_{alert_type}.md"
    out_path = ALERTS_DIR / filename

    if out_path.exists() and not force:
        print(f"  ⏭ 告警文件已存在，跳过: {out_path}")
        return None

    lines = [
        f"# {icon} 告警 · {label}",
        f"",
        f"| 字段 | 内容 |",
        f"|------|------|",
        f"| 告警类型 | `{alert_type}` |",
        f"| 严重级别 | {severity.upper()} |",
        f"| 标的代码 | {code or '—'} |",
        f"| 触发时间 | {time_str} |",
        f"",
        f"## 告警详情",
        f"",
        message or "（无详情）",
        f"",
    ]

    if context:
        lines += [f"## Thesis 上下文", f"", context, f""]

    lines += [
        f"## 处理记录",
        f"",
        f"- [ ] 已阅读",
        f"- [ ] 已分析原因",
        f"- [ ] 已更新 thesis（如需）",
        f"- [ ] 已创建/更新 decision 文件（如需）",
        f"",
        f"---",
        f"*自动生成于 {time_str}*",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ── Push notification ─────────────────────────────────────────────────

def _push_bark(title: str, body: str, bark_url: str):
    """Bark 推送（iOS）。"""
    import urllib.parse
    title_enc = urllib.parse.quote(title)
    body_enc = urllib.parse.quote(body)
    url = f"{bark_url.rstrip('/')}/{title_enc}/{body_enc}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"  [Bark 推送失败] {e}")
        return False


def _push_webhook(title: str, body: str, webhook_url: str):
    """通用 webhook 推送（POST JSON）。"""
    payload = json.dumps({"title": title, "body": body}).encode()
    req = urllib.request.Request(
        webhook_url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status < 300
    except Exception as e:
        print(f"  [Webhook 推送失败] {e}")
        return False


def push_notification(alert_type: str, code: str, message: str):
    severity, label = _SEVERITY_MAP.get(alert_type, ("info", alert_type))
    if severity == "info":
        return  # info 级别不推送

    icon = _SEVERITY_ICONS[severity]
    title = f"{icon} {label}"
    body = f"{code}: {message[:100]}" if code else message[:100]

    bark_url = os.environ.get("BARK_URL", "")
    webhook_url = os.environ.get("WEBHOOK_URL", "")

    if bark_url:
        ok = _push_bark(title, body, bark_url)
        print(f"  Bark 推送: {'✅' if ok else '❌'}")
    elif webhook_url:
        ok = _push_webhook(title, body, webhook_url)
        print(f"  Webhook 推送: {'✅' if ok else '❌'}")
    else:
        print(f"  [推送] {title} — {body}")
        print("  提示: 设置 BARK_URL 或 WEBHOOK_URL 环境变量启用手机推送")


# ── Main ──────────────────────────────────────────────────────────────

def main():
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv

    if len(args) < 1:
        print("用法: python scripts/alert_monitor.py <alert_type> [code] [message] [--force]")
        sys.exit(1)

    alert_type = args[0]
    code = args[1] if len(args) > 1 else ""
    message = args[2] if len(args) > 2 else ""

    out_path = write_alert_file(alert_type, code, message, force=force)
    if out_path:
        print(f"✅ 告警文件: {out_path}")

    push_notification(alert_type, code, message)


if __name__ == "__main__":
    main()
