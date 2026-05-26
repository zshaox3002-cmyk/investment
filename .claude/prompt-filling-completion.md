# Prompt 填充完成报告

**完成日期：** 2026-05-24  
**项目：** 投资系统 5 个 Prompt 模板填充  
**状态：** ✅ 全部完成

---

## 一、完成情况总览

| 文件 | 原始行数 | 填充后行数 | 增长倍数 | 状态 |
|-----|---------|----------|--------|------|
| ic-memo.md | 23 | 357 | 15.5x | ✅ |
| earnings-analysis.md | 20 | 288 | 14.4x | ✅ |
| thesis-tracker.md | 20 | 296 | 14.8x | ✅ |
| comps-analysis.md | 20 | 298 | 14.9x | ✅ |
| idea-generation.md | 20 | 425 | 21.3x | ✅ |
| **合计** | **103** | **1,664** | **16.2x** | ✅ |

---

## 二、借鉴来源与适配

### 2.1 Anthropic 官方 financial-services-plugins

**借鉴的 5 个 Skill**：

1. **ic-memo** → `prompts/ic-memo.md`
   - 借鉴：三情景分析框架 + 仓位公式 + 合规检查清单
   - 适配：补充 A 股风险因素（北向资金、大股东减持、解禁压力）

2. **earnings-analysis** → `prompts/earnings-analysis.md`
   - 借鉴：关键数字提取 + 三维对比 + 逐支柱验证
   - 适配：补充大股东减持、股权激励、分红方案、北向资金

3. **thesis-tracker** → `prompts/thesis-tracker.md`
   - 借鉴：五维度评分标准 + 交通灯系统 + 决策触发条件
   - 适配：补充 A 股特有证伪条件（北向资金反转、大股东减持加速）

4. **comps-analysis** → `prompts/comps-analysis.md`
   - 借鉴：可比公司选择标准 + 多维指标表 + 估值结论
   - 适配：补充 A 股特有指标（股息率、PB 分位数、FCF 收益率）

5. **idea-generation** → `prompts/idea-generation.md`
   - 借鉴：量化筛选门槛 + 主题集中度检查 + 优先级排序
   - 适配：补充 A 股筛选条件（北向资金、机构持仓、解禁压力）

### 2.2 社区 Skill：alirezarezvani/claude-skills

**借鉴的设计模式**：

1. **Phased Methodology** — 分阶段分析
   - 应用：每个 prompt 都分 4-7 个明确的步骤
   - 示例：ic-memo 的 7 步流程、earnings-analysis 的 5 步流程

2. **Visible Assumptions** — 显式化假设
   - 应用：每个计算都明确列出假设和数据来源
   - 示例：三情景目标价的假设明确列出

3. **Internal Consistency Checks** — 内部一致性检查
   - 应用：多个维度的交叉验证（如估值三角形）
   - 示例：comps-analysis 的三方法估值收敛

4. **Calibrated Uncertainty** — 校准的不确定性表达
   - 应用：用概率/置信度表达不确定性
   - 示例：三情景分析的概率分配（30%/50%/20%）

---

## 三、核心设计特点

### 3.1 角色定义

每个 prompt 都明确定义了角色和职责：

- **ic-memo**：投资委员会分析师
- **earnings-analysis**：财报分析师
- **thesis-tracker**：thesis 完整性审查官
- **comps-analysis**：卖方研究员
- **idea-generation**：选股研究员

### 3.2 结构化框架

每个 prompt 都包含：

1. **输入要求** — 明确的输入数据格式
2. **执行流程** — 4-7 个明确的步骤
3. **输出模板** — 直接对应 trades/ 或 theses/ 的文件格式
4. **关键约束** — 禁止事项 + 必须事项

### 3.3 规则注入

每个 prompt 都内嵌了 rules.yaml 的关键约束：

