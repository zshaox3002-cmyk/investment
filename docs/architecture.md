# Architecture — Investment Portfolio System v2

## 概览

三层薄壳架构：CLI 入口 → 业务层 → SQLite 数据层。

```
┌─────────────────────────────────────────────────────────────┐
│  入口层   inv CLI (Typer)  +  DASHBOARD.html (静态渲染)      │
├─────────────────────────────────────────────────────────────┤
│  业务层   workflow/  rules/  pricing/  reports/  skills/    │
├─────────────────────────────────────────────────────────────┤
│  数据层   data/portfolio.db (18 表 + 3 视图)                 │
│          config/*.yaml 仅放规则与目标，不再放状态            │
└─────────────────────────────────────────────────────────────┘
```

## 目录结构

```
src/investment/
├── cli/
│   └── __main__.py       # Typer app，所有 inv 子命令入口
├── core/
│   ├── db.py             # connect() / transaction() / init_db()
│   ├── schema.sql        # 18 张表 + 3 个视图的 DDL
│   ├── settings.py       # 路径常量（ROOT_DIR / DB_PATH / ...）
│   └── exceptions.py     # DBError 等自定义异常
├── data/
│   └── repository/       # 每表一个 CRUD 模块（待填充）
├── migration/
│   ├── _01_seed_instruments.py   # 从 holdings.csv 导入标的主表
│   ├── _02_load_current_state.py # 持仓 + 现金余额
│   ├── _03_load_quotes_history.py
│   ├── _04_parse_theses.py
│   ├── _05_parse_trades_decisions.py
│   ├── _06_load_alerts.py
│   ├── _07_load_executions.py
│   ├── _08_load_breaches.py
│   ├── runner.py         # inv migrate run — 顺序执行 01-08
│   ├── verify.py         # inv migrate verify — 5 项数据对账
│   └── utils.py          # parse_frontmatter / instrument_id_by_code / log_failure
├── pricing/
│   ├── tencent.py        # 腾讯行情 API 封装（_parse_tencent_line / fetch_prices_batch）
│   └── cache.py          # 写入 quotes 表，避免重复拉取
├── rules/
│   ├── loader.py         # load_rules() / load_capital()
│   └── checker.py        # 8 个 check_* 函数 + run_all_checks() + write_alerts()
├── reports/
│   └── dashboard.py      # inv dashboard render → DASHBOARD.html
├── skills/
│   └── __init__.py       # context_builder（DB → Skill prompt，待实现）
└── workflow/
    ├── snapshot.py       # inv snapshot pull — 抓行情 + 写 holdings/quotes/alerts
    ├── trade.py          # inv trade decision/log/stop/apply
    ├── thesis.py         # inv thesis sync/score/stale
    ├── candidate.py      # inv candidate scan/list/promote
    └── review.py         # inv review log/stats
```

## 数据层

### 核心表（18 张）

| 表 | 用途 |
|---|---|
| `instruments` | 标的主表（A/H/ETF/RSU/Cash），主键 (code, market) |
| `instrument_themes` | 一对多主题分类 |
| `holdings` | 持仓时序（每日 effective_date） |
| `cash_balances` | 现金/债券/RSU |
| `quotes` | 行情时序（替代 portfolio_ts.csv） |
| `etf_targets` | ETF 终态目标占比 |
| `executions` | 建仓执行计划 |
| `theses` | thesis 元信息 |
| `thesis_scores` | 五支柱评分历史 |
| `decisions` | 决策文档元数据 |
| `stop_rules` | 止损止盈/网格规则 |
| `trades` | 实际成交记录 |
| `alerts` | 告警事件流（去重：date+type+code） |
| `candidates` | 选股候选池 |
| `trade_reviews` | 交易复盘 |
| `review_errors` | 错误归因（12 种固定 enum） |
| `rule_breaches` | 风控违规归档 |
| `schema_version` | DB 版本管理 |

### 3 个核心视图

| 视图 | 用途 |
|---|---|
| `v_portfolio_snapshot` | 当前持仓 + 最新行情，单一计算口径 |
| `v_compliance_status` | 每条违规的当前比例 vs 阈值 |
| `v_pending_executions` | 待执行清单（dashboard 直接消费） |

