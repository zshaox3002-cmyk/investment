"""Typer CLI entry point.

Run as `inv ...` after `pip install -e .` or `python -m investment.cli ...`.
"""
from __future__ import annotations

from datetime import date as dt_date
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from investment import __version__
from investment.core import db
from investment.core.settings import DB_PATH

app = typer.Typer(
    name="inv",
    help="Personal investment portfolio CLI (SQLite-backed).",
    no_args_is_help=True,
)
console = Console()

migrate_app = typer.Typer(help="Database migrations.", no_args_is_help=True)
data_app = typer.Typer(help="Data inspection.", no_args_is_help=True)
snapshot_app = typer.Typer(help="Daily snapshot commands.", no_args_is_help=True)
dashboard_app = typer.Typer(help="Dashboard generation.", no_args_is_help=True)
thesis_app = typer.Typer(help="Thesis management.", no_args_is_help=True)
trade_app = typer.Typer(help="Trade decisions and logs.", no_args_is_help=True)
exec_app = typer.Typer(help="Execution monitoring.", no_args_is_help=True)
candidate_app = typer.Typer(help="Candidate pool.", no_args_is_help=True)
review_app = typer.Typer(help="Trade reviews.", no_args_is_help=True)
causal_app = typer.Typer(help="Causal graph analysis.", no_args_is_help=True)
profile_app = typer.Typer(help="Investor profile and goals.", no_args_is_help=True)
risk_app = typer.Typer(help="Portfolio risk quantification.", no_args_is_help=True)
attribution_app = typer.Typer(help="Performance attribution.", no_args_is_help=True)
calendar_app = typer.Typer(help="Investment calendar and reminders.", no_args_is_help=True)
cost_app = typer.Typer(help="Trade cost calculator.", no_args_is_help=True)
behavior_app = typer.Typer(help="Behavioral bias detection.", no_args_is_help=True)
notes_app = typer.Typer(help="Knowledge notes (边用边学).", no_args_is_help=True)
agent_app = typer.Typer(help="Agent orchestrator (v3).", no_args_is_help=True)
goal_app = typer.Typer(help="Annual goal progress (v3).", no_args_is_help=True)
health_app = typer.Typer(help="Position health scoring (v3).", no_args_is_help=True)
app.add_typer(migrate_app, name="migrate")
app.add_typer(data_app, name="data")
app.add_typer(snapshot_app, name="snapshot")
app.add_typer(dashboard_app, name="dashboard")
app.add_typer(thesis_app, name="thesis")
app.add_typer(trade_app, name="trade")
app.add_typer(exec_app, name="exec")
app.add_typer(candidate_app, name="candidate")
app.add_typer(review_app, name="review")
app.add_typer(causal_app, name="causal")
app.add_typer(profile_app, name="profile")
app.add_typer(risk_app, name="risk")
app.add_typer(attribution_app, name="attribution")
app.add_typer(calendar_app, name="calendar")
app.add_typer(cost_app, name="cost")
app.add_typer(behavior_app, name="behavior")
app.add_typer(notes_app, name="notes")
app.add_typer(agent_app, name="agent")
app.add_typer(goal_app, name="goal")
app.add_typer(health_app, name="health")

# ── inv dashboard serve ────────────────────────────────────────────────────────

@dashboard_app.command("serve")
def dashboard_serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(8765, "--port", "-p", help="Port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code change"),
) -> None:
    """Start the v3 web dashboard (FastAPI + uvicorn)."""
    import uvicorn
    console.print(f"[bold]Dashboard v3[/bold] → http://{host}:{port}")
    console.print("  Press Ctrl+C to stop.")
    uvicorn.run(
        "investment.web.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="warning",
    )


@app.command()
def version() -> None:
    """Show version and DB path."""
    console.print(f"[bold]inv[/bold] v{__version__}")
    console.print(f"db: {DB_PATH}")


@migrate_app.command("run")
def migrate_run(force: bool = typer.Option(False, "--force", help="Re-apply all migrations")) -> None:
    """Run all migration steps (Python + SQL, idempotent)."""
    from investment.migration.runner import run_all
    from investment.core.sql_migrator import run_sql_migrations
    run_all()
    sql_results = run_sql_migrations(force=force)
    for fname, status in sql_results.items():
        if status == "skipped":
            console.print(f"  [dim]SQL {fname}: skipped[/dim]")
        elif status.startswith("failed"):
            console.print(f"  [red]SQL {fname}: {status}[/red]")
        else:
            console.print(f"  [green]SQL {fname}: {status}[/green]")


@migrate_app.command("verify")
def migrate_verify() -> None:
    """Reconcile DB against source files and write diff report."""
    from investment.migration.verify import run as verify_run
    ok = verify_run()
    if ok:
        console.print("[green]✓ All checks passed[/green]")
    else:
        console.print("[yellow]⚠ Some checks failed — see data/migration_diff_report.md[/yellow]")
        raise typer.Exit(1)


@migrate_app.command("rollback")
def migrate_rollback() -> None:
    """Delete portfolio.db to revert to CSV-based workflow."""
    if DB_PATH.exists():
        DB_PATH.unlink()
        console.print(f"[yellow]Deleted {DB_PATH}[/yellow]")
    else:
        console.print("No DB file found.")


@data_app.command("tables")
def data_tables() -> None:
    """List tables and views."""
    rows = db.list_tables()
    table = Table(title="DB objects")
    table.add_column("name")
    for name in rows:
        table.add_row(name)
    console.print(table)


@snapshot_app.command("pull")
def snapshot_pull(
    date: str = typer.Option("", "--date", help="Override date (YYYY-MM-DD)"),
) -> None:
    """Fetch prices, update DB, run alerts, generate daily report."""
    from investment.workflow.snapshot import run as snap_run
    result = snap_run(date_str=date or None)
    if result:
        console.print(f"[green]✓[/green] {result['date']} | total={result['total_all']:,.0f} | alerts={result['alerts']}")


@snapshot_app.command("show")
def snapshot_show(
    date: str = typer.Option("", "--date", help="Date to show (YYYY-MM-DD, default today)"),
) -> None:
    """Print the daily report for a given date."""
    from investment.core.settings import REVIEWS_DIR
    from datetime import date as dt
    d = date or dt.today().isoformat()
    path = REVIEWS_DIR / "daily" / f"{d}.md"
    if not path.exists():
        console.print(f"[red]No report for {d}[/red]")
        raise typer.Exit(1)
    console.print(path.read_text(encoding="utf-8"))


# ── Dashboard commands ────────────────────────────────────────────────────

@dashboard_app.command("render")
def dashboard_render(
    mode: str = typer.Option("standard", "--mode", help="standard | pre-market | post-market"),
) -> None:
    """Generate DASHBOARD.html from DB."""
    from investment.reports.dashboard import run as dash_run
    path = dash_run(mode=mode)
    console.print(f"[green]✓[/green] {path}")


@dashboard_app.command("pre-market")
def dashboard_pre() -> None:
    """Generate DASHBOARD.html in pre-market mode."""
    from investment.reports.dashboard import run as dash_run
    path = dash_run(mode="pre-market")
    console.print(f"[green]✓[/green] {path}")


@dashboard_app.command("post-market")
def dashboard_post() -> None:
    """Generate DASHBOARD.html in post-market mode."""
    from investment.reports.dashboard import run as dash_run
    path = dash_run(mode="post-market")
    console.print(f"[green]✓[/green] {path}")


# ── Thesis commands ───────────────────────────────────────────────────────

@thesis_app.command("sync")
def thesis_sync() -> None:
    """Sync frontmatter from theses/*.md into DB."""
    from investment.workflow.thesis import sync
    n = sync()
    console.print(f"[green]✓[/green] {n} theses synced")


@thesis_app.command("list")
def thesis_list() -> None:
    """List all theses with current scores."""
    from investment.workflow.thesis import list_theses
    rows = list_theses()
    table = Table(title="Theses")
    table.add_column("Code")
    table.add_column("Name")
    table.add_column("Score")
    table.add_column("Rating")
    table.add_column("Action")
    table.add_column("Updated")
    for r in rows:
        score = f"{r['current_score']:.2f}" if r["current_score"] is not None else "—"
        table.add_row(
            r["code"], r["name"], score,
            r["rating"] or "—", r["action"] or "—", r["updated_at"] or "—",
        )
    console.print(table)


@thesis_app.command("score")
def thesis_score(
    code: str = typer.Argument(help="Stock code"),
    dimension: str = typer.Option("综合", "--dimension", "-d"),
    score: float = typer.Option(..., "--score", "-s"),
    rationale: str = typer.Option("", "--rationale", "-r"),
) -> None:
    """Record a dimension score for a thesis."""
    from investment.workflow.thesis import record_score
    ok = record_score(code, dimension, score, rationale)
    if ok:
        console.print(f"[green]✓[/green] {code} {dimension} = {score}")
    else:
        console.print(f"[red]instrument not found: {code}[/red]")
        raise typer.Exit(1)


@thesis_app.command("stale")
def thesis_stale(
    days: int = typer.Option(90, "--days", help="Threshold in days"),
) -> None:
    """List theses not updated within N days."""
    from investment.workflow.thesis import stale_theses
    rows = stale_theses(days)
    if not rows:
        console.print(f"[green]✓[/green] All theses updated within {days} days")
        return
    table = Table(title=f"Stale Theses (>{days} days)")
    table.add_column("Code")
    table.add_column("Name")
    table.add_column("Score")
    table.add_column("Last Updated")
    table.add_column("Days Since")
    for r in rows:
        score = f"{r['current_score']:.2f}" if r["current_score"] is not None else "—"
        days_s = f"{int(r['days_since'])}" if r["days_since"] else "?"
        table.add_row(r["code"], r["name"], score, r["updated_at"] or "—", days_s)
    console.print(table)


