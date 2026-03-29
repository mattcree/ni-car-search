from __future__ import annotations

import re
import sys

from .base import Listing

try:
    from rich.console import Console
    from rich.table import Table

    RICH = True
except ImportError:
    RICH = False

_console = Console() if RICH else None


def _parse_price(price: str) -> float:
    s = re.sub(r"[^\d.]", "", price)
    try:
        return float(s)
    except ValueError:
        return float("inf")


def emit(source: str, listings: list[Listing]):
    """Print a batch of results as they arrive from a scraper."""
    if not listings:
        return

    listings.sort(key=lambda r: _parse_price(r.price))

    if RICH:
        _console.print(f"  [cyan]{source}[/cyan]: [bold]{len(listings)}[/bold] listings found")
        for r in listings:
            _console.print(
                f"    [green]{r.price:>8s}[/green]  {r.year}  {r.mileage:>14s}  {r.location:20s}  {r.title}"
            )
            _console.print(f"           [dim]{r.link}[/dim]")
        _console.print()
    else:
        print(f"  {source}: {len(listings)} listings found")
        for r in listings:
            print(f"    {r.price:>8s}  {r.year}  {r.mileage:>14s}  {r.location:20s}  {r.title}")
            print(f"           {r.link}")
        print()


def display_errors(errors: dict[str, str]):
    if not errors:
        return
    if RICH:
        for name, err in errors.items():
            _console.print(f"  [red][!] {name}: {err}[/red]")
    else:
        for name, err in errors.items():
            print(f"  [!] {name}: {err}", file=sys.stderr)


def display_summary(total: int):
    if RICH:
        _console.print(f"\n[bold]{total} total listings[/bold]")
    else:
        print(f"\n{total} total listings")
