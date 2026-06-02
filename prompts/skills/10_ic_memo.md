---
skill_id: ic_memo
name: 买入决策备忘录（IC Memo）
phase: 8
priority: P1
status: implemented
category: 研究分析类
---

# Skill ⑩ — 买入决策备忘录（IC Memo）

## 触发条件

- 用户准备新建仓（硬性前置条件）
- 用户说"帮我分析一下这只股票能不能买"
- 用户从 ③ 对话选股 跳转过来做深度分析
- 候选标的晋升为 IC Memo 研究对象

## 调用工具链

本 Skill 以 LLM 分析为主，配合以下 CLI 工具链：

```python
from investment.agent_tools.thesis import thesis_sync, thesis_list, thesis_stale
from investment.agent_tools.candidate import candidate_promote

# 1. 同步当前 thesis 状态
thesis_sync()

# 2. 检查是否有过期 thesis（超过 30 天未更新）
stale = thesis_stale(days=30)

# 3. 如有 comps-analysis 前置输出，加载估值数据
# 通过 /comps-analysis (Skill ⑪) 获取估值横向对比结果
```

**LLM 分析流程（7 步）：**

1. **五支柱论证**：对每个支柱评估内容、可验证指标（3-5 个+目标值）、当前评分（0-5 分）、证伪条件
2. **三情景目标价计算**：乐观/基准/悲观三种情景，明确假设 + 概率权重 + 目标价 + 潜在回报
3. **加权期望回报**：E(R) = Σ(概率 × 回报率)，须 > 年化目标
4. **估值三角形验证**：历史百分位 + 同业对比 + DCF 交叉验证
5. **风险回报比**：上行潜力 / 下行风险 ≥ 2:1
6. **仓位建议**：初始仓位 ≤ C 档 5%，资金路由见 `config/capital.yaml`
7. **合规检查 + 7 个反人性问题**：对照 `config/rules.yaml` 的 `bias_check` 清单

## 输入 Schema

```yaml
inputs:
  code:
    type: string
    description: 目标股票代码
    required: true
  name:
    type: string
    description: 目标股票名称
    required: false
  current_price:
    type: number
    description: 当前股价
    required: false
  current_holdings:
    type: string
    description: 当前持仓情况
    required: false
  comps_analysis_output:
    type: string
    description: Skill ⑪ 估值对比输出（如有）
    required: false
  preliminary_thesis:
    type: string
    description: 初步 thesis 草案
    required: false
  macro_context:
    type: string
    description: 宏观背景判断
    required: false
```

## 输出 Schema

```yaml
outputs:
  five_pillars:
    type: array
    items:
      pillar_name: string
      content: string
      verifiable_metrics: array
      current_score: number
      falsification_condition: string
  three_scenarios:
    type: object
    properties:
      optimistic: { probability: number, target_price: number, return_pct: number, assumptions: string }
      base: { probability: number, target_price: number, return_pct: number, assumptions: string }
      pessimistic: { probability: number, target_price: number, return_pct: number, assumptions: string }
  expected_return:
    type: number
    description: 加权期望回报率
  valuation_triangle:
    type: object
    properties:
      historical_percentile: string
      peer_comparison: string
      dcf_cross_check: string
  risk_reward_ratio:
    type: string
  position_recommendation:
    type: object
    properties:
      suggested_shares: number
      suggested_pct: number
      capital_route: string
  bias_check_results:
    type: array
    items:
      question: string
      answer: string
      flag: boolean
  human_message:
    type: string
```

## 用户话术模板

```
## IC Memo — [股票名称]（[代码]）

### 核心结论
[一句话总结：建议买入/等待/放弃，核心理由]

### 五支柱论证
| 支柱 | 内容 | 评分 | 状态 |
|------|------|------|------|
| 支柱1：[名称] | [一句话] | X/5 | 🟢/🟡/🔴 |
| ... | | | |

### 三情景目标价
| 情景 | 概率 | 目标价 | 潜在回报 | 核心假设 |
|------|------|--------|---------|---------|
| 🟢 乐观 | X% | ¥X | +X% | ... |
| 🟡 基准 | X% | ¥X | +X% | ... |
| 🔴 悲观 | X% | ¥X | -X% | ... |

**加权期望回报**：[X]%（须 > 年化目标）

### 估值三角形
- 历史百分位：[X]%
- 同业对比：[低估/合理/高估 X%]
- DCF 交叉验证：[结果]

### 风险回报比
上行 [X]% : 下行 [X]% = [X]:1（须 ≥ 2:1）

### 仓位建议
- 初始仓位：[N] 股，占 C 档 [X]%
- 资金路由：[来源]
- 冷静期：[7 天]

### 反人性检查清单
[7 个问题的逐一回答]

所以你该做什么：[如通过 → 等 7 天冷静期后执行；如不通过 → 明确写出缺什么才能通过]
```

## 不做什么

- 不执行实际交易（→ 由 ② 仓位管理 配合 `inv trade log` + `inv trade apply` 执行）
- 不做估值横向对比（→ 先调用 ⑪ comps-analysis，取其输出作为输入）
- 不做财报解读（→ 如最近有财报，先调用 ⑬ earnings-analysis）
- 写不出三种情景目标价时，不准推荐买入
