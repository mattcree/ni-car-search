from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from carsearch.base import Filters, resolve_location
from carsearch.catalogue import resolve_source_params
from carsearch.runner import run
from carsearch.scrapers import get_all_scrapers

from .db import get_db

log = logging.getLogger(__name__)


# ── parsing helpers ─────────────────────────────────────────────────────────


def _parse_price(s: str) -> int | None:
    if not s or s == "-":
        return None
    cleaned = s.replace("\u00a3", "").replace(",", "").replace(" ", "")
    try:
        return int(float(cleaned))
    except (ValueError, TypeError):
        return None


def _parse_year(s: str) -> int | None:
    if not s or s == "-":
        return None
    try:
        return int(s.strip())
    except (ValueError, TypeError):
        return None


def _parse_mileage(s: str) -> int | None:
    """Parse mileage strings like '24,742 miles', '12k Miles', '33.3k Miles'."""
    if not s or s == "-":
        return None
    # Handle "12k", "33.3k" shorthand (Motors.co.uk)
    m = re.match(r"([\d,.]+)\s*k\b", s, re.IGNORECASE)
    if m:
        try:
            return int(float(m.group(1).replace(",", "")) * 1000)
        except (ValueError, TypeError):
            return None
    # Standard: strip non-digits
    digits = re.sub(r"[^\d]", "", s)
    try:
        return int(digits) if digits else None
    except (ValueError, TypeError):
        return None


def _normalise_transmission(s: str) -> str:
    if not s or s == "-":
        return "unknown"
    t = s.lower().strip()
    if "auto" in t:
        return "auto"
    if "manual" in t or "man" == t:
        return "manual"
    return "unknown"


# ── content fingerprinting ──────────────────────────────────────────────────


