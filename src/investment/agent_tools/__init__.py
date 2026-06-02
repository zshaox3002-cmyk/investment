"""Agent tools package — CLI wrappers for all inv commands.

Every function returns a ToolResult with three fields:
  success      bool   — whether the command completed without error
  data         dict   — structured output for programmatic use
  human_message str   — plain-language summary with "所以你该做什么"

This is the ONLY channel through which Skills call the CLI.
Phase 0: wrappers only, no new business logic.
"""
from .base import ToolResult
from .snapshot import snapshot_pull, snapshot_show
from .dashboard import dashboard_render
from .data import data_tables
from .migrate import migrate_run, migrate_verify
from .trade import (
    trade_decision,
    trade_list,
    trade_log,
    trade_apply,
    trade_stop,
    exec_monitor,
)
from .thesis import thesis_sync, thesis_list, thesis_score, thesis_stale
from .candidate import candidate_scan, candidate_list, candidate_refresh, candidate_promote
from .review import review_log, review_stats
from .intent_router import route, route_with_message, list_skills, RouteResult
# Phase 2
from .onboarding import run_onboarding, get_latest_profile, ProfileInput
# Phase 3
from .position_monitor import run_position_monitor, PositionReport
from .translator import (
    translate_alert_type, translate_severity, translate_rule_path,
    translate_decision_type, translate_stop_type, translate_risk_tolerance,
    translate_causal_layer, translate_score, translate_error_code,
    translate_alert, translate_alerts,
    fmt_pct, fmt_cny,
)
from .causal import (
    causal_daily,
    causal_scan,
    causal_assess,
    causal_discover,
    causal_discover_auto,
    causal_graph,
    causal_node_add,
    causal_node_list,
    causal_edge_add,
    causal_edge_list,
    causal_review_list,
    causal_review_approve,
    causal_review_reject,
    causal_lifecycle_update,
)
# Phase 4: risk
from .risk_engine import run_risk_engine, RiskReport
# Phase 5: attribution
from .attribution import run_attribution, AttributionResult
# Phase 6: causal facade
from .causal_facade import run_causal_insight, CausalInsightReport
# Phase 7
from .stock_screen import (
    parse_screen_query, run_screen, save_strategy, list_strategies,
    ScreenCriteria, ScreenResult,
)
from .calendar import (
    run_calendar, create_task, complete_task, fill_rebalance_placeholder,
    CalendarReport, CalendarTask,
)
from .cost_calculator import (
    detect_market, calc_cost, save_cost_log, CostBreakdown,
)
from .behavior_guard import (
    log_decision, run_behavior_check, BehaviorReport, BiasFlag,
)
# Phase 8: knowledge notes (边用边学)
from .knowledge_notes import read_notes, append_concept, search_notes

__all__ = [
    "ToolResult",
    "snapshot_pull", "snapshot_show",
    "dashboard_render",
    "data_tables",
    "migrate_run", "migrate_verify",
    "trade_decision", "trade_list", "trade_log", "trade_apply", "trade_stop",
    "exec_monitor",
    "thesis_sync", "thesis_list", "thesis_score", "thesis_stale",
    "candidate_scan", "candidate_list", "candidate_refresh", "candidate_promote",
    "review_log", "review_stats",
    "causal_daily", "causal_scan", "causal_assess", "causal_discover",
    "causal_discover_auto", "causal_graph",
    "causal_node_add", "causal_node_list",
    "causal_edge_add", "causal_edge_list",
    "causal_review_list", "causal_review_approve", "causal_review_reject",
    "causal_lifecycle_update",
    # Phase 1: intent router
    "route", "route_with_message", "list_skills", "RouteResult",
    # Phase 2: onboarding
    "run_onboarding", "get_latest_profile", "ProfileInput",
    # Phase 3: position monitor + translator
    "run_position_monitor", "PositionReport",
    "translate_alert_type", "translate_severity", "translate_rule_path",
    "translate_decision_type", "translate_stop_type", "translate_risk_tolerance",
    "translate_causal_layer", "translate_score", "translate_error_code",
    "translate_alert", "translate_alerts",
    "fmt_pct", "fmt_cny",
    # Phase 4: risk
    "run_risk_engine", "RiskReport",
    # Phase 5: attribution
    "run_attribution", "AttributionResult",
    # Phase 6: causal facade
    "run_causal_insight", "CausalInsightReport",
    # Phase 7
    "parse_screen_query", "run_screen", "save_strategy", "list_strategies",
    "ScreenCriteria", "ScreenResult",
    "run_calendar", "create_task", "complete_task", "fill_rebalance_placeholder",
    "CalendarReport", "CalendarTask",
    "detect_market", "calc_cost", "save_cost_log", "CostBreakdown",
    "log_decision", "run_behavior_check", "BehaviorReport", "BiasFlag",
    # Phase 8: knowledge notes
    "read_notes", "append_concept", "search_notes",
]
