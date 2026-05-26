# 借鉴分析：GitHub 金融 Skill 设计模式 → 本项目 Prompt 适配

**文档日期：** 2026-05-24  
**目标：** 明确从 Anthropic 官方和社区 skill 中借鉴的设计模式，以及如何适配到本项目的 A 股投资系统

---

## 一、借鉴来源

### 1.1 Anthropic 官方 financial-services-plugins

**仓库：** https://github.com/anthropics/financial-services-plugins

**核心 Skills（与本项目相关）：**

| Skill | 功能 | 设计模式 |
|-------|------|--------|
| **earnings-analysis** | 财报后 48h 内完成 thesis 支柱验证 | 结构化指标提取 + beat/miss 判断 + 支柱状态更新 |
| **thesis-tracker** | 月度投资论点评分卡更新 | 五维度评分标准 + 支柱状态交通灯 + 决策触发条件 |
| **comps-analysis** | 可比公司估值对比 | 同业选择标准 + 多维指标表 + 估值结论 |
| **ic-memo** | 投资委员会决策备忘录 | 三情景分析 + 仓位建议公式 + 合规检查清单 |
| **idea-generation** | 候选池扫描与筛选 | 量化筛选门槛 + 主题集中度检查 + 初步 thesis 质量门槛 |

**官方设计原则（来自 Anthropic 官方文档）：**

1. **角色定义** — 每个 skill 嵌入一个领域专家角色（分析师、顾问、合规官）
2. **结构化分析框架** — 输入 → 多步骤推理 → 模板化输出
3. **输出模板** — 固定的 markdown 结构（Executive Summary + Analysis + Recommendations）
4. **决策树** — if-then 逻辑，阈值来自规则配置
5. **数据连接器** — 与外部数据源集成（本项目用 rules.yaml 替代）
6. **用户在循环中** — 所有输出需人工审核后才能执行

---

### 1.2 社区 Skill：alirezarezvani/claude-skills

**仓库：** https://github.com/alirezarezvani/claude-skills

**financial-analyst Skill 的核心特点：**

1. **Phased Methodology** — 分阶段的分析方法论
   - Phase 1: 数据收集与验证
   - Phase 2: 指标计算与对标
   - Phase 3: 结论与建议

2. **Visible Assumptions** — 显式化假设
   - 每个计算步骤都明确列出假设
   - 便于后续审查和调整

3. **Internal Consistency Checks** — 内部一致性检查
   - 多个维度的交叉验证
   - 防止逻辑矛盾

4. **Calibrated Uncertainty** — 校准的不确定性表达
   - 用概率/置信度表达不确定性
   - 而非绝对化的结论

---

## 二、借鉴内容详解

### 2.1 角色定义模式

**Anthropic 官方做法：**
```
You are a [domain expert] responsible for [specific task].
Your output will be reviewed by [audience] before [action].
Follow [specific framework] and [compliance rules].
```

**本项目适配：**
```
You are a [纪律型价值投资者 / 财报分析师 / 持仓论点追踪员]
responsible for [具体任务].
Your analysis must comply with rules.yaml [具体条款].
Output format must match [trades/decision_*.md 或 theses/*_thesis.md].
```

**示例（ic-memo）：**
```
You are an investment committee analyst responsible for pre-purchase 
decision-making on individual stocks. Your analysis must:
1. Comply with rules.yaml trading_rules and concentration rules
2. Output a decision memo that can be directly saved as trades/decision_*.md
3. Include mandatory bias_check questions from rules.yaml
```

---

### 2.2 结构化分析框架

**Anthropic 官方做法：**
- 多步骤 chain-of-thought 推理
- 每步骤有明确的输入/输出
- 步骤之间有检查点

**本项目借鉴：**

#### earnings-analysis 框架（来自官方 earnings-analysis skill）
```
Step 1: 关键数字提取（营收/利润/现金流/毛利率/指引）
Step 2: 同比/环比/vs 一致预期的三维对比
Step 3: 逐支柱验证（对照 thesis.md 的每个支柱）
Step 4: 评分卡更新（新评分 + 趋势变化）
Step 5: 后续行动建议（维持/减仓/清仓/补仓）
```

