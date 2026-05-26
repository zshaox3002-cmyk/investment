#!/usr/bin/env python3
"""
hermes_enrich.py — 用 Hermes Agent 自动获取本周市场信息，写入周报

用法:
    python scripts/hermes_enrich.py

职责:
  1. 搜索持仓公司本周重要公告/财报/事件，结合持仓上下文给出影响分析和行动建议
  2. 搜索本周宏观数据发布日历，说明对持仓组合的具体影响
  3. 将结构化结果写入当前周报的"本周关注事项"区域

依赖: 本地已安装 Hermes Agent（hermes chat -Q）
"""

import re
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT_DIR, load_holdings

WEEKLY_DIR = ROOT_DIR / "reviews" / "weekly"
THESES_DIR = ROOT_DIR / "theses"
TRADES_DIR = ROOT_DIR / "trades"
HERMES_TIMEOUT = 180  # 秒


def week_range(today: date) -> tuple[date, date]:
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def iso_week_label(today: date) -> str:
    return today.strftime("%Y-W%W")


def check_hermes() -> bool:
    return shutil.which("hermes") is not None


def run_hermes(prompt: str) -> str:
    """调用 hermes chat -Q -q，返回输出文本。超时或失败返回空字符串。"""
    try:
        result = subprocess.run(
            ["hermes", "chat", "-Q", "-q", prompt, "-t", "web"],
            capture_output=True,
            text=True,
            timeout=HERMES_TIMEOUT,
        )
        output = result.stdout.strip()
        lines = output.splitlines()
        if lines and lines[0].startswith("session_id:"):
            lines = lines[1:]
        return "\n".join(lines).strip()
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""


def load_thesis_context(code: str) -> dict:
    """读取 thesis YAML frontmatter，返回 score/action/alert_context。"""
    for pattern in [f"{code}_thesis.md", f"0{code}_thesis.md"]:
        path = THESES_DIR / pattern
        if path.exists():
            text = path.read_text(encoding="utf-8")
            m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
            if m:
                try:
                    return yaml.safe_load(m.group(1)) or {}
                except Exception:
                    pass
    return {}


def active_decisions() -> dict[str, str]:
    """扫描 trades/decision_*.md，返回 {股票代码: 决策文件名} 映射。"""
    result = {}
    if not TRADES_DIR.exists():
        return result
    title_re = re.compile(r"^#\s+(.+)$", re.MULTILINE)
    for f in TRADES_DIR.glob("decision_*.md"):
        text = f.read_text(encoding="utf-8")
        title_m = title_re.search(text)
        title = title_m.group(1) if title_m else f.stem
        # 从标题或内容中提取股票代码
        code_m = re.search(r"\b(\d{5,6})\b", text[:500])
        if code_m:
            result[code_m.group(1)] = f.stem
    return result


def build_holdings_context(holdings: list[dict]) -> str:
    """组装每只持仓的上下文信息，注入 prompt。"""
    decisions = active_decisions()
    lines = []
    for h in holdings:
        code = h["code"]
        market = h.get("market", "A")
        name = h["name"]
        cost = float(h["cost_price"])
        price = float(h["current_price"])
        pnl_pct = (price - cost) / cost * 100
        suffix = ".HK" if market == "HK" else ".SH" if code.startswith("6") else ".SZ"

        thesis = load_thesis_context(code)
        score = thesis.get("score", "—")
        action = thesis.get("action", "—")
        alert_ctx = thesis.get("alert_context", "")

        decision_note = f"已有决策文件：{decisions[code]}" if code in decisions else "无进行中决策"

        line = (
            f"- {name}（{code}{suffix}）"
            f"  成本¥{cost:.2f} 现价¥{price:.2f} 盈亏{pnl_pct:+.1f}%"
            f"  thesis评分{score}/5 当前建议动作：{action}"
            f"  {decision_note}"
        )
        if alert_ctx:
            line += f"\n  告警背景：{alert_ctx}"
        lines.append(line)
    return "\n".join(lines)


def build_holdings_list(holdings: list[dict]) -> str:
    parts = []
    for h in holdings:
        market = h.get("market", "A")
        code = h["code"]
        name = h["name"]
        suffix = ".HK" if market == "HK" else ".SH" if code.startswith("6") else ".SZ"
        parts.append(f"{name}({code}{suffix})")
    return "、".join(parts)


