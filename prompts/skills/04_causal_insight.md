---
skill_id: causal_insight
name: 外部信息与因果归因
phase: 6
priority: P2
status: skeleton
---

# Skill ④ — 外部信息与因果归因

## 触发条件

- 持仓出现异动（相对基准超额波动，阈值由 rules.yaml 定义）
- 用户询问市场事件对持仓的影响
- 每日因果推理流水线（`causal daily`）

## 调用工具链（Phase 6 已实现）

```python
from investment.agent_tools.causal_facade import (
    run_causal_insight,
    update_validation_status,
    backfill_credibility_tiers,
)

# 零操作获取今日因果洞察（用户不需要接触节点/边）
report = run_causal_insight(as_of="2026-05-28", holding_code=None)

# report.actionable      — A/B 级信号（建议行动/持续关注）
# report.monitoring      — C 级信号（仅记录）
# report.anomalies_detected — 异动持仓代码列表
# report.human_message   — 完整人话报告（含"所以你该做什么"）

# 当后续证据出现时更新置信度（迭代修正）
update_validation_status(assessment_id=14, new_status="confirmed", reason="事件已发生")
update_validation_status(assessment_id=14, new_status="refuted", reason="事件未发生")

# CLI 入口
# inv causal insight --date 2026-05-28
# inv causal validate 14 --status confirmed --reason "理由"
# inv causal daily  (原有命令，触发完整流水线)
```

## 输入 Schema

```yaml
inputs:
  code:
    type: string
    description: 股票代码（不填则分析全部持仓）
    required: false
  event_description:
    type: string
    description: 用户描述的事件（可选，用于 discover 模式）
    required: false
  date:
    type: string
    format: YYYY-MM-DD
    description: 分析日期，默认今日
    required: false
```

## 输出 Schema

```yaml
outputs:
  signals:
    type: array
    items:
      event: string
      scope_layer: string      # L1_macro / L2_sector / L3_holding（人话翻译）
      credibility: string      # A / B / C / D
      impact_direction: string # 利好 / 利空 / 中性
      affected_holdings: array
      confidence: number
      action_required: string
  human_message:
    type: string
```

## 用户话术模板

```
## 因果归因 — [日期]

### 核心结论
[1-3 条最重要的因果信号，每条附行动指引]

### 信号详情
| 事件 | 影响层级 | 可信度 | 影响方向 | 受影响持仓 |
|------|---------|--------|---------|---------|
| [事件] | [宏观/行业/持仓层] | [高/中/低] | [利好/利空] | [股票名] |

所以你该做什么：[具体操作]
```

## 不做什么（Phase 6 边界）

- 不让用户接触节点/边的构图和审批（全自动）
- 不修改因果引擎核心算法
- 不做收益归因（→ Skill ⑧）
