---
skill_id: earnings_analysis
name: 财报解读与Thesis支柱验证
phase: 8
priority: P1
status: implemented
category: 研究分析类
---

# Skill ⑬ — 财报解读与 Thesis 支柱验证

## 触发条件

- 持仓股票发布财报后 48 小时内（强制）
- 用户说"帮我分析一下这份财报"
- 投资日历（⑤）的财报任务到期时
- 用户说"XX 发财报了，对我的持仓有什么影响"

## 调用工具链

```python
from investment.agent_tools.thesis import thesis_sync, thesis_score
from investment.agent_tools.snapshot import snapshot_pull
from investment.agent_tools.calendar import complete_task

# 1. 拉取最新行情（财报后股价反应）
snapshot_pull()

# 2. 同步 thesis 获取当前支柱状态
thesis_sync()

# 3. 财报分析完成后，更新评分卡
# thesis_score("600519", score=4.0, dimension="fundamentals", rationale="Q1营收超预期...")
# thesis_score("600519", score=3.5, dimension="overall", rationale="...")

# 4. 完成投资日历中的财报任务
# complete_task(task_id, notes="财报分析完成")
```

**LLM 分析流程（5 步）：**

1. **关键数字提取**：营收、归母净利润、扣非净利润、毛利率、经营现金流、管理层指引
2. **三维对比**：同比（YoY）vs 环比（QoQ）vs 一致预期
3. **逐支柱 ✅/⚠️/🔴 验证**：每个 thesis 支柱在新数据下是否仍然成立
4. **评分卡更新**：新评分 + 趋势变化（↑→↓）+ 更新依据
5. **后续行动建议**：维持/减仓/清仓/补仓，附具体条件和时间窗口

## 输入 Schema

```yaml
inputs:
  code:
    type: string
    description: 股票代码
    required: true
  name:
    type: string
    description: 股票名称
    required: false
  report_type:
    type: string
    enum: [Q1, Q2, Q3, Q4, annual]
    description: 财报类型
    required: true
  report_date:
    type: string
    format: YYYY-MM-DD
    description: 财报发布日期
    required: true
  consensus_data:
    type: object
    description: 一致预期数据（如有）
    required: false
```

## 输出 Schema

```yaml
outputs:
  key_figures:
    type: object
    properties:
      revenue: number
      net_profit: number
      operating_cashflow: number
      gross_margin: number
      guidance: string
  three_way_comparison:
    type: array
    items:
      metric: string
      current: number
      yoy_pct: number
      qoq_pct: number
      vs_consensus: string
  pillar_verification:
    type: array
    items:
      pillar_name: string
      verdict: string        # ✅/⚠️/🔴
      evidence: string
      updated_score: number
  scorecard_updates:
    type: array
    items:
      dimension: string
      old_score: number
      new_score: number
      trend: string          # ↑ → ↓
      rationale: string
  action_plan:
    type: object
    properties:
      recommendation: string # 维持/减仓/清仓/补仓
      conditions: string
      time_window: string
  human_message:
    type: string
```

## 用户话术模板

```
## 财报解读 — [股票名称]（[代码]）[Q1/Q2/Q3/Q4/年报]

### 关键数字
| 指标 | 本季值 | 同比 | 环比 | vs 预期 | 评价 |
|------|--------|------|------|--------|------|
| 营收 | ¥X 亿 | +X% | +X% | 超/低/符合 | |
| 归母净利润 | ¥X 亿 | | | | |
| 毛利率 | X% | | | | |
| 经营现金流 | ¥X 亿 | | | | |

### 核心发现
[2-3 条最重要的发现，每条附对 thesis 的影响]

### Thesis 支柱验证
| 支柱 | 财报前状态 | 财报验证 | 新评分 | 变化 |
|------|-----------|---------|--------|------|
| 支柱1 | 🟡 | ✅/⚠️/🔴 | X/5 | ↑→↓ |
| ... | | | | |

### 评分卡更新
[具体评分变更 + 依据]

### 后续行动
- 建议：[维持/减仓/清仓/补仓]
- 触发条件：[具体]
- 时间窗口：[具体]

所以你该做什么：[具体操作 + 如需调整仓位，启动 ② 仓位管理 的 trade decision 流程]
```

## 不做什么

- 不做初始买入分析（→ 由 ⑩ IC Memo 负责）
- 不做估值横向对比（→ 由 ⑪ comps-analysis 负责）
- 不直接执行交易（→ 输出给 ⑫ thesis_tracker 更新评分卡，由 ② 仓位管理 执行）
