"""Typer CLI entry point.

Run as `inv ...` after `pip install -e .` or `python -m investment.cli ...`.
"""
from __future__ import annotations

from datetime import date as dt_date

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


if __name__ == "__main__":
    app()