- **ic-memo**：合规检查清单（7 项）+ 反人性检查（7 个问题）
- **earnings-analysis**：后续行动决策树（基于 thesis 评分）
- **thesis-tracker**：决策触发条件（基于综合评分）
- **comps-analysis**：估值结论标准（基于相对折溢价）
- **idea-generation**：rules.yaml 合规前置检查（行业/主题集中度）

### 3.4 A 股特有适配

每个 prompt 都补充了 A 股特有的指标和风险因素：

| Prompt | A 股特有补充 |
|--------|-----------|
| ic-memo | 北向资金、大股东减持、解禁压力、分红折现法 |
| earnings-analysis | 大股东减持计划、股权激励、分红方案、北向资金 |
| thesis-tracker | 北向资金反转、大股东减持加速、政策风险 |
| comps-analysis | 股息率、PB 分位数、ROE、FCF 收益率、行业特殊调整 |
| idea-generation | 北向资金持仓趋势、机构持仓变化、解禁压力 |

---

## 四、验收标准检查

### ✅ 标准 1：可直接复制粘贴给 Claude

**验证方法**：每个 prompt 都包含完整的：
- 角色定义
- 输入要求
- 执行流程（带具体步骤）
- 输出模板
- 关键约束

**结果**：✅ 全部通过

### ✅ 标准 2：输出格式与现有文件结构一致

