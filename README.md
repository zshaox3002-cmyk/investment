# AI 驱动投资管理系统

个人投资管理框架，目标年化 10%+。以 SQLite 为唯一真相源，`inv` CLI 为统一入口，AI Skills 辅助决策，四道风控铁律约束行为。

## 快速开始

```bash
pip install -e ".[dev]"
inv migrate run          # 初始化 DB（幂等）
inv snapshot pull        # 拉取日终行情
inv dashboard render && open DASHBOARD.html  # 查看战情室
```

**环境要求**：Python 3.11+，需能访问 qt.gtimg.cn（腾讯财经 API）。

## 项目结构

```
src/investment/   # v2 核心包（CLI + 业务层 + 数据层）
config/           # 规则与目标（rules.yaml / capital.yaml / screening_rules.yaml）
theses/           # 持仓投资论点 thesis.md
prompts/          # 5 个 AI Skills 提示词模板
reviews/          # 日/周/月/季度报告
trades/           # 交易决策与复盘
data/             # portfolio.db + 行情缓存
docs/             # 白皮书 + 架构文档 + 因果推理快速上手
tests/            # pytest 单元测试（181 个）
```

## CLI 命令

### 每日盘后

```bash
inv snapshot pull                     # 拉行情 → 写入 holdings/quotes/alerts
inv dashboard render                  # 生成 DASHBOARD.html
inv exec monitor                      # 检查止损止盈触发
```

### 因果推理（v2.1）

```bash
inv causal daily                      # 一键流水线：scan → lifecycle → assess

# 图谱构建
inv causal node add --name --type --layer
inv causal node list [--layer] [--state] [--type]
inv causal edge add --from --to --d1..--d5 [--direction] [--lag]

# 信号扫描
inv causal scan [--date] [--dry-run]

# 路径发现
inv causal discover --code --event
inv causal discover-auto [--volatility] [--lookback]

# 人工审批
inv causal review                    # 交互式审批
inv causal review list / approve / reject / modify

# 影响评估
inv causal assess [--date] [--code] [--explain]

# 可视化
inv causal graph --code [--format mermaid] [--hops]

# 生命周期
inv causal lifecycle update
inv causal lifecycle review [--days]
```

### 交易操作

```bash
inv trade decision new --code --type   # 创建决策（进入冷静期）
inv trade decision list                # 查看冷静期状态
inv trade log --shares X --price Y     # 记录成交
inv trade apply ID                     # 反向更新持仓
inv trade stop add / list              # 止损止盈规则
```

### 研究与复盘

```bash
inv thesis sync / score / stale        # 论点管理
inv candidate scan --quick             # 候选池扫描
inv candidate list / promote           # 候选池管理
inv review log / stats                 # 交易复盘与错误归因
```

### 数据与迁移

```bash
inv migrate run                        # 执行迁移脚本（幂等）
inv migrate verify                     # 5 项数据对账
inv data tables                        # 列出 DB 所有表
inv data export csv                    # DB → CSV 灾难恢复
```

## 数据库

单文件 SQLite：`data/portfolio.db`，包含 25 张表和 3 个视图。核心表：

| 表 | 用途 |
|----|------|
| `instruments` / `holdings` / `quotes` | 标的、持仓、行情 |
| `decisions` / `trades` / `stop_rules` | 交易决策、成交、止损 |
| `alerts` / `rule_breaches` | 告警、违规归档 |
| `theses` / `thesis_scores` / `candidates` | 论点、评分、候选池 |
| `causal_nodes` / `causal_edges` / `pending_edges` | 因果图谱 |
| `news_signals` / `node_activation_log` / `chain_assessments` | 因果信号与评估 |

## AI Skills

| Skill | 触发时机 |
|-------|----------|
| `thesis-tracker` | 每月末 / 财报后 / 回撤 ≥15% |
| `comps-analysis` | 买入前 / 每季度 |
| `earnings-analysis` | 财报发布后 48h |
| `ic-memo` | 新建仓前（硬性要求） |
| `idea-generation` | 每月扫候选池 |

## 风控铁律

1. **交易冷静期 24h**：新建仓 7 天，卖出 3 天，补仓 5 天
2. **买入先跑 IC Memo**：未跑 ic-memo 不得建仓
3. **单股回撤 15% 强制审查**：7 天内给出持有/减仓/清仓决策
4. **账户回撤 -20% 强制降仓**：停止新建仓 60 天，主动仓位降至 ≤25%

详见 `config/rules.yaml` 和 `docs/whitepaper.html`。

## 开发

```bash
pip install -e ".[dev]"
pytest -q                              # 运行全部 181 个测试
pytest tests/causal/ -q                # 因果模块 102 个测试
```

## 文档

- [使用白皮书](docs/whitepaper.html) — 完整系统文档
- [架构说明](docs/architecture.md) — 技术架构与设计决策
- [因果推理快速上手](docs/causal_quickstart.md) — 5 步跑通因果推理
