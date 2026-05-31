---
skill_id: calendar
name: 投资日历与催办
phase: 7
priority: P1
status: skeleton
---

# Skill ⑤ — 投资日历与催办

## 触发条件

- 用户询问近期投资任务
- 冷静期到期提醒
- 财报季前后
- 月度/季度例行任务
- Phase 3 再平衡待办占位回填

## 调用工具链（Phase 7 已实现）

```
# 占位 — Phase 7 实现
1. calendar_load()           → 读取 task_calendar 表
2. cooldown_check()          → 检查冷静期到期情况
3. earnings_schedule()       → 获取持仓财报日期
4. rebalance_todo_fill()     → 回填 Phase 3 的再平衡待办占位
5. task_prioritize()         → 按优先级排序
6. human_translate()         → 翻译为人话催办清单
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
