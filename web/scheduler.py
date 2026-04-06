from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .db import get_db
from .notify import send_notification
from .scrape_job import run_scrape

log = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# Concurrency guard: prevent two scrapes for the same watch from overlapping.
_in_flight: set[int] = set()
_lock = asyncio.Lock()


class AlreadyInFlight(Exception):
    """Raised when a scrape is attempted for a watch that is already being scraped."""


async def execute_scrape(watch: dict, on_progress=None) -> dict:
    """Run a scrape with the concurrency guard.

    Both scheduler jobs and manual API polls must go through this function
    so that the same watch is never scraped concurrently.

    *on_progress(source, count)* is called as each scraper page completes.
    Raises ``AlreadyInFlight`` if a scrape is already running for this watch.
    """
    watch_id = watch["id"]
    async with _lock:
        if watch_id in _in_flight:
            raise AlreadyInFlight(f"Watch {watch_id} already in flight")
        _in_flight.add(watch_id)

    try:
        return await run_scrape(watch, on_progress=on_progress)
    finally:
        async with _lock:
            _in_flight.discard(watch_id)


async def _run_watch(watch_id: int) -> None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM watches WHERE id = ? AND enabled = 1", (watch_id,)
        ).fetchone()
        if not row:
            log.info("Watch %d not found or disabled, skipping", watch_id)
            return

        watch = dict(row)

        # Add canonical display names from catalogue
        cat_make = conn.execute(
            "SELECT canonical_name FROM catalogue_makes WHERE normalized=?",
            (watch["make"],),
        ).fetchone()
        if cat_make:
            watch["make_display"] = cat_make["canonical_name"]
            cat_model = conn.execute(
                """SELECT cmo.canonical_name FROM catalogue_models cmo
                JOIN catalogue_makes cm ON cmo.make_id = cm.id
                WHERE cm.normalized=? AND cmo.normalized=?""",
                (watch["make"], watch["model"]),
            ).fetchone()
            watch["model_display"] = cat_model["canonical_name"] if cat_model else watch["model"]
        else:
            watch["make_display"] = watch["make"]
            watch["model_display"] = watch["model"]

    try:
        result = await execute_scrape(watch)
        if result["new"] or result["price_changed"]:
            await send_notification(watch, result)
    except AlreadyInFlight:
        log.info("Watch %d already in flight, skipping scheduled run", watch_id)
    except Exception:
        log.exception("Scrape failed for watch %d", watch_id)


def is_in_flight(watch_id: int) -> bool:
    """Check whether a scrape is currently running for this watch."""
    return watch_id in _in_flight


def _compute_start_date(start_time_str: str | None) -> datetime | None:
    """Convert an HH:MM start time into the nearest future anchor point."""
    if not start_time_str:
        return None
    try:
        h, m = map(int, start_time_str.split(":"))
        anchor = time(h, m)
    except (ValueError, TypeError):
        return None

    now = datetime.now(timezone.utc)
    start = now.replace(hour=anchor.hour, minute=anchor.minute, second=0, microsecond=0)
    # If the time already passed today, use yesterday's anchor so the
    # interval grid still aligns correctly (APScheduler will skip past dates).
    if start > now:
        start -= timedelta(days=1)
    return start


def schedule_watch(watch: dict) -> None:
    """Add or replace the polling job for a watch."""
    job_id = f"watch_{watch['id']}"
    interval = watch["poll_interval_minutes"]
    # Jitter: +/-20% of the interval, minimum 30 seconds
    jitter = max(30, int(interval * 60 * 0.2))

    start_date = _compute_start_date(watch.get("poll_start_time"))

    trigger_kwargs: dict = dict(minutes=interval, jitter=jitter)
    if start_date:
        trigger_kwargs["start_date"] = start_date

    scheduler.add_job(
        _run_watch,
        IntervalTrigger(**trigger_kwargs),
        id=job_id,
        replace_existing=True,
        args=[watch["id"]],
        name=f"{watch['make']} {watch['model']}",
        max_instances=1,
    )
    log.info(
        "Scheduled watch %d (%s %s) every %dm (\u00b1%ds jitter)%s",
        watch["id"], watch["make"], watch["model"], interval, jitter,
        f" anchored at {watch['poll_start_time']}" if watch.get("poll_start_time") else "",
    )


def unschedule_watch(watch_id: int) -> None:
    job_id = f"watch_{watch_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        log.info("Unscheduled watch %d", watch_id)


def get_scheduled_jobs() -> list[dict]:
    """Return scheduler state for the admin endpoint."""
    return [
        {
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "in_flight": job.args[0] in _in_flight if job.args else False,
        }
        for job in scheduler.get_jobs()
    ]


def load_all_watches() -> None:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM watches WHERE enabled = 1").fetchall()
    for row in rows:
        schedule_watch(dict(row))


def start() -> None:
    load_all_watches()
    scheduler.start()
    log.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))


def shutdown() -> None:
    scheduler.shutdown(wait=True)
