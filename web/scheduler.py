from __future__ import annotations

import asyncio
import logging

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


def schedule_watch(watch: dict) -> None:
    """Add or replace the polling job for a watch."""
    job_id = f"watch_{watch['id']}"
    interval = watch["poll_interval_minutes"]
    # Jitter: +/-20% of the interval, minimum 30 seconds
    jitter = max(30, int(interval * 60 * 0.2))

    scheduler.add_job(
        _run_watch,
        IntervalTrigger(minutes=interval, jitter=jitter),
        id=job_id,
        replace_existing=True,
        args=[watch["id"]],
        name=f"{watch['make']} {watch['model']}",
        max_instances=1,
    )
    log.info(
        "Scheduled watch %d (%s %s) every %dm (\u00b1%ds jitter)",
        watch["id"], watch["make"], watch["model"], interval, jitter,
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
