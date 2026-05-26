"""Typer CLI entry point.

Run as `inv ...` after `pip install -e .` or `python -m investment.cli ...`.
"""
from __future__ import annotations

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
app.add_typer(migrate_app, name="migrate")
app.add_typer(data_app, name="data")
app.add_typer(snapshot_app, name="snapshot")
app.add_typer(dashboard_app, name="dashboard")
app.add_typer(thesis_app, name="thesis")
app.add_typer(trade_app, name="trade")
app.add_typer(exec_app, name="exec")
app.add_typer(candidate_app, name="candidate")
app.add_typer(review_app, name="review")


@app.command()
def version() -> None:
    """Show version and DB path."""
    console.print(f"[bold]inv[/bold] v{__version__}")
    console.print(f"db: {DB_PATH}")


@migrate_app.command("run")
def migrate_run(force: bool = typer.Option(False, "--force", help="Re-apply schema")) -> None:
    """Run all 8 migration steps (idempotent)."""
    from investment.migration.runner import run_all
    run_all()


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


if __name__ == "__main__":
    app()