# ── Trade commands ────────────────────────────────────────────────────────

@trade_app.command("decision")
def trade_decision_new(
    code: str = typer.Argument(help="Stock code"),
    decision_type: str = typer.Option("REDUCE", "--type", "-t",
                                       help="NEW|ADD|REDUCE|EXIT|REBALANCE|EMERGENCY"),
    notes: str = typer.Option("", "--notes", "-n"),
    ic_memo: bool = typer.Option(False, "--ic-memo", help="Mark IC memo as passed"),
) -> None:
    """Create a new decision record and stub markdown."""
    from investment.workflow.trade import new_decision
    result = new_decision(code, decision_type, notes, ic_memo)
    console.print(f"[green]✓[/green] {result['decision_no']} created → {result['body_path']}")


@trade_app.command("list")
def trade_decision_list(
    status: str = typer.Option("active", "--status", help="active|all|executed|cancelled"),
) -> None:
    """List decisions."""
    from investment.workflow.trade import list_decisions
    rows = list_decisions(status)
    table = Table(title=f"Decisions ({status})")
    table.add_column("No")
    table.add_column("Date")
    table.add_column("Type")
    table.add_column("Code")
    table.add_column("Status")
    table.add_column("IC Memo")
    for r in rows:
        table.add_row(
            r["decision_no"], r["decision_date"], r["decision_type"],
            r["code"] or "—", r["status"],
            "✅" if r["ic_memo_passed"] else "❌",
        )
    console.print(table)


@trade_app.command("log")
def trade_log(
    code: str = typer.Argument(help="Stock code"),
    shares: float = typer.Option(..., "--shares", "-s"),
    price: float = typer.Option(..., "--price", "-p"),
    side: str = typer.Option("BUY", "--side", help="BUY|SELL"),
    decision: str = typer.Option("", "--decision-id", "-d", help="decision_NNN"),
    fees: float = typer.Option(0.0, "--fees"),
    notes: str = typer.Option("", "--notes"),
    trade_date: str = typer.Option("", "--date"),
) -> None:
    """Record a trade execution."""
    from investment.workflow.trade import log_trade
    tid = log_trade(
        code, shares, price, side,
        decision_no=decision or None,
        fees=fees, notes=notes,
        trade_date=trade_date or None,
    )
    console.print(f"[green]✓[/green] trade #{tid} logged: {side} {shares:.0f} × ¥{price:.3f}")


@trade_app.command("stop")
def trade_stop_add(
    code: str = typer.Argument(help="Stock code"),
    decision: str = typer.Option(..., "--decision-id", "-d"),
    rule_type: str = typer.Option(..., "--type", "-t",
                                   help="GRID_SELL|GRID_BUY|STOP_LOSS|TAKE_PROFIT|HARD_DD"),
    trigger_kind: str = typer.Option("PRICE_ABS", "--trigger-kind"),
    trigger_value: float = typer.Option(..., "--trigger-value", "-v"),
    action: str = typer.Option(..., "--action", "-a"),
    shares: float = typer.Option(0.0, "--shares"),
    priority: int = typer.Option(100, "--priority"),
) -> None:
    """Add a stop rule."""
    from investment.workflow.trade import add_stop_rule
    rid = add_stop_rule(
        code, decision, rule_type, trigger_kind, trigger_value, action,
        shares=shares or None, priority=priority,
    )
    console.print(f"[green]✓[/green] stop_rule #{rid} added: {rule_type} @ {trigger_value}")


@trade_app.command("apply")
def trade_apply(
    trade_id: int = typer.Argument(help="Trade ID to apply to holdings"),
) -> None:
    """Apply a trade to update holdings (shares + weighted avg cost)."""
    from investment.workflow.trade import apply_trade
    result = apply_trade(trade_id)
    console.print(
        f"[green]✓[/green] trade #{trade_id} applied: "
        f"shares={result['new_shares']:.0f}, cost=¥{result['new_cost']:.3f}"
    )


# ── Exec monitor ──────────────────────────────────────────────────────────

@exec_app.command("monitor")
def exec_monitor() -> None:
    """Check armed stop_rules against latest quotes."""
    from investment.workflow.trade import monitor_executions
    results = monitor_executions()
    if not results:
        console.print("[yellow]No armed stop rules found[/yellow]")
        return
    table = Table(title="Stop Rules Monitor")
    table.add_column("ID")
    table.add_column("Code")
    table.add_column("Type")
    table.add_column("Trigger")
    table.add_column("Current")
    table.add_column("Action")
    table.add_column("Status")
    for r in results:
        tv = f"{r['trigger_value']:.3f}" if r["trigger_value"] is not None else "—"
        cp = f"¥{r['current_price']:.3f}" if r["current_price"] else "—"
        table.add_row(
            str(r["id"]), r["code"], r["rule_type"],
            f"{r['trigger_kind']}={tv}", cp,
            r["action"][:30], r["status"],
        )
    console.print(table)


# ── Candidate commands ────────────────────────────────────────────────────

@candidate_app.command("scan")
def candidate_scan(
    source: str = typer.Option("akshare", "--source", help="akshare|manual"),
    csv_file: str = typer.Option("", "--csv", help="CSV file path (manual mode)"),
    quick: bool = typer.Option(False, "--quick", help="Quick mode (sample 200 stocks)"),
) -> None:
    """Scan candidate pool."""
    if source == "manual":
        if not csv_file:
            console.print("[red]--csv required for manual mode[/red]")
            raise typer.Exit(1)
        from investment.workflow.candidate import scan_manual
        n = scan_manual(csv_file)
    else:
        from investment.workflow.candidate import scan_akshare
        n = scan_akshare(quick=quick)
    console.print(f"[green]✓[/green] {n} candidates inserted")


@candidate_app.command("list")
def candidate_list(
    priority: int = typer.Option(0, "--priority", help="Max priority (0=all)"),
    status: str = typer.Option("candidate", "--status"),
    limit: int = typer.Option(30, "--limit"),
) -> None:
    """List candidates."""
    from investment.workflow.candidate import list_candidates
    rows = list_candidates(
        priority=priority or None,
        status=status,
        limit=limit,
    )
    table = Table(title=f"Candidates ({status})")
    table.add_column("ID")
    table.add_column("Code")
    table.add_column("Name")
    table.add_column("PE")
    table.add_column("ROE")
    table.add_column("Score")
    table.add_column("Compliant")
    table.add_column("Status")
    for r in rows:
        pe = f"{r['pe_ttm']:.1f}" if r["pe_ttm"] else "—"
        roe = f"{r['roe_3y_avg']*100:.1f}%" if r["roe_3y_avg"] else "—"
        score = f"{r['composite_score']:.2f}" if r["composite_score"] else "—"
        compliant = "✅" if r["compliance_passed"] else f"❌ {r['compliance_blocked_by'] or ''}"
        table.add_row(
            str(r["id"]), r["code"], r["name"],
            pe, roe, score, compliant, r["status"],
        )
    console.print(table)


@candidate_app.command("refresh")
def candidate_refresh(
    codes: str = typer.Option("", "--codes", help="逗号分隔的股票代码，留空则刷新全部候选"),
    delay: float = typer.Option(1.5, "--delay", help="请求间隔秒数（默认1.5s）"),
) -> None:
    """Refresh PE/market_cap/PB for candidates via stock_value_em."""
    from investment.workflow.candidate import refresh_valuations
    code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
    result = refresh_valuations(codes=code_list, delay=delay)
    console.print(
        f"[green]✓[/green] 更新={result['updated']} 跳过={result['skipped']} 失败={result['failed']}"
    )
    if result["errors"]:
        for e in result["errors"]:
            console.print(f"  [yellow]⚠[/yellow] {e}")


@candidate_app.command("promote")
def candidate_promote(
    candidate_id: int = typer.Argument(help="Candidate ID"),
    to: str = typer.Option(..., "--to", help="ic_memo|accepted|rejected|researching|expired"),
) -> None:
    """Promote a candidate to a new status."""
    from investment.workflow.candidate import promote_candidate
    ok = promote_candidate(candidate_id, to)
    if ok:
        console.print(f"[green]✓[/green] candidate #{candidate_id} → {to}")
    else:
        console.print(f"[red]candidate #{candidate_id} not found[/red]")
        raise typer.Exit(1)


# ── Review commands ───────────────────────────────────────────────────────

@review_app.command("log")
def review_log(
    trade_id: int = typer.Option(..., "--trade-id", "-t"),
    outcome: str = typer.Option(..., "--outcome", "-o",
                                 help="win|loss|break_even|partial"),
    errors: str = typer.Option("", "--errors", "-e",
                                help="Comma-separated error codes"),
    emotion: str = typer.Option("", "--emotion"),
    pnl: float = typer.Option(0.0, "--pnl"),
    rule_breach: bool = typer.Option(False, "--rule-breach"),
) -> None:
    """Record a trade review."""
    from investment.workflow.review import log_review, ERROR_CODES
    error_list = [e.strip().upper() for e in errors.split(",") if e.strip()] if errors else []
    rid = log_review(
        trade_id, outcome, error_list,
        emotion=emotion, result_pnl=pnl or None,
        rule_breach=rule_breach,
    )
    console.print(f"[green]✓[/green] review #{rid} logged for trade #{trade_id}")


