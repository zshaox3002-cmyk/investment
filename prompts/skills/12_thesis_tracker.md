---
skill_id: thesis_tracker
name: 持仓论点跟踪与月度评分卡
phase: 8
priority: P1
status: implemented
category: 研究分析类
---

# Skill ⑫ — 持仓论点跟踪与月度评分卡

## 触发条件

- 每月末（强制）
- 财报发布后（配合 ⑬ earnings-analysis）
- 单股回撤 ≥15% 时（由 ② 仓位管理 触发）
- 用户说"帮我更新一下评分卡"
- 用户说"检查一下持仓论点是否还成立"

## 调用工具链

```python
from investment.agent_tools.thesis import thesis_sync, thesis_list, thesis_score, thesis_stale
from investment.agent_tools.position_monitor import run_position_monitor

# 1. 同步 thesis 文件 → DB
thesis_sync()

# 2. 检查过期论点（>30 天未更新）
stale = thesis_stale(days=30)

# 3. 获取当前持仓状态和盈亏
report = run_position_monitor()

# 4. 逐只评分（五维度）
# thesis_score("600519", score=4.2, dimension="overall", rationale="...")
```

**LLM 分析流程（5 步）：**

1. **五维度评分**：基本面（20%）、护城河（25%）、估值（20%）、催化剂（15%）、风险（20%）→ 0-5 分 + 评分依据
2. **交通灯支柱状态**：✅（confirm）/ 🟡（watch）/ ⚠️（warning）/ 🔴（broken）
3. **恶化标的识别**：评分连续下降、支柱破裂、回撤超阈值
4. **决策触发条件判断**：维持/减仓/清仓/补仓的条件阈值
5. **评分卡更新**：写入 `theses/{code}_thesis.md` 的评分卡章节

## 输入 Schema

```yaml
inputs:
  codes:
    type: array
    description: 要评估的股票代码列表（默认全部持仓）
    items:
      type: string
    required: false
  as_of_month:
    type: string
    format: YYYY-MM
    description: 评分月份
    required: false
  trigger_reason:
    type: string
    enum: [monthly, earnings, drawdown, manual]
    description: 触发原因
    default: monthly
```

## 输出 Schema

```yaml
outputs:
  scorecards:
    type: array
    items:
      code: string
      name: string
      overall_score: number
      dimension_scores:
        fundamentals: number
        moat: number
        valuation: number
        catalyst: number
        risk: number
      pillar_status:
        type: array
        items:
          pillar_name: string
          status: string   # confirm/watch/warning/broken
      trend: string         # improving/stable/deteriorating
      decision_trigger: string
  deteriorated_stocks:
    type: array
    description: 需要优先审查的恶化标的
  stale_theses:
    type: array
    description: 超过30天未更新的thesis
  human_message:
    type: string
```

## 用户话术模板

```
## 月度 Thesis 评分卡 — [YYYY年MM月]

### 评分总览
| 股票 | 总评分 | 趋势 | 基本面 | 护城河 | 估值 | 催化剂 | 风险 | 决策 |
|------|--------|------|--------|--------|------|--------|------|------|
| [名称] | X.X/5 | ↑→↓ | X | X | X | X | X | 维持/减仓/... |

### 支柱状态（交通灯）
**600519 贵州茅台**
| 支柱 | 状态 | 上次评分 | 本次评分 | 变化说明 |
|------|------|---------|---------|---------|
| 支柱1：[名称] | ✅/🟡/⚠️/🔴 | X/5 | X/5 | ... |

### ⚠️ 需优先审查
[恶化标的列表 + 具体原因 + 行动建议]

### 📋 过期 Thesis
[超 30 天未更新的 thesis 列表]

所以你该做什么：[逐只处理恶化标的；更新过期 thesis；对评分 <2.5 的标的启动减仓决策]
```

## 不做什么

- 不做买入分析（→ 触发 ⑩ IC Memo）
- 不做财报深度解读（→ 先调用 ⑬ earnings-analysis，取其评分更新）
- 不做交易执行（→ 由 ② 仓位管理 配合 `inv trade decision` 执行）
