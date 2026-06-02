---
skill_id: calendar
name: 投资日历与催办
phase: 7
priority: P1
status: implemented
---

# Skill ⑤ — 投资日历与催办

## 触发条件

- 用户询问近期投资任务
- 冷静期到期提醒
- 财报季前后
- 月度/季度例行任务
- Phase 3 再平衡待办占位回填

## 调用工具链（Phase 7 已实现）

```python
from investment.agent_tools.calendar import (
    run_calendar, create_task, complete_task, fill_rebalance_placeholder,
)

# 1. 加载日历任务（自动标记逾期、种子标准任务、回填再平衡占位）
report = run_calendar(period="week")  # today / week / month / quarter / year
# report.overdue       — 已逾期任务列表
# report.due_soon      — 3 天内到期任务
# report.upcoming      — 本周期内其他任务
# report.human_message — 完整人话催办清单

# 2. 手动创建/完成任务
task_id = create_task("检查600519止损", category="custom", due_date="2026-06-07", priority="high")
complete_task(task_id, notes="已检查，止损位未触发")

# 3. CLI 入口
# inv calendar show --period week
# inv calendar task add --title "..." --category custom --due 2026-06-07
```

## 输入 Schema

```yaml
inputs:
  period:
    type: string
    enum: [today, week, month, quarter, year]
    description: 查询时间范围
    default: week
  include_completed:
    type: boolean
    description: 是否包含已完成任务
    default: false
```

## 输出 Schema

```yaml
outputs:
  tasks:
    type: array
    items:
      task_id: integer
      title: string
      due_date: string
      priority: string      # high / medium / low
      category: string      # 冷静期 / 财报 / 再平衡 / 月度例行
      action_required: string
  overdue_count: integer
  human_message: string
```

## 用户话术模板

```
## 投资日历 — [本周/本月]

### 紧急待办（[N] 项）
- [ ] [任务描述]（截止：[日期]）
  所以你该做什么：[具体操作]

### 本周计划
| 日期 | 任务 | 类型 | 优先级 |
|------|------|------|--------|
| [日期] | [任务] | [类型] | [高/中/低] |

### 即将到期
[冷静期/财报/再平衡等提醒]
```

## 不做什么（Phase 7 边界）

- 不实现外部日历同步（如 Google Calendar）
- 不做财报内容分析（→ `/earnings-analysis`）
