from __future__ import annotations

import re
import sys

from .base import Listing

try:
    from rich.console import Console

    RICH = True
except ImportError:
    RICH = False

_console = Console() if RICH else None

# Standard column widths
_SRC = 14
_PRICE = 8
_YEAR = 4
_MILES = 14
_LOC = 22


def _parse_price(price: str) -> float:
    s = re.sub(r"[^\d.]", "", price)
    try:
        return float(s)
    except ValueError:
        return float("inf")


def _fmt(r: Listing, prefix: str = "") -> tuple[str, str]:
    """Return (main_line, link_line) for a listing."""
    main = f"{prefix}{r.source:{_SRC}s} {r.price:>{_PRICE}s}  {r.year:{_YEAR}s}  {r.mileage:>{_MILES}s}  {r.location:{_LOC}s}  {r.title}"
    link = f"{' ' * len(prefix)}{' ' * _SRC} {r.link}"
    return main, link


def _fmt_rich(r: Listing, prefix: str = "", prefix_style: str = "") -> tuple[str, str]:
    """Return (main_line, link_line) with rich markup."""
    pfx = f"[{prefix_style}]{prefix}[/{prefix_style}]" if prefix_style and prefix else prefix
    main = (
        f"{pfx}[cyan]{r.source:{_SRC}s}[/cyan] "
        f"[green]{r.price:>{_PRICE}s}[/green]  {r.year:{_YEAR}s}  "
        f"{r.mileage:>{_MILES}s}  {r.location:{_LOC}s}  {r.title}"
    )
    pad = " " * len(prefix)
    link = f"{pad}{' ' * _SRC} [dim]{r.link}[/dim]"
    return main, link


def _print_listing(r: Listing, prefix: str = "", prefix_style: str = ""):
    if RICH:
        main, link = _fmt_rich(r, prefix, prefix_style)
        _console.print(main)
        _console.print(link)
    else:
        main, link = _fmt(r, prefix)
        print(main)
        print(link)


def emit(source: str, listings: list[Listing]):
    """Print a batch of results as they arrive from a scraper."""
    if not listings:
        return

    listings.sort(key=lambda r: _parse_price(r.price))

    if RICH:
        _console.print(f"  [cyan]{source}[/cyan]: [bold]{len(listings)}[/bold] listings")
    else:
        print(f"  {source}: {len(listings)} listings")

    for r in listings:
        _print_listing(r, prefix="    ")

    if RICH:
        _console.print()
    else:
        print()


def display_errors(errors: dict[str, str]):
    if not errors:
        return
    for name, err in errors.items():
        if RICH:
            _console.print(f"  [red][!] {name}: {err}[/red]")
        else:
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
        _console.print(f"\n[bold yellow]Probable duplicates ({len(clusters)} cars on multiple sites):[/bold yellow]")
    else:
        print(f"\nProbable duplicates ({len(clusters)} cars on multiple sites):")

    for i, cluster in enumerate(clusters, 1):
        if RICH:
            _console.print(f"\n  [yellow]#{i}[/yellow]")
        else:
            print(f"\n  #{i}")
        for r in cluster:
            _print_listing(r, prefix="    ")


def display_diff(diff_result: dict, prev_timestamp: str):
    new = diff_result["new"]
    gone = diff_result["gone"]
    price_changed = diff_result["price_changed"]
    unchanged = diff_result["unchanged"]

    if RICH:
        _console.print(f"\n[bold]Changes since {prev_timestamp}:[/bold]")
        _console.print(
            f"  {unchanged} unchanged, "
            f"[green]+{len(new)} new[/green], "
            f"[red]-{len(gone)} gone[/red], "
            f"[yellow]{len(price_changed)} price changed[/yellow]"
        )
    else:
        print(f"\nChanges since {prev_timestamp}:")
        print(f"  {unchanged} unchanged, +{len(new)} new, -{len(gone)} gone, {len(price_changed)} price changed")

    if new:
        if RICH:
            _console.print(f"\n  [green bold]+ New ({len(new)}):[/green bold]")
        else:
            print(f"\n  + New ({len(new)}):")
        for r in sorted(new, key=lambda r: _parse_price(r.price)):
            _print_listing(r, prefix="  + ", prefix_style="green")

    if gone:
        if RICH:
            _console.print(f"\n  [red bold]- Gone ({len(gone)}):[/red bold]")
        else:
            print(f"\n  - Gone ({len(gone)}):")
        for r in sorted(gone, key=lambda r: _parse_price(r.price)):
            _print_listing(r, prefix="  - ", prefix_style="red")

    if price_changed:
        if RICH:
            _console.print(f"\n  [yellow bold]~ Price changed ({len(price_changed)}):[/yellow bold]")
        else:
            print(f"\n  ~ Price changed ({len(price_changed)}):")
        for r, old_price in sorted(price_changed, key=lambda x: _parse_price(x[0].price)):
            # Swap title to show price change
            changed = Listing(
                source=r.source,
                title=f"{old_price} -> {r.price}  {r.title}",
                price=r.price,
                year=r.year,
                mileage=r.mileage,
                location=r.location,
                link=r.link,
            )
            _print_listing(changed, prefix="  ~ ", prefix_style="yellow")
