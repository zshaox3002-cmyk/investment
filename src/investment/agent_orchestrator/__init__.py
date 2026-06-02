"""Agent Orchestrator — v3 编排层。

职责：
  1. runner.py         — 按顺序调用各 agent_tools 模块，聚合结构化结果
  2. operating_state.py — 计算健康灯（红/黄/绿），写入 daily_operating_state
  3. task_generator.py  — 各模块输出映射为分层任务，写入 task_calendar
  4. prioritizer.py     — 去重 + 分层排序（executable/confirm/monitor/blocked/info）
  5. brief.py           — 数据驱动的今日简报（非 LLM 自由发挥）

不重写任何既有 agent_tools 模块；仅做封装与编排。
"""
