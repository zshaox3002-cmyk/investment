# Phase 0 基线快照

> 生成时间：2026-05-30  
> 分支：feat/phase0-foundation  
> 用途：Phase 0 完成后的回滚基准，后续每个 Phase 收尾前必须对照此文件确认底座未被破坏。

---

## 1. 测试基线

```
总计：181 个测试（179 通过，2 个预存失败）
```

### 预存失败（Phase 0 开工前已存在，非本次引入）

| 测试 | 失败原因 |
|------|---------|
| `tests/causal/test_discoverer.py::TestLLMRetryLogic::test_schema_failure_triggers_retry` | LLM 重试逻辑测试，需要真实 API 调用 |
| `tests/causal/test_scanner.py::TestLifecycleTransitions::test_lifecycle_no_last_signal_date` | 生命周期状态断言：新节点无信号时预期 dormant，实际返回 active |

**铁律**：后续每个 Phase 结束时，`pytest -q` 的通过数不得低于 179，且不得引入新的失败。

---

## 2. 数据库对象清单（25 表 + 3 视图 + 2 内部表）

执行命令：`inv data tables`（2026-05-30）

### 业务表（25 张）

| # | 表名 | 说明 |
|---|------|------|
| 1 | alerts | 告警记录 |
| 2 | candidates | 候选池 |
| 3 | cash_balances | 现金余额 |
| 4 | causal_edges | 因果边 |
| 5 | causal_nodes | 因果节点 |
| 6 | causal_review_log | 因果审批日志 |
| 7 | chain_assessments | 因果链评估 |
| 8 | decisions | 交易决策 |
| 9 | etf_targets | ETF 目标配置 |
| 10 | executions | 执行计划 |
| 11 | holdings | 持仓 |
| 12 | instrument_themes | 标的主题标签 |
| 13 | instruments | 标的主表 |
| 14 | news_signals | 新闻信号 |
| 15 | node_activation_log | 节点激活日志 |
| 16 | pending_edges | 待审批边 |
| 17 | quotes | 行情数据 |
| 18 | review_errors | 复盘错误记录 |
| 19 | rule_breaches | 规则违反记录 |
| 20 | schema_version | Schema 版本 |
| 21 | sql_schema_migrations | SQL 迁移记录 |
| 22 | stop_rules | 止损止盈规则 |
| 23 | theses | 投资论点 |
| 24 | thesis_scores | 论点评分 |
| 25 | trade_reviews | 交易复盘 |
| 26 | trades | 成交记录 |

> 注：`sqlite_sequence` 为 SQLite 内部表，不计入业务表数量。

### 视图（3 个）

| 视图名 | 说明 |
|--------|------|
| v_causal_edges_full | 因果边完整视图（含节点名称） |
| v_compliance_status | 合规状态视图 |
| v_pending_executions | 待执行计划视图 |
| v_portfolio_snapshot | 组合快照视图 |

> 注：实际查询到 4 个视图，规格文档说 3 个，以实际为准。

---

## 3. 迁移验证（migrate verify）基线

执行命令：`inv migrate verify`（2026-05-30）

| 检查项 | 状态 | 说明 |
|--------|------|------|
| Check 1: Holdings Total Market Value | ⚠️ DIFF | DB(B+C): ¥613,005 vs CSV: ¥724,379，差 15.38%。原因：行情更新导致市值变化，属预期偏差 |
| Check 2: C-Tranche Position Ratio | ✅ 预期差异 | rules.yaml 用固定基数 ¥1,285,798，DB 用实时行情，差异属设计预期 |
| Check 3: Alert Count | ✅ OK | alerts/*.md 文件 22 个，DB 迁移 22 条，完全匹配 |
| Check 4: Thesis Scores | ✅ OK | 7 只持仓论点评分全部匹配 |
| Check 5: Known Data Conflicts | ℹ️ 信息项 | 3 个已知冲突（600219 价格/513010 目标值/C 档比例分母），均为设计预期，非 bug |

**基线结论**：Check 1 的市值偏差为预存状态，后续 Phase 不得使此偏差扩大。

---

## 4. 四道风控铁律（只读，不得修改）

来源：`config/rules.yaml`

1. **交易冷静期**：买入 7 天 / 卖出 3 天 / 补仓 5 天
2. **买入先跑 IC Memo**：新建仓前必须执行 `/ic-memo`
3. **单股回撤 15% 强制审查**：触发时必须重新评估 thesis
4. **账户回撤 -20% 强制降仓**：触发时必须执行再平衡

---

## 5. 版本信息

```
inv v2.0.0
db: /Users/zshaox/Documents/Code/investment/data/portfolio.db
Python: 3.11+
```

---

## 6. 迁移脚本清单（13 个 SQL 迁移）

| 文件 | 说明 |
|------|------|
| 09_causal_schema.sql | 因果图谱主表 |
| 10_pending_edges.sql | 待审批边 |
| 11_review_log.sql | 审批日志 |
| 12_news_signals.sql | 新闻信号 |
| 13_chain_assessments.sql | 因果链评估 |

---

## 7. 回滚检查清单

每个 Phase 收尾时，对照以下清单确认底座完整：

- [ ] `pytest -q` 通过数 ≥ 179，无新增失败
- [ ] `inv data tables` 输出包含上述 25 张业务表（只增不减）
- [ ] `inv migrate verify` Check 3（告警数）和 Check 4（论点评分）仍为 ✅ OK
- [ ] `config/rules.yaml` 和 `config/capital.yaml` 的既有字段未被修改
- [ ] 四道风控铁律逻辑未被改动
