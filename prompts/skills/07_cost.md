---
skill_id: cost
name: 交易成本计算
phase: 7
priority: P2
status: implemented
---

# Skill ⑦ — 交易成本计算

## 触发条件

- 用户计划买入或卖出前
- 用户询问交易费用
- 计算实际收益时

## 调用工具链（Phase 7 已实现）

```python
from investment.agent_tools.cost_calculator import (
    detect_market, calc_cost, save_cost_log,
)

# 1. 检测市场（自动识别沪/深/北/港股）
market = detect_market("600519")  # → "A_SH"
market = detect_market("00700")   # → "HK"

# 2. 计算全量交易成本（从 cost_model 表加载费率，fallback 到内置默认值）
breakdown = calc_cost(code="600519", shares=1000, price=25.0, side="BUY")
# breakdown.gross_amount  — 交易金额
# breakdown.commission    — 券商佣金
# breakdown.stamp_duty    — 印花税（仅卖出）
# breakdown.transfer_fee  — 过户费（沪市）
# breakdown.total_cost    — 总费用
# breakdown.net_amount    — 实际支出/到手
# breakdown.cost_rate     — 综合费率
# breakdown.human_message — 完整人话费用明细

# 3. 可选：将成本计算持久化到 trade_cost_log
log_id = save_cost_log(breakdown, trade_id=42)

# CLI: inv cost calc 600519 --shares 1000 --price 25.0 --side BUY
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
