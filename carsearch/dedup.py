"""Cross-site duplicate detection.

Dealers list the same car on multiple sites. We detect probable duplicates
by matching on year + mileage (within tolerance) + similar location. These
are the most stable identifiers - title format and price can differ between
sites.

A group of listings that are probably the same physical car is called a
"cluster". Each cluster gets a short ID so the user can see which listings
are the same car across sites.
"""

from __future__ import annotations

import re
from itertools import combinations

from .base import Listing


def _parse_mileage(mileage: str) -> int | None:
    digits = re.sub(r"[^\d]", "", mileage)
    if not digits:
        return None
    return int(digits)


def _parse_year(year: str) -> int | None:
    try:
        return int(year)
    except (ValueError, TypeError):
        return None


def _normalize_location(loc: str) -> str:
    """Rough location normalization for comparison."""
    loc = loc.lower().strip()
    # Strip distance info like "(12 miles)"
    loc = re.sub(r"\(.*?\)", "", loc).strip()
    # Strip common prefixes
    for prefix in ["county ", "co. ", "co "]:
        loc = loc.removeprefix(prefix)
    return loc


def _locations_match(a: str, b: str) -> bool:
    na = _normalize_location(a)
    nb = _normalize_location(b)
    if not na or not nb:
        return True  # can't compare, don't penalize
    # One contains the other, or they start with the same word
    if na in nb or nb in na:
        return True
    first_a = na.split(",")[0].split()[0] if na else ""
    first_b = nb.split(",")[0].split()[0] if nb else ""
    return first_a == first_b and first_a != ""


def _is_probable_match(a: Listing, b: Listing, mileage_tolerance: int = 500) -> bool:
    """Check if two listings are probably the same physical car."""
    # Must be from different sources
    if a.source == b.source:
        return False

    # Year must match exactly
    ya = _parse_year(a.year)
    yb = _parse_year(b.year)
    if ya is None or yb is None or ya != yb:
        return False

    # Mileage must be within tolerance
    ma = _parse_mileage(a.mileage)
    mb = _parse_mileage(b.mileage)
    if ma is None or mb is None:
        return False
    if abs(ma - mb) > mileage_tolerance:
        return False

    # Location should roughly match
    if not _locations_match(a.location, b.location):
        return False

    return True


def find_duplicates(listings: list[Listing], mileage_tolerance: int = 500) -> list[list[Listing]]:
    """Find clusters of listings that are probably the same car.

    Returns a list of clusters, where each cluster is a list of 2+ listings
    that appear to be the same physical car listed on different sites.
    """
    # Build adjacency: which listings match each other
    n = len(listings)
    matches: dict[int, set[int]] = {i: set() for i in range(n)}

    for i, j in combinations(range(n), 2):
        if _is_probable_match(listings[i], listings[j], mileage_tolerance):
            matches[i].add(j)
            matches[j].add(i)

    # Build clusters via connected components
    visited: set[int] = set()
    clusters: list[list[Listing]] = []

    for i in range(n):
        if i in visited or not matches[i]:
            continue
        # BFS to find connected component
        cluster_indices: set[int] = set()
        queue = [i]
        while queue:
            node = queue.pop()
            if node in cluster_indices:
                continue
            cluster_indices.add(node)
            visited.add(node)
            queue.extend(matches[node] - cluster_indices)

        if len(cluster_indices) >= 2:
            clusters.append([listings[idx] for idx in sorted(cluster_indices)])

    return clusters