#### ic-memo 框架（来自官方 ic-memo skill）
```
Step 1: 五支柱论证（参考 600219_thesis.md 的支柱结构）
Step 2: 三情景目标价计算（悲观/基础/乐观 + 概率）
Step 3: 估值三角形验证（PE/PB/分红折现）
Step 4: 风险回报比计算（加权期望回报 vs 最大回撤）
Step 5: 仓位建议（基于风险回报比和 rules.yaml 上限）
Step 6: 合规检查（rules.yaml 前置条件）
Step 7: 反人性检查（bias_check 清单）
```

---

### 2.3 输出模板模式

**Anthropic 官方做法：**
- 固定的 markdown 结构
- 表格用于数据展示
- 清晰的 section 分隔

**本项目借鉴：**

#### earnings-analysis 输出模板
```markdown
# [标的代码] 财报分析 · [日期]

## 一、关键数字速览
| 指标 | 本季 | 同比 | 环比 | vs 预期 |
|-----|------|------|------|--------|
| 营收 | ... | ... | ... | ... |
| 净利 | ... | ... | ... | ... |

## 二、逐支柱验证
### 支柱 1: [支柱名称]
- 本季数据：...
- 支持度：✅ / ⚠️ / 🔴
- 状态变化：...

## 三、评分卡更新
| 支柱 | 上次评分 | 本次评分 | 变化 | 趋势 |
|-----|---------|---------|------|------|

## 四、后续行动
- 建议：[维持/减仓/清仓/补仓]
- 理由：...
- 触发条件：...
```

#### ic-memo 输出模板
```markdown
# [标的代码] 投资决策备忘录 · [日期]

## 一、标的快照
| 字段 | 数值 |
|-----|------|

## 二、五支柱论证
### 支柱 1: ...
- 逻辑：...
- 可验证指标：...

## 三、三情景目标价
| 情景 | 概率 | 假设 | 目标价 |
|-----|------|------|--------|

## 四、估值结论
- 三方法收敛区间：...
- 当前价位：...
- 安全边际：...

## 五、仓位建议
- 初始仓位：...
- 分批建仓：...

## 六、合规检查
- [ ] 单股仓位 ≤ 25%
- [ ] 行业集中度不超标
- [ ] 冷静期已计划
- [ ] IC Memo 已完成

## 七、反人性检查
Q1: 如果这笔钱现在是现金，今天的价格我会买它吗？
A: ...
```

---

### 2.4 决策树与阈值

**Anthropic 官方做法：**
- 决策树基于量化阈值
- 阈值来自规则配置（不是硬编码）
- 每个分支都有明确的动作

**本项目借鉴：**

#### thesis-tracker 决策树
```
IF thesis_score ≤ 2.0 AND 持续 2 个季度
  → 强制清仓审查（30 天内）
ELSE IF thesis_score 在 2.0-3.0
  → 减仓至 50% 以下
ELSE IF 任一支柱状态变为 🔴
  → 48h 内触发 earnings-analysis 或 ic-memo 复核
ELSE IF 评分环比下降 ≥ 1.0
  → 标记为"恶化标的"，下月优先审查
ELSE
  → 维持持仓，继续观察
```

#### earnings-analysis 决策树
```
IF 所有支柱维持 + 业绩超预期 (+5%)
  → 维持持仓，考虑加仓
ELSE IF 1 个支柱出现裂缝 + 业绩符合预期 (±5%)
  → 维持持仓，提高关注频率
ELSE IF 1 个支柱被证伪 OR 业绩大幅低于预期 (-5%)
  → 触发 ic-memo 复核
ELSE IF 2 个以上支柱被证伪
  → 启动退出流程
```

---

### 2.5 反人性检查清单

**Anthropic 官方做法：**
- 在 decision_process 中嵌入 bias_check
- 强制要求书面回答
- 不可跳过

**本项目借鉴：**

直接复用 rules.yaml 的 `bias_check.mandatory_questions`：

```yaml
mandatory_questions:
  - "如果这笔钱现在是现金，今天的价格我会买它吗？"
  - "我是在做决策，还是在为已有的情绪寻找理由？"
  - "如果决策错了，我能承受的最大损失是多少？"
  - "这个决策违反了哪一条 rules.yaml？我有正当豁免理由吗？"
  - "30 天后回看这个决策，我会为它感到骄傲吗？"
  - "我买它，是因为未来风险收益比好，还是因为我已经亏损不甘心？"
  - "如果这只股票今天清仓，我会重新买回同样仓位吗？"
```

---

## 三、A 股特有适配

### 3.1 Anthropic 官方 skill 的通用性 vs 本项目的特殊性

