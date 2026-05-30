# Investment Portfolio — Claude Code Workflow

## 环境

```bash
INV=.venv/bin/inv          # 所有 CLI 命令入口（必须用 venv 内路径）
DB=data/portfolio.db        # SQLite 数据库
PY=.venv/bin/python         # Python 3.11
```

执行任何 `inv` 命令一律用 `$INV`，禁止使用裸 `inv` 或 `python -m investment`。

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
/ic-memo → $INV trade decision CODE --type NEW --ic-memo -n "理由"
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

## 5 个 Skills（手动调用 `/skill-name`）

| Skill | 触发时机 | 说明 |
|-------|---------|------|
| thesis-tracker | 每月末/财报后/回撤 ≥15% | 持仓论点跟踪与月度评分卡 |
| comps-analysis | 买入前/每季度 | 估值横向对比 |
| earnings-analysis | 财报发布后 48h | 财报解读 |
| ic-memo | 新建仓前（硬性） | 买入决策备忘录 |
| idea-generation | 每月 | 候选池扫描与创意生成 |

## 四道纪律护栏

1. 交易冷静期 — 买入 7 天 / 卖出 3 天 / 补仓 5 天
2. 买入先跑 IC Memo（`/ic-memo`）
3. 单股回撤 15% 强制审查
4. 账户回撤 -20% 强制降仓

## Bash 执行规范

执行任何 Bash 命令时：
1. 耗时命令加 `timeout 60s`（或合适时长）前缀
2. 不用 `| tail -N` 隐藏输出；需截断时用 `tee /tmp/cmd.log` 保存完整日志
3. 命令结束后报告：退出码、耗时、最后 20 行日志
4. 命令超 60s 无输出，先停止并说明可能卡在哪里
5. 可能交互的命令优先用非交互参数（`-y`、`--non-interactive`），或提前说明需要用户输入
