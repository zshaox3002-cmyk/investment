---
skill_id: idea_generation
name: 月度候选池扫描与创意生成
phase: 8
priority: P2
status: implemented
category: 研究分析类
---

# Skill ⑭ — 月度候选池扫描与创意生成

## 触发条件

- 每月末（强制）
- 用户说"帮我扫描一下候选池"
- 用户说"有什么新的投资机会"
- 投资日历（⑤）的月度扫描任务到期时

## 调用工具链

```python
from investment.agent_tools.candidate import candidate_scan, candidate_list, candidate_refresh, candidate_promote
from investment.agent_tools.thesis import thesis_sync, thesis_stale
from investment.agent_tools.position_monitor import run_position_monitor

# 1. 获取当前持仓和行业集中度
report = run_position_monitor()
# 识别 C 档当前配置、行业集中度、主题集中度

# 2. 扫描候选池（quick 模式用 akshare）
candidate_scan(quick=True)

# 3. 刷新候选 PE/市值/市净率
candidate_refresh()

# 4. 查看候选列表
candidates = candidate_list()

# 5. 对高质量候选晋升为 IC Memo 研究对象
# candidate_promote(candidate_id)

# 6. 同步 thesis 检查论据质量门槛
thesis_sync()
stale = thesis_stale(days=30)
```

**LLM 分析流程（5 步）：**

1. **量化筛选**：PE<25、ROE>10%（3年均值）、股息率>2%（可选）、市值>50亿、无违规记录
2. **rules.yaml 合规检查**：行业集中度不超限、主题集中度不超限、与现有持仓互补
3. **Thesis 质量门槛**：必须能写出 ≥3 个可验证支柱，否则淘汰
4. **优先级排序**：风险回报比 × 组合互补性 × thesis 质量
5. **结构化输出**：每个候选 → 代码/名称/核心指标/匹配理由/风险点/建议下一步

## 输入 Schema

```yaml
inputs:
  current_holdings:
    type: array
    description: 当前 C 档持仓列表（自动从 position_monitor 获取）
    required: false
  industry_constraints:
    type: object
    description: 行业/主题集中度约束（自动从 rules.yaml 读取）
    required: false
  scan_scope:
    type: string
    enum: [full_market, specific_industry, specific_theme]
    description: 扫描范围
    default: full_market
  max_results:
    type: integer
    description: 最多返回候选数
    default: 10
```

## 输出 Schema

```yaml
outputs:
  scan_summary:
    type: object
    properties:
      total_scanned: integer
      passed_quant_screen: integer
      passed_compliance: integer
      final_candidates: integer
  candidates:
    type: array
    items:
      rank: integer
      code: string
      name: string
      key_metrics:
        pe_ttm: number
        roe: number
        dividend_yield: number
        market_cap: number
        industry: string
      match_reason: string
      complementarity: string     # 与现有持仓的互补性
      risk_points: array
      suggested_next_step: string # promote / comps / ic_memo / pass
  compliance_breaches_avoided:
    type: array
    description: 因合规检查被淘汰的候选
  human_message:
    type: string
```

## 用户话术模板

```
## 月度候选池扫描 — [YYYY年MM月]

### 扫描概览
- 全市场扫描：[N] 只
- 通过量化筛选：[N] 只（淘汰 [N] 只）
- 通过合规检查：[N] 只（淘汰 [N] 只因行业/主题集中度）
- 通过 thesis 门槛：[N] 只
- **最终候选：[N] 只**

### 当前 C 档约束
- 可用仓位空间：[X]%（上限 X%）
- 行业集中度上限：单行业 ≤ [X]%
- 主题集中度上限：单主题 ≤ [X]%

### 候选标的（按优先级排序）
| # | 股票 | PE | ROE | 股息率 | 行业 | 匹配理由 | 互补性 | 建议 |
|---|------|-----|-----|--------|------|---------|--------|------|
| 1 | [名称] | [X] | [X]% | [X]% | [行业] | [理由] | [互补] | promote / comps |

### 风险提示
[每个候选的主要风险点]

所以你该做什么：对排名前 3 的候选运行 ⑪ comps-analysis 做估值对比，通过后晋升为 ⑩ IC Memo 研究对象。
```

## 不做什么

- 不做即时/对话式选股（→ ③ stock_screen 负责 ad-hoc 查询）
- 不做买入决策（→ 输出给 ⑩ IC Memo）
- 不做估值分析（→ 对感兴趣的候选调用 ⑪ comps-analysis）
- 不替代 rules.yaml 的合规检查（复用现有 rule_breaches 机制）