@review_app.command("stats")
def review_stats(
    months: int = typer.Option(3, "--months", "-m"),
) -> None:
    """Show error code frequency over last N months."""
    from investment.workflow.review import review_stats as get_stats, ERROR_LABELS
    rows = get_stats(months)
    if not rows:
        console.print(f"[green]No reviews in last {months} months[/green]")
        return
    table = Table(title=f"Error Frequency (last {months} months)")
    table.add_column("Error Code")
    table.add_column("Label")
    table.add_column("Count")
    table.add_column("Severity Score")
    for r in rows:
        table.add_row(
            r["error_code"],
            ERROR_LABELS.get(r["error_code"], r["error_code"]),
            str(r["count"]),
            str(r["severity_score"]),
        )
    console.print(table)


# ── Causal commands ────────────────────────────────────────────────────────

causal_node_app = typer.Typer(help="Causal graph node CRUD.", no_args_is_help=True)
causal_edge_app = typer.Typer(help="Causal graph edge CRUD.", no_args_is_help=True)
causal_app.add_typer(causal_node_app, name="node")
causal_app.add_typer(causal_edge_app, name="edge")


def _auto_keywords(name: str, description: str) -> str:
    """Try to generate keywords via LLM; return '[]' on failure."""
    try:
        from investment.core.llm import call_llm_with_schema
        from pydantic import BaseModel

        class Keywords(BaseModel):
            keywords: list[str]

        result = call_llm_with_schema(
            f"为因果图谱节点生成5-10个中文关键词，用于匹配新闻。\n"
            f"节点名称：{name}\n描述：{description or '无'}",
            Keywords,
            system_prompt="你是量化研究员，为因果节点生成关键词。",
            max_retries=2,
        )
        import json
        return json.dumps(result.keywords, ensure_ascii=False)
    except Exception:
        return "[]"


@causal_node_app.command("add")
def causal_node_add(
    name: str = typer.Option(..., "--name", help="Unique node name"),
    node_type: str = typer.Option(..., "--type", help="event|macro|commodity|sector|holding|policy"),
    layer: str = typer.Option(..., "--layer", help="L0_geopolitical|L1_macro|L2_industry|L3_holding"),
    description: str = typer.Option("", "--description"),
    keywords: str = typer.Option("", "--keywords", help="JSON array; auto-generated if empty"),
) -> None:
    """Add a node to the causal graph."""
    from investment.causal.repo import CausalRepo

    kw = keywords if keywords else _auto_keywords(name, description)
    repo = CausalRepo()
    with repo.transaction():
        nid = repo.add_node(name, node_type, layer, description, kw)
    console.print(f"[green]✓[/green] node #{nid} created: {name} ({layer})")


@causal_node_app.command("list")
def causal_node_list(
    layer: str = typer.Option("", "--layer", help="Filter: L0_geopolitical|L1_macro|L2_industry|L3_holding"),
    state: str = typer.Option("", "--state", help="Filter: active|dormant|archived"),
    node_type: str = typer.Option("", "--type", help="Filter by node_type"),
) -> None:
    """List nodes in the causal graph."""
    from investment.causal.repo import CausalRepo

    repo = CausalRepo()
    with repo.transaction():
        nodes = repo.list_nodes(
            layer=layer or None,
            state=state or None,
            node_type=node_type or None,
        )
    if not nodes:
        console.print("[yellow]No nodes found[/yellow]")
        return
    table = Table(title="Causal Nodes")
    table.add_column("ID")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Layer")
    table.add_column("Activation")
    table.add_column("Signals(30d)")
    table.add_column("State")
    for n in nodes:
        table.add_row(
            str(n.node_id), n.name, n.node_type, n.layer,
            f"{n.activation_score:.1f}", str(n.signal_count_30d),
            n.lifecycle_state,
        )
    console.print(table)


@causal_node_app.command("show")
def causal_node_show(
    name: str = typer.Argument(help="Node name"),
) -> None:
    """Show a single node's details."""
    from investment.causal.repo import CausalRepo

    repo = CausalRepo()
    with repo.transaction():
        node = repo.get_node(name)
    if not node:
        console.print(f"[red]Node not found: {name}[/red]")
        raise typer.Exit(1)
    console.print(f"[bold]{node.name}[/bold] (#{node.node_id})")
    console.print(f"  Type: {node.node_type} | Layer: {node.layer} | State: {node.lifecycle_state}")
    console.print(f"  Activation: {node.activation_score:.1f} | Signals(30d): {node.signal_count_30d}")
    console.print(f"  Keywords: {node.keywords}")
    if node.description:
        console.print(f"  Description: {node.description}")


@causal_edge_app.command("add")
def causal_edge_add(
    from_name: str = typer.Option(..., "--from", help="Source node name"),
    to_name: str = typer.Option(..., "--to", help="Target node name"),
    direction: int = typer.Option(1, "--direction", help="1=positive, -1=negative"),
    d1: int = typer.Option(3, "--d1", help="Directness (1-5)"),
    d2: int = typer.Option(3, "--d2", help="Elasticity (1-5)"),
    d3: int = typer.Option(3, "--d3", help="Consistency (1-5)"),
    d4: int = typer.Option(3, "--d4", help="Speed (1-5)"),
    d5: int = typer.Option(3, "--d5", help="Uniqueness (1-5)"),
    lag: int = typer.Option(0, "--lag", help="Lag in days"),
    evidence: str = typer.Option("", "--evidence", help="Evidence summary"),
) -> None:
    """Add a directed edge between two nodes."""
    from investment.causal.repo import CausalRepo
    from investment.causal.models import EdgeScore5D

    repo = CausalRepo()
    with repo.transaction():
        try:
            eid = repo.add_edge(
                from_name, to_name, direction,
                d1=d1, d2=d2, d3=d3, d4=d4, d5=d5,
                lag_days=lag, evidence_summary=evidence,
            )
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)

    scores = EdgeScore5D(d1_directness=d1, d2_elasticity=d2, d3_consistency=d3, d4_speed=d4, d5_uniqueness=d5)
    console.print(
        f"[green]✓[/green] edge #{eid}: {from_name} → {to_name} "
        f"(dir={direction:+d}, strength={scores.composite_strength():.2f})"
    )


@causal_edge_app.command("list")
def causal_edge_list(
    from_name: str = typer.Option("", "--from", help="Filter by source node"),
    to_name: str = typer.Option("", "--to", help="Filter by target node"),
) -> None:
    """List edges in the causal graph."""
    from investment.causal.repo import CausalRepo

    repo = CausalRepo()
    with repo.transaction():
        edges = repo.list_edges(
            from_name=from_name or None,
            to_name=to_name or None,
        )
    if not edges:
        console.print("[yellow]No edges found[/yellow]")
        return
    table = Table(title="Causal Edges")
    table.add_column("ID")
    table.add_column("From")
    table.add_column("→")
    table.add_column("To")
    table.add_column("Dir")
    table.add_column("Strength")
    table.add_column("Lag(d)")
    table.add_column("5D Scores (d1-d5)")
    for e in edges:
        scores = f"{e.d1_directness or '-'}/{e.d2_elasticity or '-'}/{e.d3_consistency or '-'}/{e.d4_speed or '-'}/{e.d5_uniqueness or '-'}"
        table.add_row(
            str(e.edge_id), e.from_name, "→", e.to_name,
            "+" if e.direction == 1 else "−",
            f"{e.strength:.2f}" if e.strength else "—",
            str(e.lag_days),
            scores,
        )
    console.print(table)


@causal_edge_app.command("show")
def causal_edge_show(
    edge_id: int = typer.Argument(help="Edge ID"),
) -> None:
    """Show a single edge's details."""
    from investment.causal.repo import CausalRepo

    repo = CausalRepo()
    with repo.transaction():
        edge = repo.get_edge(edge_id)
    if not edge:
        console.print(f"[red]Edge not found: {edge_id}[/red]")
        raise typer.Exit(1)
    console.print(f"[bold]Edge #{edge.edge_id}[/bold]: {edge.from_name} → {edge.to_name}")
    console.print(f"  Direction: {'positive' if edge.direction == 1 else 'negative'}")
    console.print(f"  Strength: {edge.strength:.2f}" if edge.strength else "  Strength: —")
    console.print(f"  Lag: {edge.lag_days}d")
    console.print(f"  5D Scores: d1={edge.d1_directness} d2={edge.d2_elasticity} d3={edge.d3_consistency} d4={edge.d4_speed} d5={edge.d5_uniqueness}")
    if edge.evidence_summary:
        console.print(f"  Evidence: {edge.evidence_summary}")


# ── Causal discover commands ─────────────────────────────────────────────────

@causal_app.command("discover")
def causal_discover(
    code: str = typer.Option(..., "--code", help="Stock code (e.g. 600219)"),
    event: str = typer.Option(..., "--event", help="Event description"),
    lookback: int = typer.Option(7, "--lookback", help="Days of price history"),
    news: str = typer.Option("", "--news", help="Manual news summaries (Phase 4: auto-fetch)"),
) -> None:
    """Discover causal paths for a holding event via LLM."""
    from investment.causal.discoverer import discover_causal_paths

    console.print(f"[dim]Discovering causal paths for {code}...[/dim]")
    try:
        paths = discover_causal_paths(
            event=event,
            holding_code=code,
            lookback_days=lookback,
            news_summaries=news or "",
        )
    except RuntimeError as exc:
        console.print(f"[red]LLM call failed: {exc}[/red]")
        raise typer.Exit(1)

    if not paths:
        console.print("[yellow]No causal paths discovered.[/yellow]")
        return

    _display_discovered_paths(paths)
    console.print(
        f"\n[green]✓[/green] {sum(len(p.edges) for p in paths)} pending edges from "
        f"{len(paths)} path(s) written. Use [bold]inv causal review[/bold] to approve."
    )


