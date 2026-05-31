---
skill_id: risk
name: 组合风险量化
phase: 4
priority: P1
status: skeleton
---

# Skill ⑥ — 组合风险量化

## 触发条件

- 月度风险评估
- 用户询问组合风险指标
- 新建仓前风险检查
- 回撤超过阈值时

## 调用工具链（Phase 4 已实现）

```python
from investment.agent_tools.risk_engine import run_risk_engine

# 计算完整风险报告（默认回溯 60 个交易日）
report = run_risk_engine(lookback_days=60, save=True)

# report.portfolio_vol       — 年化波动率
# report.max_drawdown        — 最大回撤（负数）
# report.dd_duration_days    — 回撤持续天数
# report.var_95              — 95% VaR（单日，负数）
# report.sharpe_ratio        — Sharpe 比率
# report.risk_contributions  — 各持仓风险贡献列表
# report.high_correlations   — 高相关持仓对（>0.7）
# report.pseudo_div          — 伪分散检测结果
# report.human_message       — 完整人话报告（含"所以你该做什么"）

# CLI 入口
# inv risk compute --lookback 60
# inv risk show
```

## 输入 Schema

```yaml
inputs:
  lookback_days:
    type: integer
    description: 历史数据回溯天数
    default: 252
  confidence_level:
    type: number
    description: VaR 置信水平
    default: 0.95
```

## 输出 Schema

```yaml
outputs:
  portfolio_vol:
    type: number
    description: 组合年化波动率（%）
  max_drawdown:
    type: number
    description: 最大回撤（%）
  dd_duration_days:
    type: integer
    description: 最大回撤持续天数
  var_95:
    type: number
    description: 95% VaR（单日，%）
  pseudo_diversification:
    type: object
    properties:
      detected: boolean
      description: string    # 人话描述
      concentrated_theme: string
  risk_contributions:
    type: array
    items:
      code: string
      name: string
      contrib_pct: number
  human_message: string
```

## 用户话术模板

```
## 组合风险报告 — [日期]

### 核心风险指标
| 指标 | 数值 | 评价 |
|------|------|------|
| 年化波动率 | [X]% | [低/中/高] |
| 最大回撤 | [X]% | [可接受/需关注] |
| 95% VaR | [X]% | [单日最大损失] |

### 风险集中度
[伪分散警示（如有）]

### 各持仓风险贡献
| 股票 | 风险贡献 |
|------|---------|
| [名称] | [X]% |

所以你该做什么：[具体操作]
```

## 不做什么（Phase 4 边界）

- 不做业绩归因（→ Skill ⑧，Phase 5）
- 不做情景压力测试（后续增强项）