| 维度 | Anthropic 官方 | 本项目适配 |
|-----|---------------|---------|
| **市场** | 美股（流动性强、信息充分） | A 股（流动性弱、信息不对称） |
| **数据源** | FactSet、S&P、Morningstar 等 | 本地 rules.yaml + 手工数据 |
| **估值方法** | PE/PB/DCF（标准化） | PE/PB/分红折现 + 历史分位数 |
| **风险因素** | 系统性风险、行业周期 | 北向资金、大股东减持、解禁压力、政策风险 |
| **交易纪律** | 机构级别（合规、审计） | 个人散户（心理、情绪） |

### 3.2 A 股特有指标补充

#### comps-analysis 中的 A 股指标
```
标准指标：PE / PB / PS / EV-EBITDA
A 股补充：
  - 股息率（Dividend Yield）
  - PB 分位数（近 3/5 年）
  - ROE / ROIC
  - 净利润增速（YoY）
  - 自由现金流收益率（FCF Yield）
  - 历史 PE 分位数（当前 PE 在历史区间的位置）
```

#### earnings-analysis 中的 A 股关注点
```
标准指标：营收 / 利润 / 现金流 / 毛利率
A 股补充：
  - 大股东减持计划
  - 股权激励行权条件
  - 分红方案（分红率、派息时间）
  - 北向资金持仓变化
  - 机构持仓变化
  - 解禁压力
```

#### idea-generation 中的 A 股筛选条件
```
标准条件：PE < 25 / ROE > 10% / 市值 > 50 亿
A 股补充：
  - 近 12 个月无重大违规/处罚
  - 北向资金持仓趋势（持续净买入优先）
  - 机构持仓变化（机构持仓增加优先）
  - 解禁压力评估（解禁量 < 流通股 5% 优先）
```

---

## 四、本项目 5 个 Prompt 的借鉴映射

### 4.1 ic-memo.md

**借鉴来源：**
- Anthropic 官方 `ic-memo` skill（投资委员会决策框架）
- 社区 `financial-analyst` skill（phased methodology）

**借鉴内容：**
1. **角色定义** — 投资委员会分析师
2. **三情景分析** — 悲观/基础/乐观 + 概率 + 加权期望回报
3. **估值三角形** — PE/PB/分红折现三方法收敛
4. **仓位建议公式** — 简化 Kelly 公式
5. **合规检查清单** — rules.yaml 前置条件
6. **反人性检查** — bias_check 7 个问题

**A 股适配：**
- 补充 A 股特有风险因素（北向资金、大股东减持、解禁压力）
- 补充分红折现法（A 股高股息特点）
- 补充历史分位数估值（A 股周期性特点）

---

### 4.2 earnings-analysis.md

**借鉴来源：**
- Anthropic 官方 `earnings-analysis` skill（财报解读框架）
- 社区 `financial-analyst` skill（visible assumptions）

**借鉴内容：**
1. **关键数字提取** — 营收/利润/现金流/毛利率/指引
2. **三维对比** — 同比/环比/vs 一致预期
3. **逐支柱验证** — 对照 thesis.md 的每个支柱
4. **评分卡更新** — 新评分 + 趋势变化
5. **后续行动决策树** — if-then 逻辑

**A 股适配：**
- 补充大股东减持、股权激励、分红方案关注
- 补充北向资金、机构持仓变化
- 补充解禁压力评估

---

### 4.3 thesis-tracker.md

**借鉴来源：**
- Anthropic 官方 `thesis-tracker` skill（月度评分卡更新）
- 社区 `financial-analyst` skill（internal consistency checks）

**借鉴内容：**
1. **五维度评分标准** — 基本面/估值/技术面/催化剂/风险
2. **支柱状态交通灯** — ✅ / 🟡 / ⚠️ / ❌
3. **决策触发条件** — 评分 → 行动映射表
4. **证伪条件逐一核查** — 每个支柱的 veto trigger

**A 股适配：**
- 补充 A 股特有的证伪条件（北向资金持仓反转、大股东减持加速）
- 补充解禁压力监控
- 补充政策风险监控

---

### 4.4 comps-analysis.md

**借鉴来源：**
- Anthropic 官方 `comps-analysis` skill（可比公司分析框架）
- 社区 `financial-analyst` skill（calibrated uncertainty）