### 设计原则

- `config/*.yaml` 只放**规则与目标**，不放状态
- 状态全部入 DB；markdown 文件保留正文（人类阅读），frontmatter 由 CLI 反向生成
- 告警去重：`(alert_date, alert_type, instrument_id)` 唯一约束

## 业务层关键模块

### rules/checker.py

8 个独立的 `check_*` 函数，全部是纯函数（除 `check_stop_rules` 需要 DB 连接）：

```
check_stock_drawdown()      # L1/L2/L3 三档回撤
check_stock_position()      # 单股仓位上限
check_account_drawdown()    # 账户总回撤
check_theme_concentration() # 主题集中度
check_etf_drawdown()        # ETF 回撤
check_etf_drift()           # ETF 偏离目标
check_meituan_rsu()         # 美团 RSU 市值变动
check_stop_rules()          # 止损止盈触发（需 DB conn）
```

`run_all_checks()` 聚合调用，`write_alerts()` 持久化到 DB（自动去重）。

### pricing/tencent.py

封装腾讯 `qt.gtimg.cn` API：
- `_tencent_code(code, market)` — 转换为腾讯格式（sh/sz/hk 前缀）
- `_parse_tencent_line(line)` — 解析单行响应，返回 `(code, quote_dict)`
- `fetch_prices_batch(items)` — 批量拉取，返回 `{code: quote | None}`

### workflow/trade.py

`inv trade` 命令链：
1. `new_decision()` — 创建决策记录 + stub markdown
2. `log_trade()` — 记录成交，写入 trades 表
3. `add_stop_rule()` — 添加止损止盈规则
4. `apply_trade()` — 成交反向更新 holdings

## CLI 命令树

```
inv version
inv migrate run / verify / rollback
inv snapshot pull
inv dashboard render
inv trade decision new/list/show
inv trade log
inv trade stop add/list
inv trade apply
inv exec monitor
inv thesis sync / score / stale
inv candidate scan / list / promote
inv review log / stats
inv data tables / export csv
```

## 配置文件

| 文件 | 用途 |
|---|---|
| `config/rules.yaml` | 风控铁律（止损阈值、仓位上限、主题集中度） |
| `config/capital.yaml` | 资金结构（A/B/C 档目标比例、美团 RSU） |
| `config/screening_rules.yaml` | 选股筛选规则（universe / hard_filters / soft_filters） |
| `config/execution_tracker.yaml` | 待执行事项（dashboard 消费，v1 遗留，逐步迁移到 DB） |

## 测试

```
tests/
├── unit/
│   ├── test_checker.py          # rules/checker.py 全分支（20 个用例）
│   ├── test_tencent.py          # pricing/tencent.py 解析器（11 个用例）
│   ├── test_utils.py            # migration/utils.py frontmatter（5 个用例）
│   └── test_db_and_stop_rules.py # init_db 幂等性 + stop_rules 触发（6 个用例）
├── integration/                 # 待填充
└── fixtures/                    # 待填充
```

运行：`pytest -q`（全 42 个用例，核心模块覆盖率 65%+）

## 数据流：每日盘后

```
inv snapshot pull
  └─ fetch_prices_batch()        # 腾讯 API 拉行情
  └─ 写 quotes / holdings 表
  └─ run_all_checks()            # 8 个告警检查
  └─ write_alerts()              # 去重写入 alerts 表
  └─ check_stop_rules()          # 止损止盈触发判定

inv dashboard render
  └─ 读 v_portfolio_snapshot     # 持仓 + 行情
  └─ 读 v_pending_executions     # 待执行清单
  └─ 读 v_compliance_status      # 违规状态
  └─ 生成 DASHBOARD.html
```

## v1 → v2 迁移路径

v1 的 `scripts/` 目录保留，与 v2 并行运行直至 D7 验证完成。

迁移脚本：`inv migrate run`（01-08 步，幂等）  
验证：`inv migrate verify`（5 项数据对账，差异 > 0.01% 报错）  
回退：`git checkout legacy/v1-csv-md`