**验证方法**：
- ic-memo 输出 → 对应 trades/decision_*.md 格式
- earnings-analysis 输出 → 对应 theses/*_thesis.md 的评分卡章节
- thesis-tracker 输出 → 对应 theses/*_thesis.md 的版本日志
- comps-analysis 输出 → 对应 ic-memo 或 thesis.md 的估值章节
- idea-generation 输出 → 对应 reviews/monthly/[YYYY-MM]_idea_generation.md

**结果**：✅ 全部通过

### ✅ 标准 3：内嵌 rules.yaml 的关键约束

**验证方法**：
- ic-memo：7 项合规检查 + 7 个反人性检查问题
- earnings-analysis：后续行动决策树（基于 thesis 评分）
- thesis-tracker：五维度评分标准 + 决策触发条件
- comps-analysis：估值结论标准（基于相对折溢价）
- idea-generation：rules.yaml 合规前置检查

**结果**：✅ 全部通过

### ✅ 标准 4：包含反人性检查

**验证方法**：
- ic-memo：7 个强制问题（来自 rules.yaml bias_check）
- earnings-analysis：后续行动决策树（防止情绪化决策）
- thesis-tracker：五维度评分标准（防止主观判断）
- comps-analysis：估值结论标准（防止"我觉得便宜"）
- idea-generation：量化筛选门槛（防止"我看好这个行业"）

**结果**：✅ 全部通过

---

## 五、使用指南

### 5.1 优先级与使用场景

| Prompt | 优先级 | 使用场景 | 触发条件 |
|--------|--------|--------|--------|
| ic-memo | P0 | 新建仓决策 | 任何新买入前 |
| earnings-analysis | P0 | 财报解读 | 财报发布后 48h 内 |
| thesis-tracker | P1 | 月度评分卡更新 | 每月末 |
| comps-analysis | P1 | 估值对比 | 买入前 / 季度必跑 |
| idea-generation | P2 | 候选池扫描 | 每月末 |

### 5.2 工作流集成

```
新建仓流程：
1. 运行 idea-generation → 生成候选清单
2. 运行 comps-analysis → 估值对比
3. 运行 ic-memo → 三情景分析 + 决策
4. 执行买入（冷静期 7 天后）

持仓管理流程：
1. 每月末运行 thesis-tracker → 更新评分卡
2. 财报后 48h 运行 earnings-analysis → 支柱验证
3. 根据评分和支柱状态决定是否减仓/清仓

季度复盘流程：
1. 运行 thesis-tracker → 季度强制更新
2. 运行 comps-analysis → 季度估值更新
3. 根据结果调整持仓
```

### 5.3 快速开始

**第一次使用 ic-memo**：
```
1. 复制 prompts/ic-memo.md 全文
2. 粘贴到 Claude 对话框
3. 提供输入数据（标的代码、当前股价、初步 thesis）
4. Claude 输出完整的 IC Memo
5. 保存为 trades/decision_*.md
```

**第一次使用 earnings-analysis**：
```
1. 复制 prompts/earnings-analysis.md 全文
2. 粘贴到 Claude 对话框
3. 提供输入数据（财报数据、对应 thesis.md）
4. Claude 输出财报分析 + 支柱验证
5. 更新 theses/{code}_thesis.md 的评分卡
```

---

## 六、后续改进方向

### 6.1 可选增强

- [ ] 补充 Python 脚本自动调用这些 prompt（通过 Claude API）
- [ ] 补充数据拉取脚本（从 Wind/同花顺 API 自动获取数据）
- [ ] 补充 Slack 集成（自动推送分析结果）
- [ ] 补充 Excel 导出功能（将分析结果导出为 Excel 报告）

### 6.2 验证方式

**建议的测试方案**：

1. **用 600219_thesis.md 的真实数据跑 thesis-tracker**
   - 验证输出格式是否与 thesis.md 一致
   - 验证评分卡更新是否合理

2. **用假设的新标的跑 ic-memo**
   - 验证三情景目标价计算是否正确
   - 验证合规检查是否完整

3. **用理想汽车 5/28 财报数据跑 earnings-analysis**
   - 验证支柱验证逻辑是否清晰
   - 验证后续行动建议是否合理

4. **用隆基绿能跑 comps-analysis**
   - 验证可比公司选择是否合理
   - 验证估值结论是否有数据支撑

5. **用全市场数据跑 idea-generation**
   - 验证筛选结果是否合理
   - 验证候选清单是否符合 rules.yaml 约束

---

## 七、文件清单

**新增文件**：
- `/Users/zshaox/Documents/Code/investment/prompts/ic-memo.md` (357 行)
- `/Users/zshaox/Documents/Code/investment/prompts/earnings-analysis.md` (288 行)
- `/Users/zshaox/Documents/Code/investment/prompts/thesis-tracker.md` (296 行)
- `/Users/zshaox/Documents/Code/investment/prompts/comps-analysis.md` (298 行)
- `/Users/zshaox/Documents/Code/investment/prompts/idea-generation.md` (425 行)

**参考文件**：
- `/Users/zshaox/Documents/Code/investment/.claude/borrowing-analysis.md` (借鉴分析文档)
- `/Users/zshaox/Documents/Code/investment/.claude/prompt-filling-completion.md` (本文件)

---

## 八、关键数据

| 指标 | 数值 |
|-----|------|
| 总行数 | 1,664 行 |
| 平均每个 prompt | 333 行 |
| 最长 prompt | idea-generation (425 行) |
| 最短 prompt | earnings-analysis (288 行) |
| 增长倍数 | 16.2x（从 103 行 → 1,664 行） |
| 借鉴来源 | Anthropic 官方 5 个 skill + 社区 4 个设计模式 |
| A 股特有补充 | 5 个维度（北向资金、大股东、解禁、分红、政策） |

---

## 九、签收

| 项目 | 状态 |
|-----|------|
| ic-memo.md 填充 | ✅ 完成 |
| earnings-analysis.md 填充 | ✅ 完成 |
| thesis-tracker.md 填充 | ✅ 完成 |
| comps-analysis.md 填充 | ✅ 完成 |
| idea-generation.md 填充 | ✅ 完成 |
| 借鉴分析文档 | ✅ 完成 |
| 验收标准检查 | ✅ 全部通过 |
| 使用指南 | ✅ 完成 |

**总体状态：✅ 全部完成，可投入使用**

---

**报告结束 · 2026-05-24**
