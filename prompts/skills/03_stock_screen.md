---
skill_id: stock_screen
name: 对话式选股
phase: 7
priority: P2
status: implemented
---

# Skill ③ — 对话式选股

## 触发条件

- 用户描述选股条件（PE/ROE/股息率/行业/市值等）
- 用户说"帮我找股票"、"有什么值得买的"
- 月度候选池扫描时

## 调用工具链（Phase 7 已实现）

```python
from investment.agent_tools.stock_screen import (
    parse_screen_query, run_screen, save_strategy, list_strategies,
)

# 1. 解析用户口语查询 → 结构化筛选条件
criteria = parse_screen_query("帮我找低PE高股息的消费股")
# criteria.pe_max, criteria.roe_min, criteria.dividend_yield_min, criteria.industry, criteria.style_tags

# 2. 执行扫描 + 风格标注 + 人话输出
result = run_screen("帮我找低PE高股息的消费股", save_as="低PE高股息消费")
# result.criteria, result.candidates_raw, result.style_comment, result.human_message

# 3. 可选：查看已保存策略
strategies = list_strategies()
# CLI: inv candidate scan --quick
```

## 输入 Schema

```yaml
inputs:
  natural_language_query:
    type: string
    description: 用户的口语选股描述
    required: true
  save_strategy:
    type: boolean
    description: 是否保存为可复用策略
    default: false
  strategy_name:
    type: string
    description: 策略名称（save_strategy=true 时必填）
    required: false
```

## 输出 Schema

```yaml
outputs:
  parsed_criteria:
    type: object
    description: 解析后的结构化筛选条件
  candidates:
    type: array
    items:
      code: string
      name: string
      key_metrics: object
      match_reason: string
      style_tags: array
  strategy_id:
    type: integer
    description: 保存的策略 ID（如有）
  human_message:
    type: string
```

## 用户话术模板

```
## 选股结果 — [日期]

### 你的筛选条件
[解析后的条件，人话描述]

### 候选标的（[N] 只）
| 股票 | 核心指标 | 匹配理由 | 风格 |
|------|---------|---------|------|
| [名称] | PE=[X] ROE=[X]% | [理由] | [价值/成长] |

所以你该做什么：对感兴趣的标的运行 /ic-memo 做深度分析，通过后才能建仓。
```

## 不做什么（Phase 7 边界）

- 不做买入决策（→ 触发 `/ic-memo`）
- 不做估值横向对比（→ `/comps-analysis`）
