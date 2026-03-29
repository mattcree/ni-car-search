"""Snapshot persistence and diffing.

Saves search results to JSON, keyed by listing URL. On subsequent runs,
compares against the previous snapshot to show new, gone, and price-changed
listings.

Snapshots are stored at ~/.carsearch/snapshots/{slug}.json where slug is
derived from the search params (make, model, location, radius).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .base import Filters, Listing

SNAPSHOT_DIR = Path.home() / ".carsearch" / "snapshots"


def _slug(make: str, model: str, filters: Filters) -> str:
    parts = [make.lower(), model.lower(), filters.location]
    if filters.radius:
        parts.append(f"{filters.radius}mi")
    if filters.min_price:
        parts.append(f"from{filters.min_price}")
    if filters.max_price:
        parts.append(f"to{filters.max_price}")
    if filters.min_year:
        parts.append(f"{filters.min_year}on")
    if filters.max_year:
        parts.append(f"to{filters.max_year}")
    return re.sub(r"[^a-z0-9]+", "_", "_".join(parts)).strip("_")


def load(make: str, model: str, filters: Filters, snapshot_dir: Path = SNAPSHOT_DIR) -> dict | None:
    path = snapshot_dir / f"{_slug(make, model, filters)}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def save(make: str, model: str, filters: Filters, listings: list[Listing], snapshot_dir: Path = SNAPSHOT_DIR) -> Path:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(make, model, filters)
    path = snapshot_dir / f"{slug}.json"

    data = {
        "search": {
            "make": make,
            "model": model,
            "location": filters.location,
            "radius": filters.radius,
            "min_price": filters.min_price,
            "max_price": filters.max_price,
            "min_year": filters.min_year,
            "max_year": filters.max_year,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "listings": {r.link: asdict(r) for r in listings if r.link != "-"},
    }

    with open(path, "w") as f:
        json.dump(data, f, indent=2)

    return path


def diff(previous: dict, current: list[Listing]) -> dict:
    """Compare previous snapshot against current results.

    Returns:
        {
            "new": [Listing, ...],       # in current but not previous
            "gone": [Listing, ...],      # in previous but not current
            "price_changed": [(Listing, old_price), ...],  # same URL, different price
            "unchanged": int,            # count of listings present in both
        }
    """
    prev_listings = previous.get("listings", {})
    curr_by_link = {r.link: r for r in current if r.link != "-"}

    prev_links = set(prev_listings.keys())
    curr_links = set(curr_by_link.keys())

    new = [curr_by_link[link] for link in (curr_links - prev_links)]
    gone = [
        Listing(**prev_listings[link])
        for link in (prev_links - curr_links)
    ]
    price_changed = []
    for link in (prev_links & curr_links):
        old_price = prev_listings[link]["price"]
        new_price = curr_by_link[link].price
        if old_price != new_price:
            price_changed.append((curr_by_link[link], old_price))

    unchanged = len(prev_links & curr_links) - len(price_changed)

    return {
        "new": new,
        "gone": gone,
        "price_changed": price_changed,
        "unchanged": unchanged,
    }
