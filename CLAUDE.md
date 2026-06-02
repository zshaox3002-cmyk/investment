# Investment Portfolio — Claude Code Workflow

## 环境

```bash
# 项目已配置 .envrc（direnv 自动加载），或手动执行：
export INV=.venv/bin/inv
export PY=.venv/bin/python
# DB 路径
DB=data/portfolio.db
```

- 执行任何 CLI 命令一律用 `$INV`（即 `.venv/bin/inv`），禁止裸 `inv` 或 `python -m investment`
- 若 `$INV` 未展开（shell 中变量为空），自动 fallback：直接用 `.venv/bin/inv` 替代 `$INV`
- DB schema 详见 `docs/db_schema.md`（查询前先查表结构，避免盲写 SQL）
- holdings/quotes/theses 等表用 `instrument_id` 做外键，查某只股票需先 JOIN `instruments` 获取 id

### 数据获取策略（优先级从高到低）

1. **`$INV snapshot pull`**（腾讯行情源，稳定）→ 获取所有持仓标的最新价
2. **DB 直查** `quotes` 表 → 已有历史行情，无需联网
3. **WebSearch** → 财务数据（PE/PB/ROE/EPS/营收/净利润），1 次搜索通常覆盖所有关键指标
4. **`akshare` Python 包** → 仅在前 3 种方式都失败时使用（连接不稳定，易超时）

> 关键原则：akshare 失败不重试，直接用 WebSearch 兜底。财务数据（年报/季报）优先从财经网站搜索结果获取，比 akshare 更可靠。

## 模块索引

收到用户指令时，按关键词匹配模块，直接跳到对应章节执行：