def prompt_holdings_events(
    today: date, week_start: date, week_end: date, holdings_context: str
) -> str:
    return f"""今天是{today}。我是一位个人投资者，以下是我的持仓上下文（成本价、盈亏、thesis评分、已有决策、告警背景）：

{holdings_context}

请搜索以上每只持仓公司本周（{week_start}至{week_end}）的重要事件。然后结合我的持仓上下文，分析每个事件对我的具体影响，并给出行动建议。

输出格式：先用表格列出事件，然后对每只有重要事件（🔴高或🟡中）的股票单独给出影响分析和行动建议。

不要添加额外说明文字。

## 持仓公司本周事件

| 公司 | 代码 | 事件类型 | 日期 | 摘要 | 重要度 |
|------|------|---------|------|------|--------|

（每只股票一行，无重要事件则在摘要填"本周无重要事件"，重要度填🟢低）

## 重要事件影响分析

（只对重要度🔴高或🟡中的事件做分析，🟢低的无事件股票跳过）

### [公司名]（[代码]）· thesis [评分]/5 · 盈亏 [X]%

**事件：** [一句话描述]

**影响分析：** [🔴利空 / 🟢利好 / 🟡中性]
[2-3句话。结合我的持仓成本和thesis支柱，说明该事件对投资逻辑、股价的影响。用具体数字说话，不要泛泛而谈。]

**你应该做什么：** [选一项]
- 📖 深入调研：事件重要但方向不明，需先收集更多信息再判断
- 📊 跑财报解读：有财报或业绩数据，需运行 earnings-analysis
- ⚠️ 更新thesis：事件可能改变支柱评分，需重新评估
- ✅ 按现有决策执行：已有decision文件覆盖
- 🔍 维持现状观察：影响中性或偏正面
- 🚨 加速减仓：利空叠加已有告警，需评估是否提前执行

[跟进建议：关注哪些后续信号，什么条件下应改变判断。1-2句话。]"""


def prompt_macro_calendar(
    today: date, week_start: date, week_end: date, holdings_list: str
) -> str:
    return f"""今天是{today}。我持有这些A股/港股：{holdings_list}

请搜索本周（{week_start}至{week_end}）中国相关的宏观数据发布计划，并结合我的持仓说明影响。

格式：

## 本周宏观数据发布

| 日期 | 数据名称 | 上期值 | 市场预期 | 对A股影响 |
|------|---------|--------|---------|---------|

（对A股影响填：🔴直接影响 / 🟡间接影响 / 🟢参考）

## 宏观数据对我的持仓影响

（对每个🔴直接影响的数据，用"如果…则…"句式写一段：如果数据好于预期，对我的哪些持仓有利，我应该做什么；如果数据差于预期，对我的哪些持仓不利，我应该做什么。2-3句话。）

## 宏观小结

（1-2句话整体判断）"""


def enrich_report(
    report_path: Path, holdings_result: str, macro_result: str, today: date
) -> None:
    content = report_path.read_text(encoding="utf-8")

    section = f"""## 本周关注事项

> 由 Hermes Agent 自动获取 · {today}

{holdings_result}

---

{macro_result}"""

    pattern = re.compile(
        r"## (?:本周|下周)关注事项\n.*?(?=\n---\n\*生成时间|\Z)",
        re.DOTALL,
    )
    if pattern.search(content):
        new_content = pattern.sub(section, content)
    else:
        new_content = content.rstrip() + "\n\n" + section + "\n"

    report_path.write_text(new_content, encoding="utf-8")


def main():
    today = date.today()
    week_start, week_end = week_range(today)
    week_label = iso_week_label(today)

    if not check_hermes():
        print("❌ 未找到 hermes 命令，请先安装 Hermes Agent")
        print("   安装文档：https://hermes-agent.nousresearch.com/docs/")
        sys.exit(1)

    report_path = WEEKLY_DIR / f"{week_label}.md"
    if not report_path.exists():
        print(f"❌ 周报文件不存在：{report_path}")
        print("   请先运行：python scripts/weekly_brief.py")
        sys.exit(1)

    holdings = load_holdings()
    holdings_list = build_holdings_list(holdings)
    holdings_context = build_holdings_context(holdings)

    print(f"[hermes_enrich] 本周：{week_start} ~ {week_end}")
    print(f"[hermes_enrich] 持仓：{holdings_list}")

    print("\n[1/2] 搜索持仓公司本周事件 + 影响分析（约 90-120 秒）...")
    holdings_result = run_hermes(
        prompt_holdings_events(today, week_start, week_end, holdings_context)
    )
    if not holdings_result:
        holdings_result = "## 持仓公司本周事件\n\n⚠️ 信息获取超时或失败，请手动填写"

    print("[2/2] 搜索宏观数据发布日历 + 持仓影响（约 60-90 秒）...")
    macro_result = run_hermes(
        prompt_macro_calendar(today, week_start, week_end, holdings_list)
    )
    if not macro_result:
        macro_result = "## 本周宏观数据发布\n\n⚠️ 信息获取超时或失败，请手动填写"

    enrich_report(report_path, holdings_result, macro_result, today)
    print(f"\n✅ 信息已写入 {report_path}")


if __name__ == "__main__":
    main()
