---
skill_id: onboarding
name: 目标与资产录入
phase: 2
priority: P0
status: skeleton
---

# Skill ① — 目标与资产录入 (Onboarding)

## 触发条件

- 用户首次使用系统（`user_profile` 表为空）
- 用户主动说"重新设置目标"、"更新风险偏好"
- 路由层命中 `onboarding` 关键词

## 调用工具链（Phase 2 已实现）

```python
from investment.agent_tools.onboarding import (
    ProfileInput, run_onboarding,
    get_latest_profile, get_active_goals,
    record_assets,
)

# 1. 检查是否已有 profile
profile = get_latest_profile()

# 2. 多轮对话采集后构建输入
inp = ProfileInput(
    investable_capital=...,
    risk_tolerance=...,      # conservative | moderate | aggressive
    horizon_years=...,
    target_annual_return=...,
    max_drawdown_tolerance=...,
    target_amount=...,       # 可选
    deadline=...,            # 可选 YYYY-MM-DD
)

# 3. 执行完整 onboarding 流程
result = run_onboarding(inp, assets=[...])
# result.success, result.profile_id, result.allocation, result.human_message

# 4. CLI 入口
# inv profile set --capital N --risk moderate --horizon N --target-return N
# inv profile show
# inv profile reset --confirm
```

## 输入 Schema

```yaml
inputs:
  investable_capital:
    type: number
    unit: CNY
    description: 可投资总金额
    required: true
  risk_tolerance:
    type: string
    enum: [conservative, moderate, aggressive]
    description: 风险承受能力
    required: true
  horizon_years:
    type: integer
    min: 1
    max: 30
    description: 投资期限（年）
    required: true
  target_annual_return:
    type: number
    unit: percent
    description: 目标年化收益率
    required: true
  max_drawdown_tolerance:
    type: number
    unit: percent
    description: 可接受的最大回撤
    required: false
    default: 20
```

## 输出 Schema

```yaml
outputs:
  user_profile_id:
    type: integer
    description: 写入的 user_profile 记录 ID
  abc_allocation:
    type: object
    properties:
      a_ratio: { type: number, description: A 档（生活保障金）比例 }
      b_ratio: { type: number, description: B 档（核心 ETF）比例 }
      c_ratio: { type: number, description: C 档（主动选股）比例 }
  gap_analysis:
    type: string
    description: 实际资产 vs 目标的差距人话描述
  human_message:
    type: string
    description: 含"所以你该做什么"的完整输出
```

## 用户话术模板

```
## 目标与资产录入 — [日期]

### 你的投资画像
- 可投资金额：¥[X] 万
- 风险承受：[保守/稳健/积极]
- 投资期限：[N] 年
- 目标年化：[X]%

### 专属配置方案
| 档位 | 用途 | 比例 | 金额 |
|------|------|------|------|
| A 档 | 生活保障金（货币/债券） | [X]% | ¥[X] |
| B 档 | 核心 ETF（宽基指数） | [X]% | ¥[X] |
| C 档 | 主动选股 | [X]% | ¥[X] |

### 目标差距
[实际资产 vs 目标的差距描述]

所以你该做什么：[具体下一步]
```

## 不做什么（Phase 2 边界）

- 不做仓位巡检（→ Skill ②）
- 不做风险量化（→ Skill ⑥）
- 不触碰 capital.yaml 的默认段
