# Investment Portfolio — Claude Code Workflow

## 项目定位
全职散户 AI Skills 驱动投资系统，目标年化 10%+。A/B/C 三档资金结构：生活保障金(25%) + 核心配置 ETF(50%) + 主动选股(25%)。

## 目录结构

```
.
├── config/          # 风控铁律、持仓主表、ETF 配置、宏观判断
├── theses/          # 每只股票的投资论点 thesis.md
├── prompts/         # 5 个 Skills 模板（thesis-tracker/comps/earnings/ic-memo/idea）
├── scripts/         # 自动化脚本（8 个核心 Python 脚本）
├── reviews/         # 日/周/月/季度报告
├── alerts/          # 触发告警归档
├── trades/          # 交易决策与复盘
└── data/            # 市场数据缓存
```

详见方案文档第二章节。

## 核心工作流

### 每日战情室（Dashboard）
- `python scripts/dashboard.py` — 生成 `DASHBOARD.html`，统一查看今日待办、执行进度、合规预警
- `python scripts/dashboard.py --pre-market` — 盘前模式，强调今日待办 + 盘前检查清单
- `python scripts/dashboard.py --post-market` — 盘后模式，强调进度更新 + 盘后检查清单
- 所有待办事项统一在 `config/execution_tracker.yaml` 中追踪，执行后手动更新状态

### 5 个 Skills
- **thesis-tracker** — 持仓论点跟踪与月度评分卡更新
- **comps-analysis** — 估值横向对比（买入前/季度必跑）
- **earnings-analysis** — 财报解读（财报后 48h 必跑）
- **ic-memo** — 买入决策备忘录（新买入必须先跑）
- **idea-generation** — 每月扫候选池

### 9 个自动化脚本
- `dashboard.py` — 每日战情室 HTML 生成（**新增**）
- `daily_snapshot.py` — 每日收盘后数据快照 + 告警检查
- `alert_monitor.py` — 告警触发 → Claude 分析 → 手机推送
- `weekly_brief.py` — 每周事件日历简报（基础框架）
- `hermes_enrich.py` — 调用 Hermes Agent 获取持仓公告与宏观日历，自动写入周报
- `monthly_update.py` — 月度 thesis 评分卡更新
- `quarterly_review.py` — 季度全量诊断
- `rebalance_check.py` — 仓位偏离检查
- `trade_log.py` — 交易冷静期日志

### 日常节奏
- 每日盘前: `python scripts/dashboard.py --pre-market` → 打开 DASHBOARD.html 查看今日待办
- 每日盘后: `python scripts/daily_snapshot.py` → `python scripts/dashboard.py --post-market`
- 每周一: 30 分钟读简报（weekly_brief.py + hermes_enrich.py 自动生成，含持仓公告与宏观日历）
- 每月末: 2 小时 thesis 更新
- 每季度末: 4-6 小时完整诊断

## 四道纪律护栏
1. 交易冷静期 24 小时
2. 买入先跑 IC Memo
3. 单股回撤 15% 强制审查
4. 账户回撤 -20% 强制降仓

## 开发命令
- `python scripts/dashboard.py` — 生成每日战情室 DASHBOARD.html
- `python scripts/dashboard.py --pre-market` — 盘前模式
- `python scripts/dashboard.py --post-market` — 盘后模式

## 验证命令
- `python scripts/dashboard.py && open DASHBOARD.html` — 生成并查看每日战情室
- `python scripts/daily_snapshot.py` — 运行每日快照
- `python scripts/rebalance_check.py` — 运行仓位检查
- `python scripts/weekly_brief.py && python scripts/hermes_enrich.py` — 运行周一简报（含 Hermes 信息增强）

## 重要约束
- 所有 script 读取 config/rules.yaml 作为核心配置
- 所有 thesis 放在 theses/ 目录，按股票代码命名
- 所有交易决策写入 trades/ 目录
- 所有待办事项统一在 config/execution_tracker.yaml 中追踪状态
- DASHBOARD.html 由 dashboard.py 自动生成，不手动编辑
- Git 管理完整演进历史
