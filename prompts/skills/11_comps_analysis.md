---
skill_id: comps_analysis
name: 估值横向对比分析
phase: 8
priority: P1
status: implemented
category: 研究分析类
---

# Skill ⑪ — 估值横向对比分析

## 触发条件

- 买入前（配合 ⑩ IC Memo 使用）
- 每季度定期更新持仓估值
- 用户说"帮我对比一下这几只股票的估值"
- 用户说"这只股票现在便宜还是贵"

## 调用工具链

本 Skill 以 LLM 分析为主，配合数据工具链：

```python
from investment.agent_tools.candidate import candidate_scan, candidate_list
from investment.agent_tools.snapshot import snapshot_pull

# 1. 拉取最新行情数据（含 PE/PB/PS 等估值指标）
snapshot_pull()

# 2. 扫描同行业可比公司
# candidate_scan(quick=True) → candidate_list()

# 3. 从 quotes 表获取历史估值序列用于百分位计算
# DB: SELECT pe_ttm, pb, ps_ttm FROM quotes JOIN instruments ...
```

**LLM 分析流程（5 步）：**

1. **可比公司选择**：GICS 二级分类相同、市值在目标 50%-200% 范围、业务模式相似
2. **核心估值指标提取**：PE(TTM)、PB、PS(TTM)、EV/EBITDA + A 股特有指标（股息率、ROE）
3. **历史估值百分位**：近 3/5 年 PE/PB 分位数
4. **同业估值对比表**：目标 vs 3-5 家可比公司的估值矩阵
5. **估值结论**：低估/合理/高估 + 折溢价幅度 + 安全边际判断

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
  industry:
    type: string
    description: 行业分类
    required: false
  peers:
    type: array
    description: 可比公司代码列表（可自动识别或手动指定）
    items:
      type: string
    required: false
  data_date:
    type: string
    format: YYYY-MM-DD
    description: 数据截止日期
    required: false
```

## 输出 Schema

```yaml
outputs:
  target_valuation:
    type: object
    properties:
      pe_ttm: number
      pb: number
      ps_ttm: number
      ev_ebitda: number
      dividend_yield: number
      roe: number
  historical_percentiles:
    type: object
    properties:
      pe_percentile_3y: number
      pe_percentile_5y: number
      pb_percentile_3y: number
  peer_comparison:
    type: array
    items:
      code: string
      name: string
      market_cap: number
      pe_ttm: number
      pb: number
      roe: number
      revenue_growth: number
  valuation_conclusion:
    type: object
    properties:
      verdict: string       # 低估/合理/高估
      discount_premium: string
      safety_margin: string
      confidence: string    # 高/中/低
  human_message:
    type: string
```

## 用户话术模板

```
## 估值横向对比 — [股票名称]（[代码]）

### 目标标的估值概览
| 指标 | 当前值 | 近3年分位 | 近5年分位 | 评价 |
|------|--------|----------|----------|------|
| PE(TTM) | [X] | [X]% | [X]% | [偏低/合理/偏高] |
| PB | [X] | [X]% | [X]% | |
| PS(TTM) | [X] | | | |
| EV/EBITDA | [X] | | | |
| 股息率 | [X]% | | | |

### 同业估值矩阵
| 公司 | 市值 | PE | PB | ROE | 营收增速 |
|------|------|-----|-----|-----|---------|
| [目标] | ¥[X]亿 | [X] | [X] | [X]% | [X]% |
| [可比1] | | | | | |
| ... | | | | | |

### 估值结论
[标的] 当前 PE 处于近 3 年 [X]% 分位，同业对比 [折价/溢价 X%]。
综合判断：[低估/合理/高估]，安全边际约 [X]%。

所以你该做什么：[如低估 → 将结论输入 ⑩ IC Memo 的估值三角形章节；如高估 → 等待回调或放弃]
```

## 不做什么

- 不做买入决策（→ 输出给 ⑩ IC Memo 作为估值三角形输入）
- 不做深度财务分析（→ 如有财报，调用 ⑬ earnings-analysis）
- 不做技术面分析（本 Skill 聚焦基本面估值）