@causal_app.command("discover-auto")
def causal_discover_auto(
    volatility: float = typer.Option(5.0, "--volatility", help="Min volatility % to trigger"),
    lookback: int = typer.Option(3, "--lookback", help="Days to look back"),
) -> None:
    """Auto-scan holdings with recent moves > threshold and discover paths."""
    from investment.causal.discoverer import discover_auto

    console.print(f"[dim]Scanning holdings with >{volatility}% moves over {lookback}d...[/dim]")
    results = discover_auto(volatility_pct=volatility, lookback_days=lookback)

    if not results:
        console.print("[yellow]No volatile holdings found, or no paths discovered.[/yellow]")
        return

    total_paths = 0
    total_edges = 0
    for code, paths in results.items():
        console.print(f"\n[bold]{code}[/bold] ({len(paths)} path(s)):")
        _display_discovered_paths(paths)
        total_paths += len(paths)
        total_edges += sum(len(p.edges) for p in paths)

    console.print(
        f"\n[green]✓[/green] {total_edges} pending edges from "
        f"{total_paths} path(s) across {len(results)} holding(s)."
    )


def _display_discovered_paths(paths) -> None:
    """Render discovered paths as Rich Tables."""
    from investment.causal.models import EdgeScore5D

    for i, path in enumerate(paths):
        if path.narrative:
            console.print(f"  [dim]Path {i+1}: {path.narrative}[/dim]")

        # Nodes table
        node_table = Table(title=f"Path {i+1} — Nodes", show_header=True)
        node_table.add_column("Name")
        node_table.add_column("Type")
        node_table.add_column("Layer")
        node_table.add_column("New?")
        for n in path.nodes:
            node_table.add_row(
                n.name, n.node_type, n.layer,
                "[yellow]new[/yellow]" if n.is_new else "[dim]existing[/dim]",
            )
        console.print(node_table)

        # Edges table
        edge_table = Table(title=f"Path {i+1} — Edges")
        edge_table.add_column("From → To")
        edge_table.add_column("Dir")
        edge_table.add_column("Strength")
        edge_table.add_column("Confidence")
        edge_table.add_column("Evidence")
        for e in path.edges:
            scores = EdgeScore5D(
                d1_directness=e.d1_directness, d2_elasticity=e.d2_elasticity,
                d3_consistency=e.d3_consistency, d4_speed=e.d4_speed, d5_uniqueness=e.d5_uniqueness,
            )
            edge_table.add_row(
                f"{e.from_node_name} → {e.to_node_name}",
                "+" if e.direction == 1 else "−",
                f"{scores.composite_strength():.2f}",
                f"{e.confidence:.0%}",
                e.evidence_summary[:60] + ("..." if len(e.evidence_summary) > 60 else ""),
            )
        console.print(edge_table)


# ── Causal review commands ───────────────────────────────────────────────────

causal_review_app = typer.Typer(help="Review and approve/reject pending edges.", no_args_is_help=True)
causal_app.add_typer(causal_review_app, name="review")


@causal_review_app.callback(invoke_without_command=True)
def causal_review_interactive(
    ctx: typer.Context,
) -> None:
    """Interactive review of pending causal edges. [a]pprove / [r]eject / [m]odify / [s]kip / [q]uit."""
    if ctx.invoked_subcommand is not None:
        return

    from investment.causal.reviewer import Reviewer
    from investment.causal.models import EdgeScore5D

    rev = Reviewer()
    pending = rev.list_pending()

    if not pending:
        console.print("[green]No pending edges to review.[/green]")
        return

    console.print(f"[bold]Found {len(pending)} pending edge(s)[/bold]\n")

    for i, p in enumerate(pending):
        _display_pending_edge(p, i + 1, len(pending))

        choice = typer.prompt(
            "[a]pprove / [r]eject / [m]odify / [s]kip / [q]uit",
            default="s",
        ).strip().lower()

        if choice == "q":
            console.print("Quit review.")
            break
        elif choice == "a":
            try:
                eid = rev.approve(p.pending_id)
                console.print(f"  [green]✓[/green] Approved → edge #{eid}")
            except ValueError as exc:
                console.print(f"  [red]✗[/red] {exc}")
        elif choice == "r":
            reason = typer.prompt("  Reason", default="")
            rev.reject(p.pending_id, reason)
            console.print(f"  [yellow]✓[/yellow] Rejected")
        elif choice == "m":
            _interactive_modify(rev, p)
        elif choice == "s":
            console.print("  [dim]Skipped[/dim]")


def _display_pending_edge(p, idx: int, total: int) -> None:
    """Display a single pending edge with full details."""
    from investment.causal.models import EdgeScore5D

    scores = EdgeScore5D(
        d1_directness=p.d1_directness or 3,
        d2_elasticity=p.d2_elasticity or 3,
        d3_consistency=p.d3_consistency or 3,
        d4_speed=p.d4_speed or 3,
        d5_uniqueness=p.d5_uniqueness or 3,
    )

    console.print(f"[bold]── #{p.pending_id} ({idx}/{total}) ──[/bold]")
    console.print(f"  {p.from_node_name} → {p.to_node_name}")
    console.print(f"  Direction: {'+' if p.direction == 1 else '−'}{p.direction} | "
                  f"Confidence: {p.confidence:.0%}" if p.confidence else f"  Direction: {'+' if p.direction == 1 else '−'}")
    console.print(f"  Strength: {scores.composite_strength():.2f} | "
                  f"Lag: {p.lag_days}d | "
                  f"5D: {p.d1_directness}/{p.d2_elasticity}/{p.d3_consistency}/{p.d4_speed}/{p.d5_uniqueness}")
    if p.evidence_summary:
        console.print(f"  Evidence: {p.evidence_summary[:100]}")
    if p.triggered_by_event:
        console.print(f"  Trigger: {p.triggered_by_event[:80]}")
    if p.from_node_proposed_type:
        console.print(f"  [yellow]New node needed: {p.from_node_name} "
                      f"({p.from_node_proposed_type}/{p.from_node_proposed_layer})[/yellow]")
    if p.to_node_proposed_type:
        console.print(f"  [yellow]New node needed: {p.to_node_name} "
                      f"({p.to_node_proposed_type}/{p.to_node_proposed_layer})[/yellow]")


def _interactive_modify(rev, p) -> None:
    """Interactive field-by-field modification."""
    from investment.causal.models import EdgeScore5D

    console.print("  [dim]Enter new value or leave blank to keep current[/dim]")

    fields = {
        "d1": ("d1_directness", p.d1_directness),
        "d2": ("d2_elasticity", p.d2_elasticity),
        "d3": ("d3_consistency", p.d3_consistency),
        "d4": ("d4_speed", p.d4_speed),
        "d5": ("d5_uniqueness", p.d5_uniqueness),
        "direction": ("direction", p.direction),
        "lag_days": ("lag_days", p.lag_days),
    }

    overrides = {}
    for key, (col, current) in fields.items():
        val = typer.prompt(f"  {key} [{current}]", default="").strip()
        if val:
            overrides[key] = int(val) if key != "direction" else int(val)

    reason = typer.prompt("  Reason for modification", default="").strip()

    try:
        eid = rev.modify(p.pending_id, reason=reason, **overrides)
        console.print(f"  [green]✓[/green] Modified and approved → edge #{eid}")
    except ValueError as exc:
        console.print(f"  [red]✗[/red] {exc}")


@causal_review_app.command("list")
def causal_review_list() -> None:
    """List all pending edges awaiting review."""
    from investment.causal.reviewer import Reviewer
    from investment.causal.models import EdgeScore5D

    rev = Reviewer()
    pending = rev.list_pending()

    if not pending:
        console.print("[green]No pending edges.[/green]")
        return

    table = Table(title=f"Pending Edges ({len(pending)})")
    table.add_column("ID")
    table.add_column("From → To")
    table.add_column("Dir")
    table.add_column("Conf")
    table.add_column("Strength")
    table.add_column("Evidence")
    for p in pending:
        scores = EdgeScore5D(
            d1_directness=p.d1_directness or 3, d2_elasticity=p.d2_elasticity or 3,
            d3_consistency=p.d3_consistency or 3, d4_speed=p.d4_speed or 3,
            d5_uniqueness=p.d5_uniqueness or 3,
        )
        table.add_row(
            str(p.pending_id),
            f"{p.from_node_name} → {p.to_node_name}",
            "+" if p.direction == 1 else "−",
            f"{p.confidence:.0%}" if p.confidence else "—",
            f"{scores.composite_strength():.2f}",
            (p.evidence_summary or "")[:50],
        )
    console.print(table)
    console.print(f"\n[dim]Use [bold]inv causal review[/bold] for interactive mode.[/dim]")


@causal_review_app.command("approve")
def causal_review_approve(
    pending_id: int = typer.Argument(help="Pending edge ID to approve"),
) -> None:
    """Approve a pending edge — creates nodes and edge in the graph."""
    from investment.causal.reviewer import Reviewer

    rev = Reviewer()
    try:
        eid = rev.approve(pending_id)
        console.print(f"[green]✓[/green] Pending #{pending_id} approved → edge #{eid}")
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)


@causal_review_app.command("reject")
def causal_review_reject(
    pending_id: int = typer.Argument(help="Pending edge ID to reject"),
    reason: str = typer.Option("", "--reason", "-r", help="Reason for rejection"),
) -> None:
    """Reject a pending edge."""
    from investment.causal.reviewer import Reviewer

    rev = Reviewer()
    try:
        rev.reject(pending_id, reason)
        console.print(f"[yellow]✓[/yellow] Pending #{pending_id} rejected")
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)


