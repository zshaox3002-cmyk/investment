---
skill_id: position
name: 仓位管理与再平衡巡检
phase: 3
priority: P0
status: skeleton
---

# Skill ② — 仓位管理与再平衡巡检

## 触发条件

- 每日盘后自动触发（`snapshot pull` 完成后）
- 用户询问持仓状态、告警、风控
- 单股回撤 ≥15% 时强制触发
- 账户回撤 ≥20% 时强制触发

## 调用工具链（Phase 3 已实现）

```python
from investment.agent_tools.position_monitor import run_position_monitor
from investment.agent_tools.translator import translate_alerts, translate_rule_path

# 运行完整仓位巡检
report = run_position_monitor(as_of="2026-05-30")  # 不传则用今日

# report.holdings        — 持仓列表（含盈亏、仓位占比）
# report.tranches        — A/B/C 档偏离分析
# report.alerts          — 人话告警列表（含"所以你该做什么"）
# report.rule_breaches   — 规则违反列表（含行动指引）
# report.rebalance_needed — 是否需要再平衡
# report.human_message   — 完整人话报告
```

## 输入 Schema

```yaml
inputs:
  date:
    type: string
    format: YYYY-MM-DD
    description: 查询日期，默认今日
    required: false
  force_refresh:
    type: boolean
    description: 是否强制重新拉取行情
    default: false
```

## 输出 Schema

```yaml
outputs:
  holdings_summary:
    type: array
    items:
      code: string
      name: string
      market_value: number
      pnl_pct: number
      deviation_from_target: number
  alerts:
    type: array
    items:
      rule_name: string       # 人话翻译后的规则名
      severity: string        # high / medium / low
      action_required: string # 所以你该做什么
  rebalance_needed:
    type: boolean
  human_message:
    type: string
```

## 用户话术模板

```
## 仓位巡检 — [日期]

### 核心结论
[1-3 条最重要的告警或状态，每条附行动指引]

### 持仓状态
| 股票 | 市值 | 今日涨跌 | 偏离目标 |
|------|------|---------|---------|
| [名称] | ¥[X] | [±X%] | [±X%] |

### 风控检查
[命中的规则 + 人话解释 + 行动指引]

### 下一步行动
- [ ] [操作 1]
- [ ] [操作 2]
```

## 关键规则（硬约束）

### D 档排除规则

- 计算任何占比（A/B/C 档百分比、单股仓位占比、行业集中度）时，**分母 = A + B + C**，必须排除 D 档（美团 RSU）。
- D 档在总资产中显示，但不参与再平衡基准。`config/capital.yaml` 中 `meituan_rsu_excluded: true`。
- 总资产数字（含 D 档）仅供参考，不做决策依据。
- 两阶段框架：第一阶段（至 2027-11-01）排除 RSU；第二阶段（2027-11 后）重新纳入。

### 占比计算口径

```
A% = A市值 / (A+B+C)  而非  A市值 / (A+B+C+D)
B% = B市值 / (A+B+C)
C% = C市值 / (A+B+C)
单股市值占比 = 该股市值 / C总市值
行业集中度 = 该行业市值合计 / C总市值
```

## 不做什么（Phase 3 边界）

- 不实现投资日历催办（→ Skill ⑤，Phase 7）
- 不做相关性/风险量化（→ Skill ⑥，Phase 4）
- 不新增数据表（复用现有 alerts/rule_breaches/holdings）
