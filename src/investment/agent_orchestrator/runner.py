"""Agent runner — orchestrates all modules in sequence.

Call order (premarket / postmarket):
  1. exec_monitor          — check triggered stop/take-profit rules
  2. run_position_monitor  — holdings + iron rules + tranche deviation
  3. run_risk_engine       — vol / VaR / correlation / pseudo-div
  4. run_attribution       — BHB return decomposition
  5. run_calendar          — overdue / due-soon tasks
  6. run_causal_insight    — external signal impact

snapshot_pull is intentionally excluded from the orchestrator — it requires
network access and is run separately by the user (inv snapshot pull).  The
orchestrator always reads from DB state, so it can run offline.
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Optional

from investment.core.db import transaction


RunMode = Literal["premarket", "postmarket", "manual"]


@dataclass
class ModuleResult:
    name: str
    ok: bool
    data: Any = None          # typed result object from the module
    error: str = ""


@dataclass
class OrchestratorResult:
    """Aggregated results from all modules in one run."""

    mode: RunMode
    run_date: str
    started_at: str
    finished_at: str = ""

    exec_monitor: ModuleResult = field(default_factory=lambda: ModuleResult("exec_monitor", False))
    position: ModuleResult = field(default_factory=lambda: ModuleResult("position", False))
    risk: ModuleResult = field(default_factory=lambda: ModuleResult("risk", False))
    attribution: ModuleResult = field(default_factory=lambda: ModuleResult("attribution", False))
    calendar: ModuleResult = field(default_factory=lambda: ModuleResult("calendar", False))
    causal: ModuleResult = field(default_factory=lambda: ModuleResult("causal", False))

    errors: list[str] = field(default_factory=list)
    new_task_ids: list[int] = field(default_factory=list)

    # Convenience accessors
    @property
    def position_report(self):
        return self.position.data

    @property
    def risk_report(self):
        return self.risk.data

    @property
    def attribution_result(self):
        return self.attribution.data

    @property
    def calendar_report(self):
        return self.calendar.data

    @property
    def causal_result(self):
        return self.causal.data


def _run_module(name: str, fn, *args, **kwargs) -> ModuleResult:
    """Run a module function, catching all exceptions."""
    try:
        result = fn(*args, **kwargs)
        return ModuleResult(name=name, ok=True, data=result)
    except Exception as exc:
        return ModuleResult(name=name, ok=False, error=f"{type(exc).__name__}: {exc}")


def run(
    mode: RunMode = "premarket",
    db_path: Optional[str] = None,
    save_log: bool = True,
) -> OrchestratorResult:
    """Orchestrate all modules and return aggregated results.

    Modules are called in dependency order; a failure in one does not
    prevent subsequent modules from running.
    """
    today = date.today().isoformat()
    started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    result = OrchestratorResult(mode=mode, run_date=today, started_at=started_at)

    log_id: Optional[int] = None
    if save_log:
        log_id = _start_run_log(mode, today, started_at, db_path)

    # ── 1. exec_monitor ───────────────────────────────────────────────────────
    from investment.agent_tools.trade import exec_monitor
    result.exec_monitor = _run_module("exec_monitor", exec_monitor)

    # ── 2. position_monitor ───────────────────────────────────────────────────
    from investment.agent_tools.position_monitor import run_position_monitor
    result.position = _run_module("position", run_position_monitor, db_path=db_path)

    # ── 3. risk_engine ────────────────────────────────────────────────────────
    from investment.agent_tools.risk_engine import run_risk_engine
    result.risk = _run_module("risk", run_risk_engine, db_path=db_path, save=True)

    # ── 4. attribution ────────────────────────────────────────────────────────
    from investment.agent_tools.attribution import run_attribution
    result.attribution = _run_module("attribution", run_attribution, save=True, db_path=db_path)

    # ── 5. calendar ───────────────────────────────────────────────────────────
    from investment.agent_tools.calendar import run_calendar
    result.calendar = _run_module("calendar", run_calendar, db_path=db_path)

    # ── 6. causal_insight ─────────────────────────────────────────────────────
    from investment.agent_tools.causal_facade import run_causal_insight
    result.causal = _run_module("causal", run_causal_insight, db_path=db_path)

    # ── 7. task_generator ─────────────────────────────────────────────────────
    from investment.agent_orchestrator.task_generator import generate_tasks
    try:
        new_task_ids = generate_tasks(result, db_path=db_path)
        result.new_task_ids = new_task_ids
    except Exception as exc:
        result.new_task_ids = []
        result.errors.append(f"task_generator: {exc}")

    result.finished_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    result.errors = [m.error for m in _all_modules(result) if not m.ok and m.error]

    if save_log and log_id is not None:
        status = "completed" if not result.errors else "partial"
        _finish_run_log(log_id, result.finished_at, status, result, db_path)

    return result


def _all_modules(result: OrchestratorResult) -> list[ModuleResult]:
    return [
        result.exec_monitor, result.position, result.risk,
        result.attribution, result.calendar, result.causal,
    ]


# ── agent_run_log helpers ─────────────────────────────────────────────────────

def _start_run_log(mode: str, run_date: str, started_at: str, db_path) -> Optional[int]:
    try:
        with transaction(db_path) as conn:
            cur = conn.execute(
                "INSERT INTO agent_run_log (run_date, mode, started_at, status, summary) "
                "VALUES (?, ?, ?, 'running', '')",
                (run_date, mode, started_at),
            )
            return cur.lastrowid
    except Exception:
        return None


def _finish_run_log(
    log_id: int,
    finished_at: str,
    status: str,
    result: OrchestratorResult,
    db_path,
) -> None:
    ok_modules = [m.name for m in _all_modules(result) if m.ok]
    fail_modules = [m.name for m in _all_modules(result) if not m.ok]
    summary = f"ok={','.join(ok_modules) or 'none'}; fail={','.join(fail_modules) or 'none'}"
    error_msg = "; ".join(result.errors) if result.errors else None
    try:
        with transaction(db_path) as conn:
            conn.execute(
                "UPDATE agent_run_log SET finished_at=?, status=?, summary=?, error_message=? WHERE id=?",
                (finished_at, status, summary, error_msg, log_id),
            )
    except Exception:
        pass