@causal_review_app.command("modify")
def causal_review_modify(
    pending_id: int = typer.Argument(help="Pending edge ID to modify"),
    d1: int = typer.Option(0, "--d1", help="Directness (1-5)"),
    d2: int = typer.Option(0, "--d2", help="Elasticity (1-5)"),
    d3: int = typer.Option(0, "--d3", help="Consistency (1-5)"),
    d4: int = typer.Option(0, "--d4", help="Speed (1-5)"),
    d5: int = typer.Option(0, "--d5", help="Uniqueness (1-5)"),
    direction: int = typer.Option(0, "--direction", help="1=positive, -1=negative"),
    lag: int = typer.Option(-1, "--lag", help="Lag in days"),
    reason: str = typer.Option("", "--reason", "-r", help="Reason for modification"),
) -> None:
    """Modify a pending edge's scores then approve it."""
    from investment.causal.reviewer import Reviewer

    overrides = {}
    if d1: overrides["d1"] = d1
    if d2: overrides["d2"] = d2
    if d3: overrides["d3"] = d3
    if d4: overrides["d4"] = d4
    if d5: overrides["d5"] = d5
    if direction: overrides["direction"] = direction
    if lag >= 0: overrides["lag_days"] = lag

    if not overrides:
        console.print("[yellow]No modifications specified. Use --d1..--d5, --direction, --lag[/yellow]")
        raise typer.Exit(1)

    rev = Reviewer()
    try:
        eid = rev.modify(pending_id, reason=reason, **overrides)
        console.print(f"[green]✓[/green] Pending #{pending_id} modified + approved → edge #{eid}")
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)


# ── Causal scan commands ─────────────────────────────────────────────────────

@causal_app.command("scan")
def causal_scan(
    date: str = typer.Option("", "--date", help="Target date (YYYY-MM-DD, default today)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Fetch + classify without writing to DB"),
) -> None:
    """Run daily signal scan: fetch news → dedup → LLM classify → write signals."""
    from investment.causal.scanner import scan

    target = date or None
    console.print(f"[dim]Scanning news signals for {target or 'today'}...[/dim]")
    try:
        result = scan(date=target, dry_run=dry_run)
    except Exception as exc:
        console.print(f"[red]Scan failed: {exc}[/red]")
        raise typer.Exit(1)

    console.print(
        f"[green]✓[/green] fetched={result['fetched']} deduped={result['deduped']} "
        f"classified={result['classified']} signals_written={result['signals_written']} "
        f"nodes_updated={result['nodes_updated']}"
    )
    if dry_run:
        console.print("[yellow]Dry-run mode — nothing written to DB.[/yellow]")


# ── Causal lifecycle commands ─────────────────────────────────────────────────

causal_lifecycle_app = typer.Typer(help="Activation decay and lifecycle state transitions.", no_args_is_help=True)
causal_app.add_typer(causal_lifecycle_app, name="lifecycle")


@causal_lifecycle_app.command("update")
def causal_lifecycle_update() -> None:
    """Apply activation decay and lifecycle state transitions to all nodes."""
    from investment.causal.scanner import update_lifecycle

    console.print("[dim]Applying activation decay and lifecycle transitions...[/dim]")
    try:
        result = update_lifecycle()
    except Exception as exc:
        console.print(f"[red]Lifecycle update failed: {exc}[/red]")
        raise typer.Exit(1)

    console.print(
        f"[green]✓[/green] decayed={result['decayed']} dormant={result['dormant']} "
        f"archived={result['archived']} reactivated={result['reactivated']}"
    )


# ── Causal assess commands ────────────────────────────────────────────────────

@causal_app.command("assess")
def causal_assess(
    date: str = typer.Option("", "--date", help="Target date (YYYY-MM-DD, default today)"),
    code: str = typer.Option("", "--code", help="Assess a single holding code"),
    explain: bool = typer.Option(False, "--explain", help="Show full path details"),
) -> None:
    """Assess causal impact on holdings from today's news signals."""
    from investment.causal.assessor import assess_holdings

    target = date or None
    console.print(f"[dim]Assessing causal impact for {target or 'today'}...[/dim]")
    try:
        results = assess_holdings(
            date=target,
            holding_code=code or None,
        )
    except Exception as exc:
        console.print(f"[red]Assessment failed: {exc}[/red]")
        raise typer.Exit(1)

    if not results:
        console.print("[yellow]No assessments generated (no signals, no holdings, or all below L3).[/yellow]")
        return

    from rich.table import Table
    table = Table(title=f"Causal Impact Assessment — {target or dt_date.today().isoformat()}")
    table.add_column("Code")
    table.add_column("Name")
    table.add_column("Level")
    table.add_column("Direction")
    table.add_column("Score")
    table.add_column("Paths")

    dir_map = {"positive": "[green]+[/green]", "negative": "[red]−[/red]", "neutral": "[dim]0[/dim]"}
    level_color = {"L5": "red", "L4": "yellow", "L3": "dim"}

    for r in results:
        lvl = r["impact_level"]
        table.add_row(
            r["holding_code"],
            r["holding_name"],
            f"[{level_color.get(lvl, 'white')}]{lvl}[/{level_color.get(lvl, 'white')}]",
            dir_map.get(r["direction"], "?"),
            f"{r['impact_score']:.3f}",
            str(r["paths_count"]),
        )
    console.print(table)

    # Show narrative for each assessment
    for r in results:
        if r.get("narrative_md"):
            console.print(f"\n[bold]── {r['holding_code']} {r['holding_name']} ──[/bold]")
            console.print(r["narrative_md"])

    if explain and results:
        for r in results:
            if r.get("path_details"):
                console.print(f"\n[bold underline]{r['holding_code']} Path Details:[/bold underline]")
                for i, pd in enumerate(r["path_details"]):
                    seq = " → ".join(pd["node_sequence"])
                    console.print(f"  Path {i+1}: {seq}")
                    console.print(f"    Impact: {pd['impact_contribution']:.4f} | "
                                  f"Strengths: {pd['edge_strengths']} | "
                                  f"Signal: {pd.get('signal_title', '?')[:50]}")


@causal_app.command("graph")
def causal_graph(
    code: str = typer.Option(..., "--code", help="Holding code (e.g. 600219)"),
    format: str = typer.Option("mermaid", "--format", help="Output format: mermaid"),
    hops: int = typer.Option(3, "--hops", help="Number of hops from the holding node"),
) -> None:
    """Output the causal subgraph around a holding as Mermaid/JSON."""
    from investment.causal.repo import CausalRepo

    repo = CausalRepo()
    with repo.transaction():
        holding_node = None
        nodes = repo.list_nodes(layer="L3_holding")
        for n in nodes:
            if n.name.startswith(f"{code}-"):
                holding_node = n
                break

        if not holding_node:
            console.print(f"[red]No causal node found for {code}. Run inv causal discover first.[/red]")
            raise typer.Exit(1)

        sub = repo.get_subgraph(holding_node.name, hops=hops)

    if format == "mermaid":
        _render_mermaid(holding_node.name, sub["nodes"], sub["edges"])
    else:
        import json
        console.print(json.dumps({
            "nodes": [{"name": n.name, "type": n.node_type, "layer": n.layer,
                        "activation": n.activation_score, "state": n.lifecycle_state}
                       for n in sub["nodes"]],
            "edges": [{"from": e.from_name, "to": e.to_name, "strength": e.strength,
                        "direction": e.direction}
                      for e in sub["edges"]],
        }, ensure_ascii=False, indent=2))


def _render_mermaid(center_name: str, nodes, edges) -> None:
    """Render a causal subgraph as a Mermaid flowchart."""
    lines = ["```mermaid", "graph LR"]
    node_ids: dict[str, str] = {}
    layer_colors = {
        "L0_geopolitical": "#ff6b6b",
        "L1_macro": "#ffd93d",
        "L2_industry": "#6bcb77",
        "L3_holding": "#4d96ff",
    }

    for i, n in enumerate(nodes):
        nid = f"N{i}"
        node_ids[n.name] = nid
        color = layer_colors.get(n.layer, "#ccc")
        label = n.name[:20]
        style = "stroke-width:3px" if n.name == center_name else ""
        lines.append(f'    {nid}["{label}"]:::layer_{n.layer.split("_")[0]}')

    # Class definitions for color
    for layer, color in layer_colors.items():
        lk = layer.split("_")[0]
        lines.append(f"    classDef layer_{lk} fill:{color},stroke:#333,color:#000")
    if center_name in node_ids:
        lines.append(f"    style {node_ids[center_name]} stroke-width:4px,stroke-dasharray:0")

    for e in edges:
        if e.from_name in node_ids and e.to_name in node_ids:
            arrow = "-->"
            if e.direction == -1:
                arrow = "-.->|neg|"
            label = f"|{e.strength:.1f}|" if e.strength else ""
            lines.append(f"    {node_ids[e.from_name]} {arrow}{label} {node_ids[e.to_name]}")

    lines.append("```")
    console.print("\n".join(lines))


# ── Causal daily orchestration ───────────────────────────────────────────────

