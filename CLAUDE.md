# Investment Portfolio — Claude Code Workflow

## 项目定位
全职散户 AI Skills 驱动投资系统，目标年化 10%+。A/B/C 三档资金结构：生活保障金(25%) + 核心配置 ETF(50%) + 主动选股(25%)。

当前版本：**v2**（SQLite + Typer CLI）。架构详见 `docs/architecture.md`。

## 目录结构

```
.
├── src/investment/  # v2 核心包（CLI + 业务层 + 数据层）
├── config/          # 规则与目标（rules.yaml / capital.yaml / screening_rules.yaml）
├── theses/          # 每只股票的投资论点 thesis.md
├── prompts/         # 5 个 Skills 模板
├── scripts/         # v1 脚本（保留作对账兜底，逐步废弃）
├── reviews/         # 日/周/月/季度报告
├── alerts/          # 触发告警归档（md 文件，DB 同步）
├── trades/          # 交易决策与复盘（md 文件，DB 同步）
├── data/            # portfolio.db + 行情缓存 + 迁移失败记录
├── docs/            # architecture.md 等文档
└── tests/           # pytest 单元测试
```

## 开发环境

```bash
pip install -e ".[dev]"   # 安装包 + pytest/pytest-cov
inv version               # 验证安装，显示版本和 DB 路径
```

## 核心命令（v2 inv CLI）

### 每日盘后流程
```bash
inv snapshot pull                    # 拉行情 + 写 holdings/quotes/alerts
inv dashboard render && open DASHBOARD.html  # 生成并查看战情室
```

### 迁移与验证
```bash
inv migrate run                      # 执行 01-08 迁移脚本（幂等）
inv migrate verify                   # 5 项数据对账，输出 diff_report.md
```

### 交易命令链
```bash
inv trade decision new --code 600219 --type REDUCE
inv trade log --decision-id N --shares X --price Y
inv trade stop add / list
inv trade apply ID
inv exec monitor                     # 检查止损止盈触发
```

### Thesis 与候选池
```bash
inv thesis sync / score / stale
inv candidate scan --quick           # akshare 扫描候选池
inv candidate list / promote ID
```

### 复盘
```bash
inv review log --trade-id N
inv review stats                     # 错误归因频次统计
```

## 5 个 Skills（手动调用）
- **thesis-tracker** — 持仓论点跟踪与月度评分卡更新
- **comps-analysis** — 估值横向对比（买入前/季度必跑）
- **earnings-analysis** — 财报解读（财报后 48h 必跑）
- **ic-memo** — 买入决策备忘录（新买入必须先跑）
- **idea-generation** — 每月扫候选池

## 四道纪律护栏
1. 交易冷静期 24 小时
2. 买入先跑 IC Memo
3. 单股回撤 15% 强制审查
4. 账户回撤 -20% 强制降仓

## 验证命令
```bash
pytest -q                            # 42 个单元测试，核心模块覆盖率 65%+
inv migrate verify                   # 数据对账
inv snapshot pull                    # 端到端盘后流程
```

## Bash 命令执行规范

执行任何 Bash 命令时必须遵守：

1. 所有可能耗时的命令加 `timeout 60s`（或合适时长）前缀
2. 不用 `| tail -N` 隐藏输出；需截断时用 `tee /tmp/command.log` 保存完整日志
3. 用 `tee /tmp/command.log` 保存命令日志
4. 命令结束后报告：退出码、耗时、最后 20 行日志
5. 命令超过 60 秒无输出，先停止并说明可能卡在哪里
6. 可能交互的命令优先用非交互参数（`-y`、`--non-interactive`），或提前说明需要用户输入

## 重要约束
- `config/*.yaml` 只放规则与目标，状态全部入 DB
- 所有 thesis 放在 `theses/` 目录，按股票代码命名
- 所有交易决策写入 `trades/` 目录（md 正文）+ DB（元数据）
- `DASHBOARD.html` 由 `inv dashboard render` 生成，不手动编辑
- v1 `scripts/` 保留 ≥90 天作对账兜底，不删除
- Git 管理完整演进历史；`legacy/v1-csv-md` tag 是回退点
