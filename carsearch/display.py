from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict

from .base import Listing

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box

    RICH = True
except ImportError:
    RICH = False

_console = Console(width=max(160, Console().width)) if RICH else None


def _parse_price(price: str) -> float:
    s = re.sub(r"[^\d.]", "", price)
    try:
        return float(s)
    except ValueError:
        return float("inf")


def _trans(t: str) -> str:
    t = t.lower()
    if "auto" in t or "dsg" in t:
        return "Auto"
    if "man" in t:
        return "Man"
    return "-" if t == "-" else t


def _title_with_link(r: Listing) -> str:
    """Title as a clickable hyperlink in Rich, or title + link for plain text."""
    if RICH and r.link != "-":
        return f"[link={r.link}]{r.title}[/link]"
    return r.title


def _make_table(title: str = "") -> Table:
    table = Table(
        title=title,
        box=box.SIMPLE_HEAD,
        show_edge=False,
        pad_edge=False,
        title_style="bold",
    )
    table.add_column("Source", style="cyan", no_wrap=True, min_width=14)
    table.add_column("Price", style="green bold", justify="right", no_wrap=True, min_width=8)
    table.add_column("Year", no_wrap=True, min_width=4)
    table.add_column("Mileage", justify="right", no_wrap=True, min_width=14)
    table.add_column("Trans", no_wrap=True, min_width=5)
    table.add_column("Location", no_wrap=True, max_width=26, overflow="ellipsis")
    table.add_column("Title", no_wrap=True, overflow="ellipsis")
    return table


def _add_row(table: Table, r: Listing, style: str | None = None):
    table.add_row(
        r.source, r.price, r.year, r.mileage,
        _trans(r.transmission),
        r.location,
        _title_with_link(r),
        style=style,
    )


def _plain_row(r: Listing, prefix: str = "") -> str:
    t = _trans(r.transmission)
    return (
        f"{prefix}{r.source:<14} {r.price:>8}  {r.year:<4}  {r.mileage:>14}  "
        f"{t:<6}  {r.location:<24}  {r.title}\n"
        f"{prefix}{' ' * 14} {r.link}"
    )


# --- Streaming mode (per-source as they arrive) ---

def emit_stream(source: str, listings: list[Listing]):
    if not listings:
        return
    listings.sort(key=lambda r: _parse_price(r.price))
    if RICH:
        table = _make_table(f"{source}: {len(listings)} listings")
        for r in listings:
            _add_row(table, r)
        _console.print(table)
        _console.print()
    else:
        print(f"  {source}: {len(listings)} listings")
        for r in listings:
            print(_plain_row(r, prefix="    "))
        print()


def emit_progress(source: str, listings: list[Listing]):
    """Just show a count — used in collect mode."""
    if not listings:
        return
    if RICH:
        _console.print(f"  [cyan]{source}[/cyan]: [bold]{len(listings)}[/bold] listings")
    else:
        print(f"  {source}: {len(listings)} listings")


# --- Collected mode (combined sorted table at end) ---

def display_table(listings: list[Listing]):
    if not listings:
        return
    sorted_listings = sorted(listings, key=lambda r: _parse_price(r.price))
    if RICH:
        table = _make_table()
        for r in sorted_listings:
            _add_row(table, r)
        _console.print()
        _console.print(table)
    else:
        print()
        for r in sorted_listings:
            print(_plain_row(r))


# --- JSON mode ---

def display_json(listings: list[Listing]):
    sorted_listings = sorted(listings, key=lambda r: _parse_price(r.price))
    data = [asdict(r) for r in sorted_listings]
    print(json.dumps(data, indent=2))


# --- Shared display functions ---

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
        for i, cluster in enumerate(clusters, 1):
            table = _make_table(f"#{i}")
            table.title_style = "yellow"
            for r in cluster:
                _add_row(table, r)
            _console.print(table)
    else:
        print(f"\nProbable duplicates ({len(clusters)} cars on multiple sites):")
        for i, cluster in enumerate(clusters, 1):
            print(f"\n  #{i}")
            for r in cluster:
                print(_plain_row(r, prefix="    "))


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
            table = _make_table(f"+ New ({len(new)})")
            table.title_style = "green bold"
            for r in sorted(new, key=lambda r: _parse_price(r.price)):
                _add_row(table, r, style="green")
            _console.print(table)
        else:
            print(f"\n  + New ({len(new)}):")
            for r in sorted(new, key=lambda r: _parse_price(r.price)):
                print(_plain_row(r, prefix="  + "))

    if gone:
        if RICH:
            table = _make_table(f"- Gone ({len(gone)})")
            table.title_style = "red bold"
            for r in sorted(gone, key=lambda r: _parse_price(r.price)):
                _add_row(table, r, style="red")
            _console.print(table)
        else:
            print(f"\n  - Gone ({len(gone)}):")
            for r in sorted(gone, key=lambda r: _parse_price(r.price)):
                print(_plain_row(r, prefix="  - "))

    if price_changed:
        if RICH:
            table = _make_table(f"~ Price changed ({len(price_changed)})")
            table.title_style = "yellow bold"
            for r, old_price in sorted(price_changed, key=lambda x: _parse_price(x[0].price)):
                changed = Listing(
                    source=r.source,
                    title=f"{old_price} -> {r.price}  {r.title}",
                    price=r.price, year=r.year, mileage=r.mileage,
                    location=r.location, link=r.link,
                )
                _add_row(table, changed, style="yellow")
            _console.print(table)
        else:
            print(f"\n  ~ Price changed ({len(price_changed)}):")
            for r, old_price in sorted(price_changed, key=lambda x: _parse_price(x[0].price)):
                changed = Listing(
                    source=r.source,
                    title=f"{old_price} -> {r.price}  {r.title}",
                    price=r.price, year=r.year, mileage=r.mileage,
                    location=r.location, link=r.link,
                )
                print(_plain_row(changed, prefix="  ~ "))