def _fingerprint(year: int | None, mileage: int | None, transmission: str) -> str:
    """Compute a content-based identity for a physical car.

    Within a watch (fixed make/model), year + mileage band + transmission
    is a strong enough signal to group listings of the same car across sites.
    """
    y = str(year) if year else "?"
    m = str(mileage // 1000) if mileage is not None else "?"
    t = _normalise_transmission(transmission)
    return f"{y}:{m}:{t}"


# ── state resolution ────────────────────────────────────────────────────────


def _resolve(
    url: str,
    fp: str,
    source: str,
    active: dict[str, dict],
    gone: dict[str, dict],
    vehicles_by_fp: dict[str, list[dict]],
    vehicle_sources: dict[int, set[str]],
) -> tuple[str, dict | None, dict | None]:
    """Determine what state a scraped listing falls into.

    Returns (kind, listing_or_none, vehicle_or_none) where kind is one of:
      "active"     — known listing still active
      "gone"       — known listing was gone, now returning
      "new_source" — new URL, vehicle exists but doesn't have this source yet
      "new"        — completely new vehicle and listing
    """
    if url in active:
        listing = active[url]
        return ("active", listing, None)
    if url in gone:
        listing = gone[url]
        return ("gone", listing, None)

    # Look for a vehicle with this fingerprint that doesn't already
    # have a listing from this same source — that's cross-site dedup.
    for vehicle in vehicles_by_fp.get(fp, []):
        if source not in vehicle_sources.get(vehicle["id"], set()):
            return ("new_source", None, vehicle)

    return ("new", None, None)


# ── main entry point ────────────────────────────────────────────────────────


async def run_scrape(watch: dict, on_progress=None) -> dict:
    """Run scrapers for a watch and persist results to the database.

    *on_progress(event_dict)* is called with structured events so the caller
    can stream live status to the UI.  Event types:

      {"type": "started", "scrapers": ["AutoTrader", ...]}
      {"type": "progress", "source": "AutoTrader", "count": 37}
      {"type": "persisting"}
    """
    now = datetime.now(timezone.utc).isoformat()
    watch_id = watch["id"]

    location = watch["location"] or "northern-ireland"
    postcode = resolve_location(location)
    filters = Filters(
        min_price=watch["min_price"],
        max_price=watch["max_price"],
        min_year=watch["min_year"],
        max_year=watch["max_year"],
        postcode=postcode,
        location=location.lower(),
        radius=watch["radius"] or 0,
    )

    # Record the run before scraping so failures are always visible.
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO scrape_runs (watch_id, started_at) VALUES (?, ?)",
            (watch_id, now),
        )
        run_id = cur.lastrowid
        conn.commit()

    log.info("Scraping %s %s (watch %d, run %d)", watch["make"], watch["model"], watch_id, run_id)

    all_scrapers = get_all_scrapers()

    # ── event helpers ───────────────────────────────────────────────────────
    # Every event is both persisted to run_events and streamed to the UI.

    def _log_event(event_type: str, source: str | None = None,
                   count: int | None = None, message: str | None = None):
        ts = datetime.now(timezone.utc).isoformat()
        with get_db() as c:
            c.execute(
                "INSERT INTO run_events (run_id, event_type, timestamp, source, count, message) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, event_type, ts, source, count, message),
            )
            c.commit()

    def _emit(event: dict):
        if on_progress:
            on_progress(event)

    # Tell the UI which scrapers we're about to run.
    _emit({"type": "started", "scrapers": [s.name for s in all_scrapers]})
    _log_event("RUN_START")

    # Wire up progress reporting through the runner's on_results callback.
    source_counts: dict[str, int] = {}

    def on_results(source, listings):
        source_counts[source] = source_counts.get(source, 0) + len(listings)
        _log_event("SCRAPER_PROGRESS", source=source, count=source_counts[source])
        _emit({"type": "progress", "source": source, "count": source_counts[source]})

    def on_event(event_type, source, **kwargs):
        match event_type:
            case "scraper_start":
                _log_event("SCRAPER_START", source=source)
                _emit({"type": "scraper_start", "source": source})
            case "scraper_done":
                _log_event("SCRAPER_DONE", source=source, count=source_counts.get(source, 0))
                _emit({"type": "scraper_done", "source": source, "count": source_counts.get(source, 0)})
            case "scraper_retry":
                _log_event("SCRAPER_RETRY", source=source, message=kwargs.get("error"), count=kwargs.get("attempt"))
            case "scraper_error":
                _log_event("SCRAPER_ERROR", source=source, message=kwargs.get("error"))
                _emit({"type": "scraper_error", "source": source, "message": kwargs.get("error", "")})

    # Look up per-source aliases from the catalogue (if populated).
    with get_db() as cat_conn:
        sp = resolve_source_params(cat_conn, watch["make"], watch["model"])
    if sp:
        log.info("Catalogue resolved %d source aliases for %s %s", len(sp), watch["make"], watch["model"])

    try:
        scraped, errors = await run(
            watch["make"], watch["model"], filters,
            on_results=on_results, on_event=on_event,
            source_params=sp or None,
        )
    except Exception as exc:
        with get_db() as conn:
            conn.execute(
                "UPDATE scrape_runs SET finished_at=?, errors=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), json.dumps({"_fatal": str(exc)}), run_id),
            )
            conn.commit()
        raise

    # ── persist ─────────────────────────────────────────────────────────────

    _log_event("PERSIST_START")
    _emit({"type": "persisting"})

    # Deduplicate scraped items by URL (last-writer-wins)
    scraped_by_url: dict[str, object] = {}
    for item in scraped:
        scraped_by_url[item.link] = item

    with get_db() as conn:
        try:
            # Pre-load existing state into memory to avoid per-item queries.
            active_listings, gone_listings = _load_listings(conn, watch_id)
            vehicles_by_fp, vehicle_sources = _load_vehicles(conn, watch_id)

            new_count = 0
            new_source_count = 0
            price_changed_count = 0
            returned_count = 0

            for url, item in scraped_by_url.items():
                price = _parse_price(item.price)
                year = _parse_year(item.year)
                mileage = _parse_mileage(item.mileage)
                fp = _fingerprint(year, mileage, item.transmission)

                match _resolve(url, fp, item.source, active_listings, gone_listings, vehicles_by_fp, vehicle_sources):
                    case ("active", listing, _vehicle):
                        conn.execute(
                            "UPDATE listings SET last_seen_at=? WHERE id=?",
                            (now, listing["id"]),
                        )
                        conn.execute(
                            "UPDATE vehicles SET last_seen_at=? WHERE id=?",
                            (now, listing["vehicle_id"]),
                        )
                        if price is not None and listing["price"] is not None and price != listing["price"]:
                            conn.execute("UPDATE listings SET price=? WHERE id=?", (price, listing["id"]))
                            conn.execute(
                                """INSERT INTO vehicle_events
                                (vehicle_id, listing_id, run_id, event_type, timestamp, price, old_price, source)
                                VALUES (?, ?, ?, 'PRICE_CHANGE', ?, ?, ?, ?)""",
                                (listing["vehicle_id"], listing["id"], run_id, now, price, listing["price"], item.source),
                            )
                            price_changed_count += 1

                    case ("gone", listing, _vehicle):
                        conn.execute(
                            """UPDATE listings
                            SET status='active', price=?, last_seen_at=?, gone_at=NULL
                            WHERE id=?""",
                            (price, now, listing["id"]),
                        )
                        vid = listing["vehicle_id"]
                        conn.execute(
                            "UPDATE vehicles SET status='active', last_seen_at=?, gone_at=NULL WHERE id=?",
                            (now, vid),
                        )
                        conn.execute(
                            """INSERT INTO vehicle_events
                            (vehicle_id, listing_id, run_id, event_type, timestamp, price, source)
                            VALUES (?, ?, ?, 'RETURNED', ?, ?, ?)""",
                            (vid, listing["id"], run_id, now, price, item.source),
                        )
                        returned_count += 1

                    case ("new_source", None, vehicle):
                        vid = vehicle["id"]
                        lid = _insert_listing(conn, vid, watch_id, item, price, year, now)
                        conn.execute("UPDATE vehicles SET last_seen_at=? WHERE id=?", (now, vid))
                        conn.execute(
                            """INSERT INTO vehicle_events
                            (vehicle_id, listing_id, run_id, event_type, timestamp, price, source)
                            VALUES (?, ?, ?, 'NEW_SOURCE', ?, ?, ?)""",
                            (vid, lid, run_id, now, price, item.source),
                        )
                        # Update in-memory source tracking
                        vehicle_sources.setdefault(vid, set()).add(item.source)
                        new_source_count += 1

                    case ("new", None, None):
                        vid = _insert_vehicle(conn, watch_id, fp, year, mileage, item.transmission, now)
                        lid = _insert_listing(conn, vid, watch_id, item, price, year, now)
                        # Register in in-memory maps so later items can match
                        new_vehicle = {"id": vid, "fingerprint": fp}
                        vehicles_by_fp.setdefault(fp, []).append(new_vehicle)
                        vehicle_sources[vid] = {item.source}
                        conn.execute(
                            """INSERT INTO vehicle_events
                            (vehicle_id, listing_id, run_id, event_type, timestamp, price, source)
                            VALUES (?, ?, ?, 'FOUND', ?, ?, ?)""",
                            (vid, lid, run_id, now, price, item.source),
                        )
                        new_count += 1

            # ── mark gone ───────────────────────────────────────────────────
            gone_count = 0
            affected_vehicles: set[int] = set()

            for url, listing in active_listings.items():
                if url not in scraped_by_url:
                    conn.execute(
                        "UPDATE listings SET status='gone', gone_at=? WHERE id=?",
                        (now, listing["id"]),
                    )
                    vid = listing["vehicle_id"]
                    conn.execute(
                        """INSERT INTO vehicle_events
                        (vehicle_id, listing_id, run_id, event_type, timestamp, price, source)
                        VALUES (?, ?, ?, 'SOURCE_GONE', ?, ?, ?)""",
                        (vid, listing["id"], run_id, now, listing["price"], listing["source"]),
                    )
                    affected_vehicles.add(vid)
                    gone_count += 1

            # If all listings for a vehicle are now gone, mark the vehicle gone.
            for vid in affected_vehicles:
                remaining = conn.execute(
                    "SELECT COUNT(*) FROM listings WHERE vehicle_id=? AND status='active'",
                    (vid,),
                ).fetchone()[0]
                if remaining == 0:
                    conn.execute(
                        "UPDATE vehicles SET status='gone', gone_at=? WHERE id=?",
                        (now, vid),
                    )
                    conn.execute(
                        """INSERT INTO vehicle_events
                        (vehicle_id, run_id, event_type, timestamp) VALUES (?, ?, 'GONE', ?)""",
                        (vid, run_id, now),
                    )

            # ── finalise run ────────────────────────────────────────────────
            finished_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """UPDATE scrape_runs
                SET finished_at=?, total_found=?, new_count=?, new_source_count=?,
                    gone_count=?, price_changed_count=?, returned_count=?, errors=?
                WHERE id=?""",
                (
                    finished_at, len(scraped_by_url), new_count, new_source_count,
                    gone_count, price_changed_count, returned_count,
                    json.dumps(errors) if errors else None, run_id,
                ),
            )
            conn.execute("UPDATE watches SET last_polled_at=? WHERE id=?", (finished_at, watch_id))
            conn.commit()
            _log_event("PERSIST_DONE")

        except Exception:
            conn.rollback()
            _log_event("PERSIST_ERROR")
            conn.execute(
                "UPDATE scrape_runs SET finished_at=?, errors=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), json.dumps({"_fatal": "DB error during persist"}), run_id),
            )
            conn.commit()
            raise

    result = {
        "run_id": run_id,
        "total_found": len(scraped_by_url),
        "new": new_count,
        "new_sources": new_source_count,
        "gone": gone_count,
        "price_changed": price_changed_count,
        "returned": returned_count,
        "errors": errors,
    }
    log.info("Scrape done (watch %d): %s", watch_id, result)
    return result


