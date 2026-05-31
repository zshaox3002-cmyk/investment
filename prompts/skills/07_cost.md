---
skill_id: cost
name: 交易成本计算
phase: 7
priority: P2
status: skeleton
---

# Skill ⑦ — 交易成本计算

## 触发条件

- 用户计划买入或卖出前
- 用户询问交易费用
- 计算实际收益时

## 调用工具链（Phase 7 已实现）

```
# 占位 — Phase 7 实现
1. market_detect()           → 判断市场（A 股沪/深/北 / 港股 / ETF）
2. cost_model_load()         → 读取 cost_model 表（费率配置）
3. cost_calc()               → 计算印花税/佣金/过户费/港股摩擦
4. cost_log_save()           → 写入 trade_cost_log 表
5. human_translate()         → 人话费用明细
```

## 输入 Schema

```yaml
inputs:
  code:
    type: string
    description: 股票代码
    required: true
  shares:
    type: number
    description: 交易股数
    required: true
  price:
    type: number
    description: 交易价格
    required: true
  side:
    type: string
    enum: [BUY, SELL]
    required: true
  broker_commission_rate:
    type: number
    description: 券商佣金率（默认万 2.5）
    default: 0.00025
```

## 输出 Schema

```yaml
outputs:
  gross_amount:
    type: number
    description: 交易金额（股数 × 价格）
  stamp_duty:
    type: number
    description: 印花税（仅卖出，A 股 0.1%）
  commission:
    type: number
    description: 券商佣金
  transfer_fee:
    type: number
    description: 过户费（沪市）
  total_cost:
    type: number
    description: 总费用
  net_amount:
    type: number
    description: 实际到手金额（买入为支出，卖出为收入）
  cost_rate:
    type: number
    description: 综合费率（%）
  human_message: string
```

## 用户话术模板

```
## 交易成本估算

### 交易概要
[股票名称] [买入/卖出] [N] 股 @ ¥[价格]

### 费用明细
| 费用项 | 金额 | 说明 |
|--------|------|------|
| 印花税 | ¥[X] | 卖出时收取，0.1% |
| 券商佣金 | ¥[X] | 万 2.5（最低 5 元） |
| 过户费 | ¥[X] | 沪市收取 |
| **合计** | **¥[X]** | 综合费率 [X]% |

### 实际到手
[买入：实际支出 ¥X / 卖出：实际到手 ¥X]

所以你该做什么：[如"这笔交易的摩擦成本为 X%，需要股价上涨 X% 才能回本"]
```

## 不做什么（Phase 7 边界）

- 不做税务规划（后续增强项）
- 不做跨市场套利计算
