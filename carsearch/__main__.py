from __future__ import annotations

import argparse
import asyncio

from .base import Filters, resolve_location
from .dedup import find_duplicates
from .display import (
    display_diff, display_duplicates, display_errors, display_json,
    display_summary, display_table, emit_progress, emit_stream,
)
from .runner import run
from .snapshot import diff, load, save


def main():
    parser = argparse.ArgumentParser(
        description="Search NI car sites for a given make and model.",
    )
    parser.add_argument("make", help="Car make (e.g. volkswagen, bmw)")
    parser.add_argument("model", help="Car model (e.g. golf, 3-series, kodiaq)")
    parser.add_argument("--location", default="northern-ireland", help="Location name or postcode (e.g. belfast, BT1 1AA)")
    parser.add_argument("--radius", type=int, default=0, metavar="MILES", help="Search radius in miles (default: no limit)")
    parser.add_argument("--max-pages", type=int, default=0, metavar="N", help="Max pages to scrape per site (default: no limit)")
    parser.add_argument("--min-price", type=int, metavar="N", help="Minimum price")
    parser.add_argument("--max-price", type=int, metavar="N", help="Maximum price")
    parser.add_argument("--min-year", type=int, metavar="YEAR", help="Minimum year")
    parser.add_argument("--max-year", type=int, metavar="YEAR", help="Maximum year")
    parser.add_argument("--no-snapshot", action="store_true", help="Don't save or compare snapshots")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output results as JSON")
    parser.add_argument("--stream", action="store_true", help="Show results per-source as they arrive (default: collect and sort)")
    args = parser.parse_args()

    make = args.make
    model = args.model

    location = args.location
    postcode = resolve_location(location)

    filters = Filters(
        min_price=args.min_price,
        max_price=args.max_price,
        min_year=args.min_year,
        max_year=args.max_year,
        postcode=postcode,
        location=location.lower(),
        radius=args.radius,
        max_pages=args.max_pages,
    )

    label = f"{make} {model}"
    filter_parts = []
    if filters.min_price:
        filter_parts.append(f"\u00a3{filters.min_price:,}+")
    if filters.max_price:
        filter_parts.append(f"up to \u00a3{filters.max_price:,}")
    if filters.min_year:
        filter_parts.append(f"{filters.min_year}+")
    if filters.max_year:
        filter_parts.append(f"up to {filters.max_year}")

    loc_label = location.title() if location != "northern-ireland" else "Northern Ireland"
    if filters.radius:
        loc_label += f" ({filters.radius} miles)"

    if filter_parts:
        label += f" ({', '.join(filter_parts)})"

    if not args.json_output:
        print(f"\nSearching for {label} in {loc_label}...\n")

    # Choose emit callback based on mode
    if args.json_output:
        on_results = None
    elif args.stream:
        on_results = emit_stream
    else:
        on_results = emit_progress

    # Load previous snapshot
    previous = None
    if not args.no_snapshot:
        previous = load(make, model, filters)

    # Run search
    results, errors = asyncio.run(run(make, model, filters, on_results=on_results))

    if args.json_output:
        display_json(results)
        return

    display_errors(errors)

    # Show combined sorted table in collect mode
    if not args.stream:
        display_table(results)

    # Duplicate detection
    clusters = find_duplicates(results)
    dup_count = sum(len(c) - 1 for c in clusters)
    display_summary(len(results), dup_count)
    display_duplicates(clusters)

    # Compare and save snapshot
    if not args.no_snapshot:
        if previous:
            prev_ts = previous["search"].get("timestamp", "unknown")
            diff_result = diff(previous, results)
            display_diff(diff_result, prev_ts)

        path = save(make, model, filters, results)
        print(f"\n  Snapshot saved: {path}")

    print()


if __name__ == "__main__":
    main()
