"""`stampan` command-line entry point. Commands are added phase by phase."""

from __future__ import annotations

import typer

from stampan import __version__

app = typer.Typer(
    name="stampan",
    help="Calibrated measurement of postage stamps from flatbed scans.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print the stampan version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
