# DB Schema — Investment Portfolio

> 自动生成：2026-05-31 | 来源：`sqlite3 data/portfolio.db .schema`
> 维护规则：每次 `$INV migrate run` 后更新本文档

## 查询前必读

- `holdings`, `quotes`, `theses`, `trades`, `stop_rules` 等核心表用 `instrument_id` 做外键
- 查某只股票前必须先 JOIN `instruments` 获取 `id`：
  ```sql
  SELECT id FROM instruments WHERE code='600219'
  ```
- 已有便利视图 `v_portfolio_snapshot` 直接返回持仓+行情+盈亏
- 已有便利视图 `v_compliance_status` 直接返回违规状态+紧急度

---

## 核心表（高频使用）

### instruments — 标的字典
| 列 | 类型 | 说明 |
|-----|------|------|
| id | INTEGER PK | 全局唯一 ID |
| code | TEXT | 代码（600219 / 02015 / 513010） |
| market | TEXT | A / HK / US / OTC |
| name | TEXT | 名称 |
| asset_class | TEXT | STOCK / ETF / BOND / CASH / RSU |
| industry | TEXT | 行业 |
| tranche | TEXT | A / B / C / D |
| active | INTEGER | 1=活跃 |

### holdings — 持仓快照（S2 型，每次变动插新行）
| 列 | 类型 | 说明 |
|-----|------|------|
| instrument_id | INTEGER FK | → instruments.id |
| effective_date | TEXT | 生效日期 |
| shares | REAL | 持股数 |
| cost_price | REAL | 加权成本均价 |
| source | TEXT | manual / trade_apply / migration |

> 取最新持仓：`WHERE effective_date = (SELECT MAX(effective_date) FROM holdings h2 WHERE h2.instrument_id = h.instrument_id)`

### quotes — 日行情
| 列 | 类型 | 说明 |
|-----|------|------|
| instrument_id | INTEGER FK | |
| quote_date | TEXT | 行情日期 |
| close | REAL | 收盘价 |
| change_pct | REAL | 涨跌幅（小数） |
| volume | REAL | 成交量（股） |

### theses — 论点元数据
| 列 | 类型 | 说明 |
|-----|------|------|
| instrument_id | INTEGER PK | 一只股票一条记录 |
| current_score | REAL | 综合评分 0-5 |
| rating | TEXT | ⭐ 评级 |
| action | TEXT | 建议动作 |
| body_path | TEXT | thesis.md 路径 |

### thesis_scores — 论点分维度评分
| 列 | 类型 | 说明 |
|-----|------|------|
| instrument_id | INTEGER FK | |
| dimension | TEXT | 维度名称 |
| score | REAL | 0-5 分 |
| trend | TEXT | 趋势 |
| rationale | TEXT | 评分理由 |

### decisions — 交易决策
| 列 | 类型 | 说明 |
|-----|------|------|
| decision_no | TEXT UNIQUE | decision_XXXX |
| decision_type | TEXT | NEW / ADD / REDUCE / EXIT / REBALANCE / EMERGENCY |
| primary_instrument_id | INTEGER FK | |
| ic_memo_passed | INTEGER | 0/1 |
| status | TEXT | draft / active / executed / cancelled / superseded |
| rules_breach_ref | TEXT | 违规引用 |

### trades — 实际成交记录
| 列 | 类型 | 说明 |
|-----|------|------|
| instrument_id | INTEGER FK | |
| trade_date | TEXT | |
| side | TEXT | BUY / SELL |
| shares | REAL | |
| price | REAL | |
| amount | REAL | 成交金额 |
| decision_id | INTEGER FK | |

### stop_rules — 止损止盈规则
| 列 | 类型 | 说明 |
|-----|------|------|
| rule_type | TEXT | STOP_LOSS / TAKE_PROFIT / GRID_BUY / GRID_SELL / HARD_DD |
| trigger_kind | TEXT | PRICE_ABS / PRICE_REL / PNL_PCT |
| trigger_value | REAL | |
| action | TEXT | 触发后动作 |
| status | TEXT | armed / triggered / disarmed / expired |

---

## 便利视图

### v_portfolio_snapshot — 当前持仓一览
返回：`code, name, tranche, shares, cost_price, price, market_value, cost_total, pnl_pct`

直接查，无需 JOIN：
```sql
SELECT * FROM v_portfolio_snapshot WHERE code='600219'
```

### v_compliance_status — 违规状态+紧急度
返回：`rule_path, current_value, threshold, breach_amount, status, urgency, code, name`

urgency 取值：`EXPIRED`（超期）/ `URGENT`（<7天）/ `OK`

---

## 业务表（按需查询）

### candidates — 候选池
直接存 `code` 字段（无需 JOIN instruments），含 `pe_ttm`, `pb`, `roe_3y_avg`, `composite_score`, `status`

### alerts — 告警日志
587 条历史告警，含 `alert_type`, `severity`, `instrument_id`, `message`, `acknowledged`

### rule_breaches — 违规追踪
3 条活跃违规（single_stock_max / theme_concentration / active_position_total），含宽限期

### causal_nodes / causal_edges / pending_edges — 因果图谱
51 个节点 + 62 条边 + 49 条待审批边

### trades — 历史成交
129 条记录，含完整买卖历史

---

## 视图完整列表

| 视图 | 用途 |
|------|------|
| `v_portfolio_snapshot` | 当前持仓+行情+盈亏 |
| `v_compliance_status` | 违规状态+紧急度 |
| `v_pending_executions` | 待执行的分批交易 |
| `v_causal_edges_full` | 因果边（含节点名） |

---

## 更新记录

- 2026-05-31：初版，基于 Phase 7 迁移后 schema（31 张表 + 4 个视图）
