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
app.add_typer(migrate_app, name="migrate")
app.add_typer(data_app, name="data")
app.add_typer(snapshot_app, name="snapshot")
app.add_typer(dashboard_app, name="dashboard")
app.add_typer(thesis_app, name="thesis")


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


if __name__ == "__main__":
    app()