# ── data loading ────────────────────────────────────────────────────────────


def _load_listings(conn, watch_id) -> tuple[dict[str, dict], dict[str, dict]]:
    """Pre-load all listings for a watch, split by status."""
    active: dict[str, dict] = {}
    gone: dict[str, dict] = {}

    for row in conn.execute(
        """SELECT l.*, v.fingerprint AS _fp
        FROM listings l JOIN vehicles v ON l.vehicle_id = v.id
        WHERE l.watch_id = ?""",
        (watch_id,),
    ):
        d = dict(row)
        match d["status"]:
            case "active":
                active[d["url"]] = d
            case _:
                gone[d["url"]] = d

    return active, gone


def _load_vehicles(conn, watch_id) -> tuple[dict[str, list[dict]], dict[int, set[str]]]:
    """Pre-load vehicles and their source sets for a watch.

    Returns:
        vehicles_by_fp: fingerprint -> list of vehicle dicts (multiple vehicles can share a fp)
        vehicle_sources: vehicle_id -> set of source names with active listings
    """
    vehicles_by_fp: dict[str, list[dict]] = {}
    for row in conn.execute("SELECT * FROM vehicles WHERE watch_id=?", (watch_id,)):
        vehicles_by_fp.setdefault(row["fingerprint"], []).append(dict(row))

    vehicle_sources: dict[int, set[str]] = {}
    for row in conn.execute(
        "SELECT vehicle_id, source FROM listings WHERE watch_id=? AND status='active'",
        (watch_id,),
    ):
        vehicle_sources.setdefault(row["vehicle_id"], set()).add(row["source"])

    return vehicles_by_fp, vehicle_sources


# ── insert helpers ──────────────────────────────────────────────────────────


def _insert_vehicle(conn, watch_id, fp, year, mileage, transmission, now) -> int:
    cur = conn.execute(
        """INSERT INTO vehicles
        (watch_id, fingerprint, year, mileage_bucket, transmission, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (watch_id, fp, year, mileage // 1000 if mileage is not None else None,
         _normalise_transmission(transmission), now, now),
    )
    return cur.lastrowid


def _insert_listing(conn, vehicle_id, watch_id, item, price, year, now) -> int:
    cur = conn.execute(
        """INSERT INTO listings
        (vehicle_id, watch_id, url, source, title, price, year, mileage,
         location, transmission, body_type, fuel_type, image_url, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (vehicle_id, watch_id, item.link, item.source, item.title,
         price, year, item.mileage, item.location, item.transmission,
         item.body, item.fuel_type, item.image_url, now, now),
    )
    return cur.lastrowid
