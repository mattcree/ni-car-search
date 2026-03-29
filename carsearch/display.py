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


def display_summary(total: int, duplicate_count: int = 0):
    if RICH:
        msg = f"\n[bold]{total} total listings[/bold]"
        if duplicate_count:
            msg += f" [dim]({duplicate_count} probable duplicates across sites)[/dim]"
        _console.print(msg)
    else:
        msg = f"\n{total} total listings"
        if duplicate_count:
            msg += f" ({duplicate_count} probable duplicates across sites)"
        print(msg)


def display_duplicates(clusters: list[list]):
    if not clusters:
        return

    if RICH:
        _console.print(f"\n[bold yellow]Probable duplicates ({len(clusters)} cars listed on multiple sites):[/bold yellow]")
        for i, cluster in enumerate(clusters, 1):
            _console.print(f"\n  [yellow]Duplicate #{i}:[/yellow]")
            for r in cluster:
                _console.print(
                    f"    [{r.source:10s}]  {r.price:>8s}  {r.year}  {r.mileage:>14s}  {r.location:20s}  {r.title}"
                )
                _console.print(f"                 [dim]{r.link}[/dim]")
    else:
        print(f"\nProbable duplicates ({len(clusters)} cars listed on multiple sites):")
        for i, cluster in enumerate(clusters, 1):
            print(f"\n  Duplicate #{i}:")
            for r in cluster:
                print(f"    [{r.source:10s}]  {r.price:>8s}  {r.year}  {r.mileage:>14s}  {r.location:20s}  {r.title}")
                print(f"                 {r.link}")


def display_diff(diff_result: dict, prev_timestamp: str):
    new = diff_result["new"]
    gone = diff_result["gone"]
    price_changed = diff_result["price_changed"]
    unchanged = diff_result["unchanged"]

    if RICH:
        _console.print(f"\n[bold]Changes since {prev_timestamp}:[/bold]")
        _console.print(f"  {unchanged} unchanged, [green]+{len(new)} new[/green], [red]-{len(gone)} gone[/red], [yellow]{len(price_changed)} price changed[/yellow]")

        if new:
            _console.print(f"\n  [green bold]+ New listings ({len(new)}):[/green bold]")
            for r in sorted(new, key=lambda r: _parse_price(r.price)):
                _console.print(f"    [green]+[/green] {r.price:>8s}  {r.year}  {r.mileage:>14s}  {r.location:20s}  {r.title}")
                _console.print(f"             [dim]{r.link}[/dim]")

        if gone:
            _console.print(f"\n  [red bold]- Gone ({len(gone)}):[/red bold]")
            for r in sorted(gone, key=lambda r: _parse_price(r.price)):
                _console.print(f"    [red]-[/red] {r.price:>8s}  {r.year}  {r.mileage:>14s}  {r.location:20s}  {r.title}")
                _console.print(f"             [dim]{r.link}[/dim]")

        if price_changed:
            _console.print(f"\n  [yellow bold]~ Price changed ({len(price_changed)}):[/yellow bold]")
            for r, old_price in sorted(price_changed, key=lambda x: _parse_price(x[0].price)):
                _console.print(f"    [yellow]~[/yellow] {old_price:>8s} -> {r.price:<8s}  {r.year}  {r.location:20s}  {r.title}")
                _console.print(f"             [dim]{r.link}[/dim]")
    else:
        print(f"\nChanges since {prev_timestamp}:")
        print(f"  {unchanged} unchanged, +{len(new)} new, -{len(gone)} gone, {len(price_changed)} price changed")

        if new:
            print(f"\n  + New listings ({len(new)}):")
            for r in sorted(new, key=lambda r: _parse_price(r.price)):
                print(f"    + {r.price:>8s}  {r.year}  {r.mileage:>14s}  {r.location:20s}  {r.title}")
                print(f"             {r.link}")

        if gone:
            print(f"\n  - Gone ({len(gone)}):")
            for r in sorted(gone, key=lambda r: _parse_price(r.price)):
                print(f"    - {r.price:>8s}  {r.year}  {r.mileage:>14s}  {r.location:20s}  {r.title}")
                print(f"             {r.link}")

        if price_changed:
            print(f"\n  ~ Price changed ({len(price_changed)}):")
            for r, old_price in sorted(price_changed, key=lambda x: _parse_price(x[0].price)):
                print(f"    ~ {old_price:>8s} -> {r.price:<8s}  {r.year}  {r.location:20s}  {r.title}")
                print(f"             {r.link}")