@causal_app.command("daily")
def causal_daily(
    date: str = typer.Option("", "--date", help="Target date (YYYY-MM-DD, default today)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Scan + assess without writing"),
) -> None:
    """Run full daily causal pipeline: scan → lifecycle → assess."""
    from investment.causal.scanner import scan, update_lifecycle
    from investment.causal.assessor import assess_holdings

    target = date or None

    # 1. Scan
    console.print("[bold]1/3[/bold] Scanning news signals...")
    try:
        scan_result = scan(date=target, dry_run=dry_run)
        console.print(
            f"  fetched={scan_result['fetched']} deduped={scan_result['deduped']} "
            f"classified={scan_result['classified']} signals={scan_result['signals_written']}"
        )
    except Exception as exc:
        console.print(f"  [red]Scan failed: {exc}[/red]")

    # 2. Lifecycle
    console.print("[bold]2/3[/bold] Updating lifecycle...")
    try:
        lc_result = update_lifecycle()
        console.print(
            f"  decayed={lc_result['decayed']} dormant={lc_result['dormant']} "
            f"archived={lc_result['archived']} reactivated={lc_result['reactivated']}"
        )
    except Exception as exc:
        console.print(f"  [red]Lifecycle update failed: {exc}[/red]")

    # 3. Assess
    console.print("[bold]3/3[/bold] Assessing causal impact on holdings...")
    try:
        results = assess_holdings(date=target)
        if results:
            for r in results:
                console.print(
                    f"  {r['holding_code']} {r['holding_name']}: "
                    f"[{r['impact_level']}] {r['direction']} "
                    f"score={r['impact_score']:.3f} paths={r['paths_count']}"
                )
        else:
            console.print("  [dim]No L3+ assessments generated[/dim]")
    except Exception as exc:
        console.print(f"  [red]Assessment failed: {exc}[/red]")

    console.print("[green]✓[/green] Daily causal pipeline complete")


# ── Causal lifecycle review command ──────────────────────────────────────────

@causal_lifecycle_app.command("review")
def causal_lifecycle_review(
    days: int = typer.Option(90, "--days", help="Look back window in days"),
) -> None:
    """List nodes whose lifecycle state changed within the last N days."""
    from datetime import date as dt_date, timedelta
    from investment.causal.repo import CausalRepo

    repo = CausalRepo()
    cutoff = (dt_date.today() - timedelta(days=days)).isoformat()

    with repo.transaction():
        # Check review log for lifecycle changes
        rows = repo._conn.execute(
            """SELECT n.name, n.node_type, n.layer, n.lifecycle_state, n.activation_score,
                      n.updated_at
               FROM causal_nodes n
               WHERE n.updated_at >= ?
                 AND n.lifecycle_state IN ('dormant','archived')
               ORDER BY n.updated_at DESC""",
            (cutoff,),
        ).fetchall()

    if not rows:
        console.print(f"[green]No lifecycle changes in the last {days} days.[/green]")
        return

    from rich.table import Table
    table = Table(title=f"Lifecycle Changes (last {days} days)")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Layer")
    table.add_column("State")
    table.add_column("Activation")
    table.add_column("Updated")

    state_color = {"dormant": "yellow", "archived": "red"}
    for r in rows:
        state = r["lifecycle_state"]
        table.add_row(
            r["name"], r["node_type"], r["layer"],
            f"[{state_color.get(state, 'white')}]{state}[/{state_color.get(state, 'white')}]",
            f"{r['activation_score']:.1f}" if r["activation_score"] else "—",
            r["updated_at"][:10] if r["updated_at"] else "—",
        )
    console.print(table)


# ── Profile commands ──────────────────────────────────────────────────────────

@profile_app.command("show")
def profile_show() -> None:
    """Show the current investor profile and A/B/C allocation."""
    from investment.agent_tools.onboarding import get_latest_profile, get_active_goals, _RISK_LABELS
    profile = get_latest_profile()
    if not profile:
        console.print("[yellow]尚未设置投资画像。运行 `inv profile set` 开始配置。[/yellow]")
        raise typer.Exit(1)

    console.print(f"\n[bold]投资画像[/bold]（创建于 {profile['created_at'][:10]}）")
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("字段", style="dim")
    table.add_column("值")
    risk_label = _RISK_LABELS.get(profile["risk_tolerance"], profile["risk_tolerance"])
    table.add_row("风险承受", risk_label)
    table.add_row("最大回撤容忍", f"{profile['max_drawdown_tolerance']}%")
    table.add_row("投资期限", f"{profile['horizon_years']} 年")
    table.add_row("可投资金额", f"¥{profile['investable_capital']:,.0f}")
    console.print(table)

    console.print(f"\n[bold]A/B/C 配置方案[/bold]")
    alloc_table = Table()
    alloc_table.add_column("档位")
    alloc_table.add_column("用途")
    alloc_table.add_column("比例")
    alloc_table.add_column("金额")
    capital = profile["investable_capital"]
    alloc_table.add_row("A 档", "生活保障金（货币/债券）",
                        f"{profile['a_ratio']:.0%}", f"¥{capital * profile['a_ratio']:,.0f}")
    alloc_table.add_row("B 档", "核心 ETF（宽基指数）",
                        f"{profile['b_ratio']:.0%}", f"¥{capital * profile['b_ratio']:,.0f}")
    alloc_table.add_row("C 档", "主动选股",
                        f"{profile['c_ratio']:.0%}", f"¥{capital * profile['c_ratio']:,.0f}")
    console.print(alloc_table)

    goals = get_active_goals(profile["id"])
    if goals:
        console.print(f"\n[bold]投资目标[/bold]")
        for g in goals:
            console.print(f"  目标年化：{g['target_annual_return']}%", end="")
            if g["target_amount"]:
                console.print(f"  目标金额：¥{g['target_amount']:,.0f}", end="")
            if g["deadline"]:
                console.print(f"  截止：{g['deadline']}", end="")
            console.print()


@profile_app.command("set")
def profile_set(
    capital: float = typer.Option(..., "--capital", "-c", help="可投资金额（元）"),
    risk: str = typer.Option(..., "--risk", "-r", help="风险承受：conservative/moderate/aggressive"),
    horizon: int = typer.Option(..., "--horizon", "-h", help="投资期限（年）"),
    target_return: float = typer.Option(..., "--target-return", "-t", help="目标年化收益率（%）"),
    max_dd: float = typer.Option(20.0, "--max-dd", help="最大回撤容忍度（%）"),
    target_amount: Optional[float] = typer.Option(None, "--target-amount", help="目标金额（元）"),
    deadline: Optional[str] = typer.Option(None, "--deadline", help="目标截止日期 YYYY-MM-DD"),
    notes: str = typer.Option("", "--notes", "-n", help="备注"),
) -> None:
    """Create or update investor profile and generate A/B/C allocation."""
    from investment.agent_tools.onboarding import ProfileInput, run_onboarding
    inp = ProfileInput(
        investable_capital=capital,
        risk_tolerance=risk,
        horizon_years=horizon,
        target_annual_return=target_return,
        max_drawdown_tolerance=max_dd,
        target_amount=target_amount,
        deadline=deadline,
        notes=notes,
    )
    result = run_onboarding(inp)
    if result.success:
        console.print(result.human_message)
    else:
        console.print(f"[red]{result.human_message}[/red]")
        raise typer.Exit(1)


@profile_app.command("reset")
def profile_reset(
    confirm: bool = typer.Option(False, "--confirm", help="确认重置（必须显式传入）"),
) -> None:
    """Delete all profile data (user_profile, goals, asset_inventory). Requires --confirm."""
    if not confirm:
        console.print("[yellow]危险操作！请加 --confirm 参数确认删除所有画像数据。[/yellow]")
        raise typer.Exit(1)
    from investment.core.db import transaction as db_tx
    with db_tx() as conn:
        conn.execute("DELETE FROM asset_inventory")
        conn.execute("DELETE FROM goals")
        conn.execute("DELETE FROM user_profile")
    console.print("[green]✓ 投资画像已清空。运行 `inv profile set` 重新配置。[/green]")


# ── Risk commands ─────────────────────────────────────────────────────────────

@risk_app.command("compute")
def risk_compute(
    lookback: int = typer.Option(60, "--lookback", "-l", help="历史回溯天数"),
    no_save: bool = typer.Option(False, "--no-save", help="不写入数据库"),
) -> None:
    """Compute portfolio risk metrics and save to DB."""
    from investment.agent_tools.risk_engine import run_risk_engine
    console.print(f"[dim]计算组合风险指标（回溯 {lookback} 天）...[/dim]")
    report = run_risk_engine(lookback_days=lookback, save=not no_save)
    console.print(report.human_message)
    if not no_save:
        console.print(f"\n[dim]✓ 风险指标已保存（{report.calc_date}）[/dim]")


