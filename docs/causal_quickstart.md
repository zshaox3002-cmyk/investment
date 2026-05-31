# 因果推理快速上手

从 0 开始，5 步跑通因果推理全流程。

## 前置条件

```bash
pip install -e ".[dev]"
inv migrate run          # 创建 causal 相关表（迁移 09-13）
```

## 第 1 步：构建因果图谱

手动添加地缘→宏观→行业→持仓的传导链节点和边。

```bash
# 添加节点（L0→L1→L2→L3）
inv causal node add --name "中东-军事冲突" --type event --layer L0_geopolitical
inv causal node add --name "WTI油价" --type macro --layer L1_macro
inv causal node add --name "有色金属-铝" --type sector --layer L2_industry
inv causal node add --name "600219-南山铝业" --type holding --layer L3_holding

# 添加有向边（5 维评分）
inv causal edge add --from "中东-军事冲突" --to "WTI油价" --direction 1 --d1 5 --d2 4 --d3 4 --d4 5 --d5 4
inv causal edge add --from "WTI油价" --to "有色金属-铝" --direction 1 --d1 4 --d2 3 --d3 3 --d4 2 --d5 2
inv causal edge add --from "有色金属-铝" --to "600219-南山铝业" --direction 1 --d1 4 --d2 3 --d3 4 --d4 3 --d5 3
```

也可以让 LLM 自动发现路径（会在图谱已有节点基础上扩展）：

```bash
inv causal discover --code 600219 --event "中国PMI超预期回升"
```

## 第 2 步：运行首次信号扫描

抓取当日新闻，LLM 分类识别哪些匹配图谱节点，写入信号并更新节点激活度。

```bash
inv causal scan
```

输出示例：`fetched=15 deduped=12 classified=8 signals_written=5 nodes_updated=3`

`--dry-run` 模式可预览分类结果而不写入 DB：

```bash
inv causal scan --dry-run
```

## 第 3 步：审批 Pending Edges

LLM 发现的新边以 pending 状态暂存，需人工审批后才能加入图谱。

```bash
# 查看待审批列表
inv causal review list

# 交互式逐条审批（approve/reject/modify/skip/quit）
inv causal review

# 或直接命令行操作
inv causal review approve 1
inv causal review reject 2 --reason "传导链过长，证据不足"
inv causal review modify 3 --d1 4 --d2 3 --lag 2
```

## 第 4 步：评估影响

基于今日信号，搜索所有信号→持仓的传导路径，计算复合影响分数，LLM 生成中文解读。

```bash
# 评估全部持仓
inv causal assess

# 评估单只持仓 + 显示路径详情
inv causal assess --code 600219 --explain
```

只有 L3+（|impact| ≥ 0.15）的结果才会写入 `chain_assessments` 表。

## 第 5 步：在 Dashboard 中查看

```bash
inv dashboard render && open DASHBOARD.html
```

找到页面上"影响链异动"区块，查看：
- 等级颜色编码（L3 蓝 / L4 黄 / L5 红）
- 关键传导链节点序列
- 信号数量和建议动作
- 点击行展开 LLM 详细解读

## 每日一键流程

熟悉后可直接用一条命令完成全部三步：

```bash
inv causal daily
inv dashboard render && open DASHBOARD.html
```

## 进阶操作

```bash
# 可视化持仓的因果子图
inv causal graph --code 600219 --format mermaid --hops 3

# 检查节点生命周期变化（最近 90 天休眠/归档的节点）
inv causal lifecycle review --days 90

# 列出所有 L3 持仓节点
inv causal node list --layer L3_holding

# 查看某条边的 5 维评分
inv causal edge show 1
```

## 关键概念速查

| 概念 | 说明 |
|------|------|
| 节点激活度 | 0–100，信号驱动上升 + 时间衰减，决定节点是否参与传导 |
| 5 维边评分 | d1 直接性 30% + d2 弹性 25% + d3 一致性 25% + d4 速度 10% + d5 独特性 10% |
| 路径影响 | `signal_strength × Π(strength_i/5) × sign × 0.85^(n-1)` |
| 影响等级 | L1 <0.05 / L2 <0.15 / L3 <0.30 / L4 <0.50 / L5 ≥0.50 |
| Pending Edge | LLM 发现但未经人工审批的候选边 |
| 生命周期 | active → dormant(14天无信号) → archived(90天休眠) |
