---
skill_id: attribution
name: 业绩归因
phase: 5
priority: P1
status: skeleton
---

# Skill ⑧ — 业绩归因

## 触发条件

- 季度/年度复盘
- 用户询问收益来源
- 月度总结

## 调用工具链（Phase 5 已实现）

```python
from investment.agent_tools.attribution import run_attribution

# 计算业绩归因（默认 vs 沪深300，默认最近30天）
result = run_attribution(
    period_start="2026-01-01",
    period_end="2026-03-31",
    benchmark_code="000300",
    save=True,
)

# result.total_return        — 组合总收益
# result.benchmark_return    — 基准收益
# result.excess_return       — 超额收益
# result.timing_contrib      — 择时贡献
# result.selection_contrib   — 选股贡献
# result.allocation_contrib  — 配置贡献
# result.human_message       — 完整人话报告（含能力边界结论+"所以你该做什么"）

# 不变量：timing + selection + allocation + interaction ≈ excess_return

# CLI 入口
# inv attribution compute --start 2026-01-01 --end 2026-03-31
# inv attribution show
```

## 输入 Schema

```yaml
inputs:
  period_start:
    type: string
    format: YYYY-MM-DD
    description: 归因起始日期
    required: true
  period_end:
    type: string
    format: YYYY-MM-DD
    description: 归因截止日期
    default: today
  benchmark:
    type: string
    description: 基准指数代码
    default: "000300"  # 沪深 300
```

## 输出 Schema

```yaml
outputs:
  total_return:
    type: number
    description: 组合总收益（%）
  benchmark_return:
    type: number
    description: 基准收益（%）
  excess_return:
    type: number
    description: 超额收益（%）
  timing_contrib:
    type: number
    description: 择时贡献（%）
  selection_contrib:
    type: number
    description: 选股贡献（%）
  allocation_contrib:
    type: number
    description: 配置贡献（%）
  ability_assessment:
    type: string
    description: 能力边界人话结论
  human_message: string
```

## 用户话术模板

```
## 业绩归因 — [起始日期] 至 [截止日期]

### 收益总览
| 指标 | 数值 |
|------|------|
| 组合收益 | [X]% |
| 基准（沪深 300） | [X]% |
| 超额收益 | [±X]% |

### 收益来源拆解
| 来源 | 贡献 | 说明 |
|------|------|------|
| 择时 | [X]% | [买卖时机的贡献] |
| 选股 | [X]% | [选对/选错股票的贡献] |
| 配置 | [X]% | [仓位分配的贡献] |

### 能力边界结论
[诚实的人话结论，如"赚的 15% 里 13% 是大盘给的，选股只贡献 2%"]

所以你该做什么：[具体操作]
```

## 不做什么（Phase 5 边界）

- 不做因果归因（→ Skill ④，Phase 6）——收益拆解 ≠ 异动原因
- 不做情景压力测试（后续增强项）