| 关键词 | 模块 |
|--------|------|
| 盘后、行情、快照、snapshot、dashboard、战情室、DASHBOARD | [模块1](#模块1-每日盘后) |
| 交易、买入、卖出、建仓、减仓、清仓、decision、trade、止损、止盈、apply、log、记录、误操作 | [模块2](#模块2-交易操作) |
| thesis、论点、评分、stale、过期、sync、持仓、holdings | [模块3](#模块3-持仓与论点) |
| 候选、candidate、scan、扫描、选股、promote、refresh | [模块4](#模块4-候选池) |
| 复盘、review、错误、stats、归因 | [模块5](#模块5-复盘) |
| 因果、causal、节点、边、图谱、信号、impact chain、discover、assess、daily | [模块6](#模块6-因果推理) |
| 迁移、migrate、对账、verify、rollback、tables、导出、data | [模块7](#模块7-数据维护) |
| 测试、pytest、安装、venv、版本、version、cov | [模块8](#模块8-环境与测试) |
| IC Memo、买入分析、建仓分析、投资决策、三情景、目标价 | [模块9](#模块9-研究分析类-skills) |
| 估值对比、同业比较、comps、估值分析、可比公司 | [模块9](#模块9-研究分析类-skills) |
| 论点跟踪、月度评分、评分卡、thesis跟踪、支柱状态 | [模块9](#模块9-研究分析类-skills) |
| 财报分析、业绩解读、earnings、季报、年报 | [模块9](#模块9-研究分析类-skills) |
| 候选池刷新、量化筛选、系统扫描、新机会 | [模块9](#模块9-研究分析类-skills) |
| 复习、概念、笔记、notes、知识、学习笔记、是什么意思 | [模块10](#模块10-知识归档) |

## 项目定位

全职散户 AI Skills 驱动投资系统，目标年化 10%+。A/B/C 三档：生活保障金(25%) + 核心 ETF(50%) + 主动选股(25%)。当前版本 v2（SQLite + Typer CLI）。

## 目录结构

```
src/investment/  # v2 核心包（CLI + 业务层 + 数据层）
config/          # 规则与目标 yaml（只放规则，不放状态）
theses/          # 每只股票投资论点 thesis.md
prompts/         # 5 个 Skills 模板
scripts/         # v1 脚本（保留 ≥90 天作对账兜底）
reviews/         # 日/周/月/季度报告
alerts/          # 触发告警归档（md + DB 同步）
trades/          # 交易决策与复盘（md + DB 同步）
data/            # portfolio.db + 行情缓存
docs/            # architecture.md 等文档
knowledge/       # 学习笔记（边用边学，概念解释归档）
tests/           # pytest 单元测试
```

## 重要约束

- `config/*.yaml` 只放规则与目标，状态全部入 DB
- 所有 thesis 放在 `theses/` 目录，按股票代码命名
- 所有交易决策写入 `trades/` 目录（md 正文）+ DB（元数据）
- `DASHBOARD.html` 由 `$INV dashboard render` 生成，不手动编辑
- 持仓更新必须走 `$INV trade apply`，不直接改 holdings 表

---

## 模块1: 每日盘后

```bash
$INV snapshot pull                      # 拉行情 → 写 quotes/holdings/alerts → 风控检查
$INV snapshot show [DATE]               # 查看某日日报告
$INV dashboard render --mode post-market  # 生成盘后战情室（默认）
$INV dashboard render --mode pre-market   # 生成盘前战情室
```

**盘后标准流程：**
```
$INV snapshot pull → $INV dashboard render --mode post-market → open DASHBOARD.html
```

**盘前速查：**
```
$INV dashboard render --mode pre-market → open DASHBOARD.html
```

---

## 模块2: 交易操作

### 单命令

```bash
$INV trade decision CODE --type NEW|ADD|REDUCE|EXIT|REBALANCE|EMERGENCY [--notes "..."] [--ic-memo]
$INV trade list [--status active|all|executed|cancelled]
$INV trade log CODE -s SHARES -p PRICE --side BUY|SELL [-d decision_NNN] [--fees 0] [--notes "..."] [--date YYYY-MM-DD]
$INV trade apply TRADE_ID               # 成交后反向更新持仓（shares + 加权均价）
$INV trade stop CODE -d decision_NNN --type GRID_SELL|GRID_BUY|STOP_LOSS|TAKE_PROFIT|HARD_DD --trigger-kind PRICE_ABS -v TRIGGER_VALUE -a ACTION [--shares N] [--priority 100]
$INV exec monitor                       # 检查已 arm 的止损止盈规则是否触发
```

### 冷静期规则

| 操作 | 冷静期 | 豁免条件 |
|------|--------|---------|
| 新建仓 | 7 天 | 无 |
| 卖出/减仓 | 3 天 | 强制减仓豁免 |
| 补仓 | 5 天 | 无 |

### 买入工作流

```
用自然语言触发（如"帮我给 600519 做一份买入分析"）→ IC Memo 分析
→ $INV trade decision CODE --type NEW --ic-memo -n "理由"
→ 等 7 天冷静期
→ $INV trade log CODE -s N -p PRICE --side BUY -d decision_NNN
→ $INV trade apply TRADE_ID
```

初始仓位 ≤ C 档 5%，资金路由见 `config/capital.yaml`。

### 卖出工作流

```
$INV trade decision CODE --type REDUCE -n "理由"
        → 等 3 天冷静期（强制减仓豁免）
        → $INV trade log CODE -s N -p PRICE --side SELL -d decision_NNN
        → $INV trade apply TRADE_ID
```

单笔 ≤ 仓位 40%。资金路由：30%→A档 / 70%→B档。

### 止损止盈

```bash
$INV trade stop CODE -d decision_NNN --type STOP_LOSS --trigger-kind PRICE_ABS -v 止损价 -a SELL
$INV trade stop CODE -d decision_NNN --type TAKE_PROFIT --trigger-kind PRICE_ABS -v 止盈价 -a SELL
$INV exec monitor                       # 每个交易日盘后检查触发
```

### 误操作处理

当出现高买低卖等误操作时：
1. 如实记录交易（`$INV trade log`，notes 标注"误操作"+"原因"）
2. 不复原、不删除记录
3. 在当日/周复盘中归因（`$INV review log`，error_code 用 `TIMING_ERROR` 或自定义）

---

## 模块3: 持仓与论点

```bash
$INV thesis sync                        # 同步 theses/*.md frontmatter → DB
$INV thesis list                        # 列出所有论点及评分
$INV thesis score CODE --score N [--dimension D] [--rationale "..."]  # 写入月度评分
$INV thesis stale [--days 30]           # 列出超期未更新的论点
```

**月度评分工作流：**
```
$INV thesis sync → $INV thesis stale → 逐只 $INV thesis score CODE --score N
```

---

## 模块4: 候选池

```bash
$INV candidate scan [--quick] [--codes "A,B"]   # 扫描候选（--quick 用 akshare）
$INV candidate list                              # 查看候选池
$INV candidate refresh [--codes "A,B"] [--delay N]  # 刷新 PE/市值/市净率
$INV candidate promote ID                        # 晋升为 IC Memo 研究对象
```

---

## 模块5: 复盘

```bash
$INV review log --trade-id N [--error-code CODE] [--notes "..."]   # 记录单笔复盘
$INV review stats [--months N]                   # 错误归因频次统计
```

**错误码（常用）：**
`TIMING_ERROR`（时机错误）、`THESIS_BREAK`（论点失效）、`OVERSIZE`（超仓）、`PANIC_SELL`（恐慌卖出）、`FOMO_BUY`（追高）、`NO_STOP_LOSS`（未设止损）

---

## 模块6: 因果推理

### 核心命令

```bash
$INV causal daily [--dry-run]            # 每日一键：scan → lifecycle → assess
$INV causal scan [--dry-run]             # 新闻抓取 → 去重 → LLM 分类 → 写信号表
$INV causal assess [--code CODE] [--explain]  # 评估今日信号对持仓的因果影响（L3+ 写 DB）
$INV causal discover --code CODE --event "事件描述"  # LLM 自动发现因果路径
$INV causal discover-auto                # 自动扫描波动持仓并发现路径
$INV causal graph --code CODE [--format mermaid|json] [--hops N]  # 可视化因果子图
```

### 图谱构建

```bash
$INV causal node add --name "名称" --type MACRO|SECTOR|COMPANY|EVENT|SENTIMENT|INDICATOR --layer L1_macro|L2_sector|L3_holding|L4_indicator
$INV causal node list [--layer L3_holding]
$INV causal node show ID

$INV causal edge add --from SRC_NODE --to TGT_NODE --direction POSITIVE|NEGATIVE|BIDIRECTIONAL --d1 3 --d2 4 --d3 2 --d4 5 --d5 3
$INV causal edge list [--layer L3_holding]
$INV causal edge show ID
```

### 审查（pending edges 审批）

```bash
$INV causal review list                  # 查看待审批边
$INV causal review approve ID            # 批准
$INV causal review reject ID --reason "理由"  # 拒绝
$INV causal review modify ID --d1 N [--d2 N] [--d3 N] [--d4 N] [--d5 N]  # 修改评分后批准
$INV causal review                       # 交互式逐条审批
```

### 生命周期

```bash
$INV causal lifecycle update             # 应用衰减 + 状态迁移（active→dormant→archived）
$INV causal lifecycle review [--days 90] # 查看生命周期变化
```

### 因果推理 5 步上手

```
1. 建节点: $INV causal node add ...
2. 建边:   $INV causal edge add ...  (或用 $INV causal discover 自动)
3. 扫描:   $INV causal scan
4. 审批:   $INV causal review list → approve/reject
5. 评估:   $INV causal assess --explain
```

每日一键：`$INV causal daily`

---

## 模块9: 研究分析类 Skills

> 以下 5 个 Skill 通过自然语言意图路由触发。所有 Skill 定义在 `prompts/skills/10-14_*.md`。

### Skill ⑩ — 买入决策备忘录（IC Memo）

```bash
# 自然语言触发示例：
# "帮我给 600519 做一份买入分析"
# "这只股票能不能买"
# "帮我评估一下这笔投资的回报和风险"

# 底层 CLI:
$INV thesis sync                        # 同步 thesis 状态
$INV trade decision CODE --type NEW --ic-memo -n "理由"
```

### Skill ⑪ — 估值横向对比

```bash
# 自然语言触发示例：
# "帮我对比一下这几只股票的估值"
# "600519 现在的估值和同行比怎么样"
# "这只股票便宜还是贵"

# 底层 CLI:
$INV snapshot pull                      # 拉取估值数据
$INV candidate scan --quick             # 扫描同行业可比公司
```

### Skill ⑫ — 持仓论点跟踪

```bash
# 自然语言触发示例：
# "帮我更新一下持仓评分卡"
# "检查一下我的持仓论点还成立吗"
# "这个月的 thesis 评分该更新了"

# 底层 CLI:
$INV thesis sync                        # 同步 thesis → DB
$INV thesis stale --days 30             # 检查过期论点
$INV thesis score CODE --score N --dimension D --rationale "..."  # 月度评分
```

### Skill ⑬ — 财报解读

```bash
# 自然语言触发示例：
# "600519 发财报了，帮我分析一下"
# "这份季报怎么样，对持仓有什么影响"

# 底层 CLI:
$INV snapshot pull                      # 财报后股价反应
$INV thesis sync                        # 获取当前支柱状态
$INV thesis score CODE --score N --dimension fundamentals --rationale "..."
```

### Skill ⑭ — 月度候选池扫描

```bash
# 自然语言触发示例：
# "帮我扫描一下候选池，看看有没有新机会"
# "这个月的候选标的扫描该做了"

# 底层 CLI:
$INV candidate scan --quick             # 全市场扫描
$INV candidate refresh                  # 刷新 PE/市值
$INV candidate list                     # 查看候选
$INV candidate promote ID               # 晋升为 IC Memo 研究对象
```

---

## 模块7: 数据维护

```bash
$INV migrate run                         # 执行迁移脚本（幂等）
$INV migrate verify                      # 5 项数据对账 → diff_report.md
$INV migrate rollback                    # 删除 DB（危险！需确认）
$INV data tables                         # 列出 DB 表与视图
```

---

## 模块8: 环境与测试

```bash
$INV version                             # 显示版本和 DB 路径
$PY -m pytest -q                         # 42 个单元测试
$PY -m pytest -q --cov=src/investment    # 带覆盖率
$PY -m pytest tests/ -k PATTERN          # 按模式筛选
pip install -e ".[dev]"                  # 首次安装（在 venv 内）
```

---

## 模块10: 知识归档

### 自动归档规则（最高优先级）

> ⚠️ **每当你向用户解释一个新投资概念（如"加权期望回报"、"戴维斯双杀"、"PE 估值"等），解释完毕后必须立即运行以下命令归档：**

```bash
$INV notes append --concept "概念名" --explanation "通俗解释（用户视角，1-3句话）" --example "实际案例（可从当前对话中提取）" --summary "一句话总结"
```

- 去重已内置，不会重复记录同名概念
- 用户追问时才解释概念，解释完就自动归档，无需用户额外指令
- 归档后无需向用户确认（静默完成）

### 复习/查询

```bash
$INV notes search "关键词"   # 搜索已归档概念
$INV notes read              # 查看全部笔记
```

### 用户触发示例

> "复习一下我学过的内容" → 运行 `$INV notes read` 展示全部笔记
> "什么是 PE" → 解释完自动 `$INV notes append`，不回显确认

---

## 14 个 Skills（自然语言触发，无需手动 `/skill-name`）

### 操作执行类（5 个）
| 编号 | Skill ID | 名称 | 优先级 | 触发时机 |
|------|----------|------|--------|---------|
| ① | `onboarding` | 目标与资产录入 | P0 | 首次使用 / 重置目标 |
| ② | `position` | 仓位管理与再平衡巡检 | P0 | 每日盘后 / 主动查询 |
| ③ | `stock_screen` | 对话式选股 | P2 | 即时选股查询 |
| ⑤ | `calendar` | 投资日历与催办 | P1 | 任务管理 / 提醒 |
| ⑦ | `cost` | 交易成本计算 | P2 | 买卖前估算 |

### 风险监控类（2 个）
| 编号 | Skill ID | 名称 | 优先级 | 触发时机 |
|------|----------|------|--------|---------|
| ⑥ | `risk` | 组合风险量化 | P1 | 风险评估 / 月度复盘 |
| ⑨ | `behavior` | 行为约束与决策日志 | P2 | 决策前检查 / 复盘 |

### 分析归因类（2 个）
| 编号 | Skill ID | 名称 | 优先级 | 触发时机 |
|------|----------|------|--------|---------|
| ④ | `causal_insight` | 外部信息与因果归因 | P2 | 异动解释 / 新闻解读 |
| ⑧ | `attribution` | 业绩归因 | P1 | 复盘 / 季度总结 |

### 研究分析类（5 个）
| 编号 | Skill ID | 名称 | 优先级 | 触发时机 |
|------|----------|------|--------|---------|
| ⑩ | `ic_memo` | 买入决策备忘录 | P1 | 新建仓前（硬性前置） |
| ⑪ | `comps_analysis` | 估值横向对比 | P1 | 买入前 / 每季度 |
| ⑫ | `thesis_tracker` | 持仓论点跟踪 | P1 | 每月末 / 财报后 / 回撤≥15% |
| ⑬ | `earnings_analysis` | 财报解读 | P1 | 财报发布后 48h |
| ⑭ | `idea_generation` | 月度候选池扫描 | P2 | 每月末 |

> 用户直接用自然语言触发（如"帮我分析一下这只股票能不能买"→⑩，"对比一下估值"→⑪），无需手动敲 `/skill-name`。系统通过意图路由自动匹配最合适的 Skill。路由规则详见 `prompts/_intent_router.md`。

## 四道纪律护栏

1. 交易冷静期 — 买入 7 天 / 卖出 3 天 / 补仓 5 天
2. 买入先跑 IC Memo（说"帮我分析一下XX能不能买"，系统自动触发 Skill ⑩）
3. 单股回撤 15% 强制审查
4. 账户回撤 -20% 强制降仓

## Bash 执行规范

执行任何 Bash 命令时：
1. 耗时命令加 `timeout 60s`（或合适时长）前缀
2. 不用 `| tail -N` 隐藏输出；需截断时用 `tee /tmp/cmd.log` 保存完整日志
3. 命令结束后报告：退出码、耗时、最后 20 行日志
4. 命令超 60s 无输出，先停止并说明可能卡在哪里
5. 可能交互的命令优先用非交互参数（`-y`、`--non-interactive`），或提前说明需要用户输入
