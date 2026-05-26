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
app.add_typer(migrate_app, name="migrate")
app.add_typer(data_app, name="data")
app.add_typer(snapshot_app, name="snapshot")


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


if __name__ == "__main__":
    app()