@risk_app.command("show")
def risk_show(
    lookback: int = typer.Option(60, "--lookback", "-l", help="历史回溯天数"),
) -> None:
    """Show latest risk metrics from DB, or compute if not available."""
    from investment.core.db import connect as db_connect
    from investment.agent_tools.risk_engine import run_risk_engine
    conn = db_connect()
    row = conn.execute(
        "SELECT * FROM risk_metrics ORDER BY calc_date DESC, id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if row:
        from investment.agent_tools.translator import fmt_pct
        console.print(f"\n[bold]最近一次风险计算[/bold]（{row['calc_date']}，回溯 {row['lookback_days']} 天）")
        table = Table()
        table.add_column("指标")
        table.add_column("数值")
        table.add_row("年化波动率", fmt_pct(row["portfolio_vol"] or 0))
        table.add_row("最大回撤", fmt_pct(row["max_drawdown"] or 0))
        table.add_row("回撤持续", f"{row['dd_duration_days'] or 0} 个交易日")
        table.add_row("95% VaR（单日）", fmt_pct(row["var_95"] or 0))
        table.add_row("Sharpe 比率", f"{row['sharpe_ratio'] or 0:.2f}")
        console.print(table)
        console.print("\n[dim]运行 `inv risk compute` 更新指标[/dim]")
    else:
        console.print("[yellow]尚无风险数据，正在计算...[/yellow]")
        report = run_risk_engine(lookback_days=lookback, save=True)
        console.print(report.human_message)


# ── Attribution commands ──────────────────────────────────────────────────────

@attribution_app.command("compute")
def attribution_compute(
    start: Optional[str] = typer.Option(None, "--start", "-s", help="起始日期 YYYY-MM-DD（默认30天前）"),
    end: Optional[str] = typer.Option(None, "--end", "-e", help="截止日期 YYYY-MM-DD（默认今日）"),
    benchmark: str = typer.Option("000300", "--benchmark", "-b", help="基准指数代码（默认沪深300）"),
    no_save: bool = typer.Option(False, "--no-save", help="不写入数据库"),
) -> None:
    """Compute performance attribution (BHB decomposition) vs benchmark."""
    from investment.agent_tools.attribution import run_attribution
    console.print("[dim]计算业绩归因...[/dim]")
    result = run_attribution(
        period_start=start, period_end=end,
        benchmark_code=benchmark, save=not no_save,
    )
    console.print(result.human_message)
    if not no_save and not result.insufficient_data:
        console.print(f"\n[dim]✓ 归因结果已保存（{result.period_start} ~ {result.period_end}）[/dim]")


@attribution_app.command("show")
def attribution_show() -> None:
    """Show the most recent attribution result from DB."""
    from investment.core.db import connect as db_connect
    from investment.agent_tools.translator import fmt_pct
    conn = db_connect()
    row = conn.execute(
        "SELECT * FROM performance_attribution ORDER BY period_end DESC, id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if not row:
        console.print("[yellow]尚无归因数据。运行 `inv attribution compute` 生成。[/yellow]")
        raise typer.Exit(1)

    console.print(f"\n[bold]最近一次业绩归因[/bold]（{row['period_start']} ~ {row['period_end']}）")
    table = Table()
    table.add_column("指标")
    table.add_column("数值")
    table.add_row("组合收益", fmt_pct(row["total_return"] or 0))
    table.add_row(f"基准（{row['benchmark_code']}）", fmt_pct(row["benchmark_return"] or 0))
    excess = row["excess_return"] or 0
    table.add_row("超额收益", f"{'+' if excess >= 0 else ''}{fmt_pct(excess)}")
    table.add_row("择时贡献", fmt_pct(row["timing_contrib"] or 0))
    table.add_row("选股贡献", fmt_pct(row["selection_contrib"] or 0))
    table.add_row("配置贡献", fmt_pct(row["allocation_contrib"] or 0))
    console.print(table)
    console.print("\n[dim]运行 `inv attribution compute` 更新[/dim]")


# ── Causal insight commands (Phase 6 additions) ───────────────────────────────

@causal_app.command("insight")
def causal_insight(
    date: Optional[str] = typer.Option(None, "--date", "-d", help="日期 YYYY-MM-DD（默认今日）"),
    code: Optional[str] = typer.Option(None, "--code", "-c", help="只查看指定股票代码"),
) -> None:
    """Show causal insights for today's holdings (zero graph operations required)."""
    from investment.agent_tools.causal_facade import run_causal_insight
    report = run_causal_insight(as_of=date, holding_code=code)
    console.print(report.human_message)


@causal_app.command("validate")
def causal_validate(
    assessment_id: int = typer.Argument(..., help="评估记录 ID"),
    status: str = typer.Option(..., "--status", "-s", help="confirmed | refuted | open"),
    reason: str = typer.Option("", "--reason", "-r", help="更新原因"),
) -> None:
    """Update the validation status of a causal assessment (confirmed/refuted/open)."""
    from investment.agent_tools.causal_facade import update_validation_status
    ok = update_validation_status(assessment_id, status, reason)
    if ok:
        console.print(f"[green]✓ 评估 #{assessment_id} 状态已更新为 {status}[/green]")
    else:
        console.print(f"[red]更新失败：ID {assessment_id} 不存在或状态值无效[/red]")
        raise typer.Exit(1)


# ── Calendar commands ─────────────────────────────────────────────────────────

@calendar_app.command("show")
def calendar_show(
    period: str = typer.Option("week", "--period", "-p", help="today|week|month|quarter|year"),
) -> None:
    """Show investment calendar tasks for the given period."""
    from investment.agent_tools.calendar import run_calendar
    report = run_calendar(period=period)
    console.print(report.human_message)


@calendar_app.command("add")
def calendar_add(
    title: str = typer.Argument(..., help="任务标题"),
    due: str = typer.Option(..., "--due", "-d", help="截止日期 YYYY-MM-DD"),
    category: str = typer.Option("custom", "--category", "-c", help="cooldown|earnings|rebalance|monthly|quarterly|annual|custom"),
    priority: str = typer.Option("medium", "--priority", "-p", help="high|medium|low"),
    code: Optional[str] = typer.Option(None, "--code", help="相关股票代码"),
    notes: Optional[str] = typer.Option(None, "--notes", "-n", help="备注"),
) -> None:
    """Add a task to the investment calendar."""
    from investment.agent_tools.calendar import create_task
    task_id = create_task(title, category, due, priority, code, notes)
    console.print(f"[green]✓ 任务已添加（ID: {task_id}）：{title}，截止 {due}[/green]")


@calendar_app.command("done")
def calendar_done(
    task_id: int = typer.Argument(..., help="任务 ID"),
    notes: str = typer.Option("", "--notes", "-n", help="完成备注"),
) -> None:
    """Mark a calendar task as done."""
    from investment.agent_tools.calendar import complete_task
    ok = complete_task(task_id, notes)
    if ok:
        console.print(f"[green]✓ 任务 #{task_id} 已完成[/green]")
    else:
        console.print(f"[red]任务 #{task_id} 不存在[/red]")
        raise typer.Exit(1)


# ── Cost commands ─────────────────────────────────────────────────────────────

@cost_app.command("calc")
def cost_calc(
    code: str = typer.Argument(..., help="股票代码"),
    shares: float = typer.Option(..., "--shares", "-s", help="股数"),
    price: float = typer.Option(..., "--price", "-p", help="价格"),
    side: str = typer.Option(..., "--side", help="BUY|SELL"),
    save: bool = typer.Option(False, "--save", help="保存到数据库"),
) -> None:
    """Calculate transaction cost for a trade."""
    from investment.agent_tools.cost_calculator import calc_cost, save_cost_log
    breakdown = calc_cost(code, shares, price, side)
    console.print(breakdown.human_message)
    if save:
        save_cost_log(breakdown)
        console.print("[dim]✓ 成本记录已保存[/dim]")


# ── Behavior commands ─────────────────────────────────────────────────────────

@behavior_app.command("check")
def behavior_check(
    lookback: int = typer.Option(90, "--lookback", "-l", help="回溯天数"),
) -> None:
    """Run a behavioral bias check on recent trading activity."""
    from investment.agent_tools.behavior_guard import run_behavior_check
    report = run_behavior_check(lookback_days=lookback)
    console.print(report.human_message)


@behavior_app.command("log")
def behavior_log(
    decision_type: str = typer.Argument(..., help="BUY|SELL|HOLD|PASS"),
    reason: str = typer.Option(..., "--reason", "-r", help="决策理由"),
    code: Optional[str] = typer.Option(None, "--code", "-c", help="相关股票代码"),
    emotion: Optional[str] = typer.Option(None, "--emotion", "-e", help="当前情绪状态"),
) -> None:
    """Log a decision and check for behavioral biases."""
    from investment.agent_tools.behavior_guard import log_decision
    journal_id, biases = log_decision(decision_type, reason, code, emotion)
    console.print(f"[green]✓ 决策已记录（ID: {journal_id}）[/green]")
    if biases:
        console.print(f"\n[yellow]⚠️ 检测到 {len(biases)} 个行为偏差：[/yellow]")
        for b in biases:
            console.print(f"  - {b.bias_label}：{b.evidence}")
            console.print(f"    所以你该做什么：{b.action}")
    else:
        console.print("[green]✓ 未检测到明显行为偏差[/green]")


# ---- knowledge notes (边用边学) ----

@notes_app.command("append")
def notes_append(
    concept: str = typer.Option(..., "--concept", "-c", help="概念名称"),
    explanation: str = typer.Option(..., "--explanation", "-e", help="通俗解释"),
    example: str = typer.Option("", "--example", "-x", help="实际案例"),
    summary: str = typer.Option("", "--summary", "-s", help="一句话总结"),
) -> None:
    """Append a new concept to the learning notes (auto-dedup)."""
    from investment.agent_tools.knowledge_notes import append_concept

    success, msg = append_concept(
        concept=concept,
        explanation=explanation,
        example=example,
        summary=summary,
    )
    if success:
        console.print(f"[green]✓ {msg}[/green]")
    else:
        console.print(f"[yellow]⚠ {msg}[/yellow]")


@notes_app.command("search")
def notes_search(
    query: str = typer.Argument(..., help="搜索关键词"),
) -> None:
    """Search the learning notes for a concept."""
    from investment.agent_tools.knowledge_notes import search_notes

    result = search_notes(query)
    console.print(result)


@notes_app.command("read")
def notes_read() -> None:
    """Read the full learning notes file."""
    from investment.agent_tools.knowledge_notes import read_notes

    content = read_notes()
    console.print(content)


# ═══════════════════════════════════════════════════════════════════════════════
# inv agent  (v3 orchestrator)
# ═══════════════════════════════════════════════════════════════════════════════

@agent_app.command("run")
def agent_run(
    mode: str = typer.Option("premarket", "--mode", "-m",
                             help="premarket | postmarket | manual"),
    no_save: bool = typer.Option(False, "--no-save", help="Skip writing to DB"),
) -> None:
    """Run the full agent orchestration loop and print today's brief."""
    from investment.agent_orchestrator.runner import run as orch_run
    from investment.agent_orchestrator.operating_state import compute_and_save
    from investment.agent_orchestrator.brief import generate_brief, format_brief_text

    valid_modes = ("premarket", "postmarket", "manual")
    if mode not in valid_modes:
        console.print(f"[red]--mode must be one of {valid_modes}[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]⚙ Agent run[/bold] mode={mode} …")

    result = orch_run(mode=mode, save_log=not no_save)

    # Report module outcomes
    modules = [
        ("exec_monitor", result.exec_monitor),
        ("position",     result.position),
        ("risk",         result.risk),
        ("attribution",  result.attribution),
        ("calendar",     result.calendar),
        ("causal",       result.causal),
    ]
    for name, mod in modules:
        if mod.ok:
            console.print(f"  [green]✓[/green] {name}")
        else:
            console.print(f"  [yellow]⚠[/yellow] {name}: {mod.error[:80]}")

    # Compute + save operating state
    state = compute_and_save(result, db_path=None)

    # Generate and print brief
    brief = generate_brief(result, state)
    console.print("")
    console.print(format_brief_text(brief))


@agent_app.command("brief")
def agent_brief() -> None:
    """Print today's brief from DB state (no re-run)."""
    from investment.agent_orchestrator.brief import DailyBrief, _build_human_message
    from investment.agent_orchestrator.operating_state import OperatingState
    from investment.core.db import connect
    from datetime import date

    today = date.today().isoformat()
    conn = connect()

    # Read operating state from DB
    row = conn.execute(
        "SELECT * FROM daily_operating_state WHERE state_date=?", (today,)
    ).fetchone()
    conn.close()

    if not row:
        console.print(
            f"[yellow]今日（{today}）尚无运行记录。请先执行 `inv agent run`。[/yellow]"
        )
        raise typer.Exit(1)

    state = OperatingState(
        state_date=row["state_date"],
        health_light=row["health_light"],
        state_label=row["state_label"],
        executable_count=row["executable_count"],
        confirm_count=row["confirm_count"],
        monitor_count=row["monitor_count"],
        blocked_count=row["blocked_count"],
        critical_count=row["critical_count"],
        warning_count=row["warning_count"],
        top_message=row["top_message"],
        evidence_json=row["evidence_json"],
    )

    from investment.agent_orchestrator.brief import (
        _get_tasks_from_db, _get_breach_changes,
        _LIGHT_LABELS, _fmt_value, DailyBrief,
    )
    executable, confirm, monitor = _get_tasks_from_db(None)
    new_breaches, resolved = _get_breach_changes(None)

    brief = DailyBrief(
        brief_date=today,
        health_light=state.health_light,
        state_label=state.state_label,
        executable_count=state.executable_count or len(executable),
        confirm_count=state.confirm_count or len(confirm),
        monitor_count=state.monitor_count or len(monitor),
        executable_tasks=executable[:10],
        confirm_tasks=confirm[:10],
        monitor_tasks=monitor[:10],
        new_breaches=new_breaches,
        resolved_breaches=resolved,
        next_action=executable[0]["title"] if executable else "",
        next_command=executable[0].get("command", "") if executable else "",
    )
    console.print(_build_human_message(brief))


@agent_app.command("tasks")
def agent_tasks(
    layer: str = typer.Option("", "--layer", "-l",
                              help="executable | confirm | monitor | blocked | info"),
    all_: bool = typer.Option(False, "--all", "-a", help="Include done/skipped"),
) -> None:
    """List today's agent-generated tasks from task_calendar."""
    from investment.core.db import connect
    from datetime import date
    import json as _json

    today = date.today().isoformat()
    conn = connect()

    status_filter = "" if all_ else "AND status NOT IN ('done','skipped')"
    layer_filter  = f"AND decision_layer='{layer}'" if layer else ""

    rows = conn.execute(
        f"""SELECT id, title, status, priority, decision_layer,
                   related_code, due_date, suggested_command, source_module
            FROM task_calendar
            WHERE due_date <= date(?, '+7 days')
            {status_filter}
            {layer_filter}
            ORDER BY
              CASE decision_layer
                WHEN 'executable' THEN 0 WHEN 'confirm' THEN 1
                WHEN 'monitor'    THEN 2 WHEN 'blocked' THEN 3
                ELSE 4 END,
              CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
              due_date""",
        (today,),
    ).fetchall()
    conn.close()

    if not rows:
        console.print("[dim]No tasks found.[/dim]")
        return

    _LAYER_COLORS = {
        "executable": "red", "confirm": "yellow",
        "monitor": "blue",  "blocked": "dim",  "info": "white",
    }
    table = Table(title=f"Agent Tasks — {today}")
    table.add_column("ID",      style="dim", width=4)
    table.add_column("Layer",   width=12)
    table.add_column("Pri",     width=4)
    table.add_column("Title",   min_width=30)
    table.add_column("Code",    width=8)
    table.add_column("Due",     width=11)
    table.add_column("Command", overflow="fold")

    for r in rows:
        lyr = r["decision_layer"] or "monitor"
        color = _LAYER_COLORS.get(lyr, "white")
        table.add_row(
            str(r["id"]),
            f"[{color}]{lyr}[/{color}]",
            r["priority"] or "",
            r["title"],
            r["related_code"] or "",
            r["due_date"] or "",
            r["suggested_command"] or "",
        )
    console.print(table)




# ═══════════════════════════════════════════════════════════════════════════════
# inv goal  (v3 — annual goal progress)
# ═══════════════════════════════════════════════════════════════════════════════

@goal_app.command("compute")
def goal_compute() -> None:
    """Compute YTD goal progress and write to goal_progress table."""
    from investment.agent_tools.goal_engine import run_goal_engine
    result = run_goal_engine()
    console.print(result.human_message)
    if result.insufficient_data:
        console.print("[yellow]⚠ insufficient_data — some fields unavailable[/yellow]")
    else:
        console.print("[green]✓ goal_progress updated[/green]")


@goal_app.command("show")
def goal_show() -> None:
    """Show latest goal_progress record from DB."""
    from investment.core.db import connect
    conn = connect()
    row = conn.execute(
        "SELECT * FROM goal_progress ORDER BY progress_date DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if not row:
        console.print("[yellow]No goal_progress data. Run `inv goal compute` first.[/yellow]")
        raise typer.Exit(1)

    table = Table(title=f"Goal Progress — {row['progress_date']}")
    table.add_column("Metric")
    table.add_column("Value")

    def _fmt(v, pct=True):
        if v is None:
            return "N/A"
        return f"{float(v)*100:+.2f}%" if pct else str(v)

    table.add_row("年度目标收益率", _fmt(row["target_annual_return"]))
    table.add_row("实际 YTD", _fmt(row["actual_ytd_return"]))
    table.add_row("应达 YTD（线性）", _fmt(row["target_ytd_return"]))
    table.add_row("进度差距", _fmt(row["progress_gap"]))
    table.add_row("剩余所需年化", _fmt(row["required_return_remaining"]))
    table.add_row("最大回撤", _fmt(row["max_drawdown"]))
    table.add_row("风险预算使用", f"{float(row['risk_budget_used'])*100:.0f}%" if row["risk_budget_used"] is not None else "N/A")
    table.add_row("沪深300 YTD", _fmt(row["benchmark_return_ytd"]))
    table.add_row("组合总值", f"{float(row['portfolio_value'])/1e4:.1f}万" if row["portfolio_value"] else "N/A")
    console.print(table)


# ═══════════════════════════════════════════════════════════════════════════════
# inv health  (v3 — position health scoring)
# ═══════════════════════════════════════════════════════════════════════════════

@health_app.command("compute")
def health_compute() -> None:
    """Compute per-holding health scores and write to position_health table."""
    from investment.agent_tools.position_health import run_position_health, build_health_summary
    records = run_position_health()
    if not records:
        console.print("[yellow]No holdings found.[/yellow]")
        return
    console.print(build_health_summary(records))
    console.print(f"[green]✓ {len(records)} records written to position_health[/green]")


@health_app.command("show")
def health_show() -> None:
    """Show latest position_health records from DB."""
    from investment.core.db import connect
    conn = connect()
    rows = conn.execute(
        """SELECT ph.*, i.code, i.name, i.tranche
           FROM position_health ph
           JOIN instruments i ON i.id = ph.instrument_id
           WHERE ph.calc_date = (SELECT MAX(calc_date) FROM position_health)
           ORDER BY ph.health_score ASC""",
    ).fetchall()
    conn.close()

    if not rows:
        console.print("[yellow]No position_health data. Run `inv health compute` first.[/yellow]")
        raise typer.Exit(1)

    table = Table(title=f"Position Health — {rows[0]['calc_date']}")
    table.add_column("Code", width=8)
    table.add_column("Name", min_width=10)
    table.add_column("Label", width=12)
    table.add_column("Score", width=6)
    table.add_column("PnL%", width=8)
    table.add_column("Drawdown%", width=10)
    table.add_column("Weight%", width=8)
    table.add_column("Action", overflow="fold")

    _LABEL_COLORS = {
        "act": "red", "review": "yellow", "watch": "blue",
        "healthy": "green", "unknown": "dim", "insufficient_data": "dim",
    }
    for r in rows:
        label = r["health_label"] or "unknown"
        color = _LABEL_COLORS.get(label, "white")
        def _p(v, mult=100):
            return f"{float(v)*mult:+.1f}%" if v is not None else "N/A"
        table.add_row(
            r["code"],
            r["name"],
            f"[{color}]{label}[/{color}]",
            f"{float(r['health_score']):.0f}" if r["health_score"] else "N/A",
            _p(r["pnl_pct"]),
            _p(r["drawdown_pct"]),
            _p(r["weight_total"]),
            r["suggested_action"] or "",
        )
    console.print(table)


if __name__ == "__main__":
    app()