**借鉴内容：**
1. **可比公司选择标准** — 同行业/市值相近/业务模式相似
2. **核心估值指标表** — PE/PB/PS/EV-EBITDA/股息率
3. **历史估值百分位** — 近 3/5 年分位数
4. **相对估值结论** — 低估/合理/高估 + 折溢价幅度
5. **估值差异归因** — 为什么比同业便宜/贵

**A 股适配：**
- 补充 A 股特有指标（股息率、PB 分位数、ROE、FCF 收益率）
- 补充行业特殊调整（周期股用 PB/ROE，成长股用 PS/PEG，金融股用 PB/ROE）
- 补充北向资金持仓对比（机构认可度指标）

---

### 4.5 idea-generation.md

**借鉴来源：**
- Anthropic 官方 `idea-generation` skill（候选池扫描框架）
- 社区 `financial-analyst` skill（phased methodology）

**借鉴内容：**
1. **量化筛选门槛** — PE/ROE/股息率/市值/违规记录
2. **rules.yaml 合规前置检查** — 行业/主题集中度不超标
3. **初步 thesis 质量门槛** — 必须能写出 ≥3 个可验证支柱
4. **结构化输出模板** — 每个候选标的固定字段
5. **优先级排序** — 基于风险回报比和组合互补性

**A 股适配：**
- 补充 A 股特有筛选条件（北向资金持仓趋势、机构持仓变化、解禁压力）
- 补充行业分散化优先（避免新能源链已超标的重复）
- 补充与现有持仓高相关性排除

---

## 五、设计模式总结表

| 设计模式 | Anthropic 官方 | 社区 Skill | 本项目借鉴 |
|---------|--------------|----------|---------|
| **角色定义** | ✅ 领域专家 + 输出受众 | ✅ 分析师视角 | ✅ 纪律型投资者 + rules.yaml 合规 |
| **结构化框架** | ✅ 多步骤 CoT | ✅ Phased methodology | ✅ 5 步骤 + 检查点 |
| **输出模板** | ✅ 固定 markdown 结构 | ✅ 表格 + section | ✅ 直接对应 trades/ 和 theses/ |
| **决策树** | ✅ if-then 逻辑 + 阈值 | ✅ 量化条件 | ✅ rules.yaml 驱动 |
| **反人性检查** | ✅ bias_check 清单 | ✅ 显式假设 | ✅ 7 个强制问题 |
| **数据连接** | ✅ MCP 连接器 | ✅ 数据源明确 | ✅ rules.yaml + 本地数据 |
| **A 股适配** | ❌ 无 | ❌ 无 | ✅ 北向资金/大股东/解禁/政策 |

---

## 六、实施计划

### 6.1 填充顺序（按优先级）

1. **ic-memo.md** — 最关键，新建仓必须先跑
   - 借鉴：Anthropic ic-memo + 社区 phased methodology
   - 适配：三情景 + 仓位公式 + A 股风险因素

2. **earnings-analysis.md** — 财报后 48h 触发
   - 借鉴：Anthropic earnings-analysis + 社区 visible assumptions
   - 适配：逐支柱验证 + A 股关注点

3. **thesis-tracker.md** — 月度例行
   - 借鉴：Anthropic thesis-tracker + 社区 consistency checks
   - 适配：五维度评分 + 交通灯系统

4. **comps-analysis.md** — 买入前/季度必跑
   - 借鉴：Anthropic comps-analysis + 社区 calibrated uncertainty
   - 适配：A 股指标 + 行业特殊调整

5. **idea-generation.md** — 每月候选池扫描
   - 借鉴：Anthropic idea-generation + 社区 phased methodology
   - 适配：A 股筛选条件 + 主题集中度检查

### 6.2 验证方式

每个 prompt 填充完成后，验证标准：
1. ✅ 可以直接复制粘贴给 Claude，不需要额外解释
2. ✅ 输出格式与现有 trades/ 和 theses/ 文件结构一致
3. ✅ 内嵌了 rules.yaml 的关键约束，不会产生违规建议
4. ✅ 包含反人性检查，防止情绪化决策
5. ✅ 包含 A 股特有的风险因素和指标

---

**文档结束 · 2026-05-24**

Sources:
- [Anthropic Financial Services Agents](https://anthropic.com/news/finance-agents)
- [Claude Prompting Strategies for Financial Analysis](https://claude.com/resources/tutorials/prompting-strategies-for-financial-analysis)
- [anthropics/financial-services-plugins](https://github.com/anthropics/financial-services-plugins)
- [alirezarezvani/claude-skills](https://github.com/alirezarezvani/claude-skills)
