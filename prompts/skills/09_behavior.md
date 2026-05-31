---
skill_id: behavior
name: 行为约束与决策日志
phase: 7
priority: P2
status: skeleton
---

# Skill ⑨ — 行为约束与决策日志

## 触发条件

- 用户准备做交易决策前
- 复盘时发现行为偏差
- 用户主动请求行为检查

## 调用工具链（Phase 7 已实现）

```
# 占位 — Phase 7 实现
1. behavior_flags_check()    → 检测处置效应/锚定/过度交易
2. decision_journal_save()   → 记录决策日志（decision_journal 表）
3. trade_frequency_calc()    → 计算近期交易频率
4. holding_period_calc()     → 计算平均持仓周期
5. bias_detect()             → 识别行为偏差模式
6. human_translate()         → 翻译为人话行为报告
```

## 输入 Schema

```yaml
inputs:
  check_type:
    type: string
    enum: [pre_trade, post_trade, periodic]
    description: 检查类型（交易前/交易后/定期）
    default: pre_trade
  trade_context:
    type: object
    description: 当前交易上下文（交易前检查时必填）
    properties:
      code: string
      side: string
      reason: string
    required: false
  lookback_days:
    type: integer
    description: 行为分析回溯天数
    default: 90
```

## 输出 Schema

```yaml
outputs:
  biases_detected:
    type: array
    items:
      bias_type: string      # 人话翻译后的偏差名称
      evidence: string       # 具体表现
      severity: string       # high / medium / low
      action_required: string
  trade_frequency:
    type: object
    properties:
      trades_per_month: number
      assessment: string     # 正常/偏高/过度
  avg_holding_days:
    type: number
  journal_id:
    type: integer
    description: 记录的决策日志 ID
  human_message: string
```

## 用户话术模板

```
## 行为检查 — [日期]

### 检测到的行为偏差
[如有偏差，每条附行动指引；无偏差则给出正面确认]

### 交易频率分析
过去 [N] 天：[X] 笔交易，平均每月 [X] 笔
评价：[正常/偏高/过度]

### 平均持仓周期
[X] 天（[短线/中线/长线]风格）

所以你该做什么：[具体操作]
```

## 不做什么（Phase 7 边界）

- 不做强制拦截（只提示，不阻止交易）
- 不做心理咨询（只识别行为模式，不做心理分析）
