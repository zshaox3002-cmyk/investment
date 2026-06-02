# Investment System v3 Architecture

生成时间：2026-06-02  
目的：Phase 0 基线确认——记录现有能力边界，为 v3 编排层提供接入地图。

---

## 1. task_calendar 现有字段

表定义位于 `migrations/18_phase7.sql`：

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | INTEGER PK | AUTOINCREMENT | |
| title | TEXT | NOT NULL | 任务标题 |
| category | TEXT | NOT NULL, CHECK | cooldown/earnings/rebalance/monthly/quarterly/annual/custom |
| due_date | TEXT | NOT NULL | ISO date YYYY-MM-DD |
| priority | TEXT | DEFAULT 'medium' | high/medium/low |
| status | TEXT | DEFAULT 'pending' | pending/done/skipped/overdue |
| related_code | TEXT | | 关联股票代码 |
| notes | TEXT | | |
| created_at | TEXT | DEFAULT CURRENT_TIMESTAMP | |
| updated_at | TEXT | DEFAULT CURRENT_TIMESTAMP | |

关联表 `task_log`：记录每条任务的 created/completed/skipped/snoozed/overdue 操作。

v3 Phase 1 将扩展以下列（幂等 ADD COLUMN）：
`source_module`, `source_ref`, `action_type`, `decision_layer`,
`evidence_json`, `blocking_reason`, `suggested_command`, `confidence`

---

## 2. agent_tools 已封装函数

### 2.1 结构化返回类型

所有工具返回 `ToolResult(success, data, human_message, raw_output)` —— 定义在 `agent_tools/base.py`。

例外：`position_monitor` 返回 `PositionReport`，`risk_engine` 返回 `RiskReport`，
`attribution` 返回 `AttributionResult`，`calendar` 返回 `CalendarReport`，
`causal_facade` 返回 `CausalInsightReport`。

### 2.2 各模块入口函数

| 模块文件 | 入口函数 | 返回类型 | 说明 |
|----------|----------|----------|------|
| `snapshot.py` | `snapshot_pull()` | ToolResult | 拉取行情→写 quotes/holdings/alerts |
| `snapshot.py` | `snapshot_show(date)` | ToolResult | 查日报 |
| `position_monitor.py` | `run_position_monitor(as_of, db_path)` | PositionReport | 持仓监控 + 铁则检查 + 再平衡偏离 |
| `risk_engine.py` | `run_risk_engine(lookback_days, calc_date, db_path, save)` | RiskReport | 波动率/VaR/回撤/相关性/伪分散 |
| `attribution.py` | `run_attribution(period_start, period_end, benchmark_code, save, db_path)` | AttributionResult | BHB 业绩归因 |
| `calendar.py` | `run_calendar(period, db_path)` | CalendarReport | 任务日历（逾期/即将到期/待办） |
| `causal_facade.py` | `run_causal_insight(as_of, holding_code, db_path)` | CausalInsightReport | 因果影响评估 |
| `trade.py` | `exec_monitor()` | ToolResult | 检查止损止盈规则触发 |
| `trade.py` | `trade_decision(code, type, notes, ic_memo)` | ToolResult | 创建交易决策 |
| `trade.py` | `trade_log(code, shares, price, side, ...)` | ToolResult | 记录成交 |
| `trade.py` | `trade_apply(trade_id)` | ToolResult | 反向更新持仓 |

### 2.3 PositionReport 关键字段

```python
@dataclass
class PositionReport:
    as_of: str
    total_portfolio_value: float
    holdings: list[HoldingSummary]    # code, name, tranche, pnl_pct, weight_in_tranche
    tranches: list[TrancheSummary]    # tranche, actual_ratio, deviation_text
    alerts: list[HumanAlert]          # severity, message
    rule_breaches: list[RuleBreach]   # rule_name, current_value, threshold, action_required
    rebalance_needed: bool
    human_message: str
    has_profile: bool
```

### 2.4 RiskReport 关键字段

```python
@dataclass
class RiskReport:
    calc_date: str
    lookback_days: int
    instrument_count: int
    portfolio_vol: float        # 年化波动率
    max_drawdown: float         # 最大回撤（负数）
    dd_duration_days: int
    var_95: float               # 1日 VaR
    var_99: float
    sharpe_ratio: float
    instrument_returns: list[InstrumentReturns]
    risk_contributions: dict[str, float]   # code → % of portfolio risk
    correlation_matrix: np.ndarray
    pseudo_diversification: PseudoDiversification
    insufficient_data: bool
    human_message: str
```

### 2.5 AttributionResult 关键字段

