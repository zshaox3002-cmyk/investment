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
app.add_typer(migrate_app, name="migrate")
app.add_typer(data_app, name="data")


@app.command()
def version() -> None:
    """Show version and DB path."""
    console.print(f"[bold]inv[/bold] v{__version__}")
    console.print(f"db: {DB_PATH}")


@migrate_app.command("run")
def migrate_run(force: bool = typer.Option(False, "--force", help="Re-apply schema")) -> None:
    """Initialize or migrate the SQLite database."""
    version = db.init_db(force=force)
    console.print(f"[green]ok[/green] schema_version={version} db={DB_PATH}")


@migrate_app.command("verify")
def migrate_verify() -> None:
    """Verify DB structure (placeholder until D2 implements full reconciliation)."""
    tables = db.list_tables()
    console.print(f"[green]ok[/green] tables/views: {len(tables)}")
    for name in tables:
        console.print(f"  - {name}")


@data_app.command("tables")
def data_tables() -> None:
    """List tables and views."""
    rows = db.list_tables()
    table = Table(title="DB objects")
    table.add_column("name")
    for name in rows:
        table.add_row(name)
    console.print(table)


if __name__ == "__main__":
    app()