```python
@dataclass
class AttributionResult:
    period_start / period_end: str
    benchmark_code / benchmark_name: str
    total_return: float
    benchmark_return: float
    excess_return: float
    timing_contrib / selection_contrib / allocation_contrib: float
    insufficient_data: bool
    human_message: str
```

### 2.6 CalendarReport 关键字段

```python
@dataclass
class CalendarReport:
    as_of: str
    period: str
    overdue: list[CalendarTask]      # 已逾期
    due_soon: list[CalendarTask]     # 3日内到期
    upcoming: list[CalendarTask]     # 周期内其余任务
    human_message: str
```

---

## 3. 视图与关键表字段

### v_portfolio_snapshot（视图）

综合 holdings + quotes + instruments 的当前持仓快照：

| 字段 | 说明 |
|------|------|
| instrument_id | 标的 ID |
| code | 股票代码 |
| market | 市场（A/HK/US） |
| name | 名称 |
| tranche | 档位 A/B/C/D |
| shares | 持股数 |
| cost_price | 成本均价 |
| latest_price | 最新价（来自 quotes） |
| market_value | 当前市值 |
| cost_total | 总成本 |
| pnl | 浮盈浮亏 |
| pnl_pct | 盈亏% |

### rule_breaches（表）

| 字段 | 说明 |
|------|------|
| id | PK |
| rule_path | 规则标识（如 single_stock_max） |
| current_value | 当前值 |
| threshold | 阈值 |
| status | active/resolved/grace |
| grace_until | 宽限期截止日 |
| notes | |

### alerts（表）

| 字段 | 说明 |
|------|------|
| id | PK |
| alert_date | 告警日期 |
| alert_type | 告警类型 |
| severity | critical/warning/info |
| instrument_id | 关联标的（可为空） |
| message | 告警内容 |

### executions（表）

| 字段 | 说明 |
|------|------|
| id | PK |
| plan_name | 执行计划名称 |
| instrument_id | 标的 |
| phase / batch | 阶段/批次 |
| side | BUY/SELL |
| status | pending/in_progress/done/skipped/blocked/expired |
| trigger_type | time/price_abs/price_rel/event/condition |
| trigger_spec | 触发条件详情 |

---

## 4. CLI 命令树（现有）

Typer 多级结构，所有命令通过 `.venv/bin/inv` 调用：

```
inv version
inv migrate run|verify|rollback
inv data tables
inv snapshot pull|show [DATE]
inv dashboard render [--mode post-market|pre-market]
inv thesis sync|list|score|stale
inv trade decision|list|log|apply|stop
inv exec monitor
inv candidate scan|list|refresh|promote
inv review log|stats
inv causal daily|scan|assess|discover|discover-auto|graph
inv causal node add|list|show
inv causal edge add|list|show
inv causal review list|approve|reject|modify
inv causal lifecycle update|review
inv profile ...
inv risk compute|show
inv attribution run|show
inv calendar list|create|complete
inv cost calc|show
inv behavior check|log
inv notes append|search|read
inv goal compute|show         # Phase 4 新增
inv health compute|show       # Phase 4 新增
inv agent run|brief|tasks     # Phase 2 新增
inv dashboard serve           # Phase 5 新增
```

---

## 5. v3 新增模块（Phase 0 骨架）

位于 `src/investment/agent_orchestrator/`：

| 文件 | 职责 |
|------|------|
| `runner.py` | 编排主入口：按顺序调用各模块，聚合结构化结果 |
| `operating_state.py` | 健康灯判定 + 写入 daily_operating_state |
| `task_generator.py` | 各模块输出 → 分层任务 → 写入 task_calendar |
| `prioritizer.py` | 去重 + executable/confirm/monitor/blocked/info 分层排序 |
| `brief.py` | 数据驱动的"今日简报"生成（非 LLM 自由发挥） |

---

## 6. v3 新增表（Phase 1 migration 19）

| 表名 | 主键 | 说明 |
|------|------|------|
| daily_operating_state | state_date | 每日健康灯 + 计数摘要 |
| goal_progress | progress_date | 年度目标追踪（YTD/目标/差距） |
| position_health | calc_date + instrument_id | 持仓健康度综合评分 |
| agent_run_log | id（自增） | Agent 每次运行日志 |

task_calendar 扩展列：
`source_module`, `source_ref`, `action_type`, `decision_layer`,
`evidence_json`, `blocking_reason`, `suggested_command`, `confidence`

---

## 7. 颜色语义（全局固定）

| 颜色 | 含义 |
|------|------|
| 红 | 必须处理 / 违规 / 止损触发 |
| 黄 | 待确认 / 警告 |
| 蓝 | 仅监控 / 信息 |
| 绿 | 可执行 / 已完成 |
| 灰 | 非交易日 / 失效 |
