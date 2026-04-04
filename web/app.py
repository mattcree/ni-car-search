from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from .config import LOG_LEVEL
from .db import db_dependency, init_db
from .models import (
    EventResponse,
    FeedEventResponse,
    ListingResponse,
    RunDetailResponse,
    SchedulerJobResponse,
    ScrapeResultResponse,
    ScrapeRunResponse,
    SettingsUpdate,
    VehicleDetailResponse,
    VehicleResponse,
    WatchCreate,
    WatchResponse,
    WatchStatsResponse,
    WatchUpdate,
)
from .notify import send_notification
from .scheduler import (
    AlreadyInFlight,
    execute_scrape,
    get_scheduled_jobs,
    schedule_watch,
    shutdown,
    start,
    unschedule_watch,
)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"

Conn = sqlite3.Connection


# ── lifecycle ───────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start()
    yield
    shutdown()


app = FastAPI(title="CarSearch", lifespan=lifespan)


@app.middleware("http")
async def error_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


# ── health ──────────────────────────────────────────────────────────────────


@app.get("/health")
def health(conn: Conn = Depends(db_dependency)):
    conn.execute("SELECT 1")
    return {"status": "ok"}


# ── feed ────────────────────────────────────────────────────────────────────


@app.get("/api/feed", response_model=list[FeedEventResponse])
def get_feed(
    since: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
    conn: Conn = Depends(db_dependency),
):
    if since:
        where = "WHERE ve.timestamp > ?"
        params: list = [since, limit]
    else:
        where = ""
        params = [limit]

    rows = conn.execute(
        f"""SELECT
            ve.id, ve.event_type, ve.timestamp, ve.vehicle_id,
            ve.price, ve.old_price, ve.source,
            v.year AS vehicle_year, v.mileage_bucket,
            v.transmission AS vehicle_transmission,
            w.id AS watch_id, w.make AS watch_make, w.model AS watch_model,
            (SELECT l.title FROM listings l
             WHERE l.vehicle_id = v.id
             ORDER BY LENGTH(l.title) DESC LIMIT 1) AS vehicle_title,
            (SELECT MIN(l2.price) FROM listings l2
             WHERE l2.vehicle_id = v.id AND l2.status = 'active'
             AND l2.price IS NOT NULL) AS vehicle_price
        FROM vehicle_events ve
        JOIN vehicles v ON ve.vehicle_id = v.id
        JOIN watches w ON v.watch_id = w.id
        {where}
        ORDER BY ve.timestamp DESC
        LIMIT ?""",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/feed/count")
def get_feed_count(
    since: str = Query(""),
    conn: Conn = Depends(db_dependency),
):
    if since:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM vehicle_events WHERE timestamp > ?",
            (since,),
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) AS n FROM vehicle_events").fetchone()
    return {"count": row["n"]}


# ── watches ─────────────────────────────────────────────────────────────────


def _watch_with_counts(conn: Conn, row: sqlite3.Row) -> dict:
    w = dict(row)
    stats = conn.execute(
        """SELECT
            COUNT(DISTINCT v.id) AS vehicles,
            SUM(CASE WHEN v.status='active' THEN 1 ELSE 0 END) AS active,
            SUM(CASE WHEN v.status='gone' THEN 1 ELSE 0 END) AS gone
        FROM vehicles v WHERE v.watch_id=?""",
        (row["id"],),
    ).fetchone()
    w["vehicle_count"] = stats["vehicles"] or 0
    w["active_count"] = stats["active"] or 0
    w["gone_count"] = stats["gone"] or 0

    # Health: check last 3 runs for errors
    recent = conn.execute(
        "SELECT errors FROM scrape_runs WHERE watch_id=? AND finished_at IS NOT NULL ORDER BY started_at DESC LIMIT 3",
        (row["id"],),
    ).fetchall()
    if not recent:
        w["health"] = "unknown"
    elif all(r["errors"] for r in recent):
        w["health"] = "failing"
    elif any(r["errors"] for r in recent):
        w["health"] = "degraded"
    else:
        w["health"] = "healthy"

    # Next scheduled run
    from .scheduler import get_scheduled_jobs
    for job in get_scheduled_jobs():
        if job["id"] == f"watch_{row['id']}":
            w["next_run"] = job["next_run"]
            w["in_flight"] = job["in_flight"]
            break
    else:
        w["next_run"] = None
        w["in_flight"] = False

    return w


@app.get("/api/watches", response_model=list[WatchResponse])
def list_watches(conn: Conn = Depends(db_dependency)):
    rows = conn.execute("SELECT * FROM watches ORDER BY created_at DESC").fetchall()
    return [_watch_with_counts(conn, r) for r in rows]


@app.post("/api/watches", status_code=201, response_model=WatchResponse)
def create_watch(body: WatchCreate, conn: Conn = Depends(db_dependency)):
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO watches
        (make, model, location, radius, min_price, max_price,
         min_year, max_year, poll_interval_minutes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            body.make.strip().lower(), body.model.strip().lower(),
            body.location, body.radius, body.min_price, body.max_price,
            body.min_year, body.max_year, body.poll_interval_minutes, now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM watches WHERE id=?", (cur.lastrowid,)).fetchone()
    watch = _watch_with_counts(conn, row)
    if watch["enabled"]:
        schedule_watch(watch)
    return watch


@app.get("/api/watches/{watch_id}", response_model=WatchResponse)
def get_watch(watch_id: int, conn: Conn = Depends(db_dependency)):
    row = conn.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
    if not row:
        raise HTTPException(404, detail="Watch not found")
    return _watch_with_counts(conn, row)


_WATCH_COLUMNS = {
    "make", "model", "location", "radius", "min_price", "max_price",
    "min_year", "max_year", "poll_interval_minutes", "enabled",
}


@app.put("/api/watches/{watch_id}", response_model=WatchResponse)
def update_watch(watch_id: int, body: WatchUpdate, conn: Conn = Depends(db_dependency)):
    row = conn.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
    if not row:
        raise HTTPException(404, detail="Watch not found")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return _watch_with_counts(conn, row)

    if "make" in updates:
        updates["make"] = updates["make"].strip().lower()
    if "model" in updates:
        updates["model"] = updates["model"].strip().lower()
    if "enabled" in updates:
        updates["enabled"] = int(updates["enabled"])

    updates = {k: v for k, v in updates.items() if k in _WATCH_COLUMNS}
    if not updates:
        return _watch_with_counts(conn, row)

    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(
        f"UPDATE watches SET {set_clause} WHERE id=?",
        [*updates.values(), watch_id],
    )
    conn.commit()

    row = conn.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
    watch = _watch_with_counts(conn, row)

    if watch["enabled"]:
        schedule_watch(watch)
    else:
        unschedule_watch(watch_id)
    return watch


@app.delete("/api/watches/{watch_id}", status_code=204)
def delete_watch(watch_id: int, conn: Conn = Depends(db_dependency)):
    conn.execute("DELETE FROM watches WHERE id=?", (watch_id,))
    conn.commit()
    unschedule_watch(watch_id)


@app.post("/api/watches/{watch_id}/poll")
async def poll_watch(watch_id: int, conn: Conn = Depends(db_dependency)):
    """Trigger a scrape and stream progress via SSE.

    Events:
      {"type":"progress","source":"AutoTrader","count":20}
      {"type":"done","result":{...}}
      {"type":"error","message":"..."}
    """
    row = conn.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
    if not row:
        raise HTTPException(404, detail="Watch not found")

    watch = dict(row)
    progress_queue: asyncio.Queue = asyncio.Queue()

    def on_progress(event: dict):
        progress_queue.put_nowait(event)

    async def _run():
        result = await execute_scrape(watch, on_progress=on_progress)
        if result["new"] or result["price_changed"]:
            await send_notification(watch, result)
        return result

    # Run scrape as independent task so it survives client disconnect.
    task = asyncio.create_task(_run())

    async def event_stream():
        try:
            while not task.done():
                try:
                    event = await asyncio.wait_for(progress_queue.get(), timeout=2.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"

            # Drain any remaining progress events
            while not progress_queue.empty():
                yield f"data: {json.dumps(progress_queue.get_nowait())}\n\n"

            # Final result or error
            try:
                result = task.result()
                yield f"data: {json.dumps({'type': 'done', 'result': result})}\n\n"
            except AlreadyInFlight:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Scrape already in progress'})}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        except asyncio.CancelledError:
            pass  # Client disconnected — task continues independently

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── vehicles ────────────────────────────────────────────────────────────────

_VEHICLE_STATUS = {"active", "gone", "all"}
_VEHICLE_SORT = {"best_price", "year", "first_seen_at", "last_seen_at", "listing_count"}


@app.get("/api/watches/{watch_id}/vehicles", response_model=list[VehicleResponse])
def watch_vehicles(
    watch_id: int,
    status: str = Query("active"),
    sort: str = Query("best_price"),
    order: str = Query("asc"),
    conn: Conn = Depends(db_dependency),
):
    if status not in _VEHICLE_STATUS:
        raise HTTPException(422, detail=f"status must be one of {_VEHICLE_STATUS}")

    query = "SELECT * FROM vehicles WHERE watch_id=?"
    params: list = [watch_id]
    if status != "all":
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY id"

    vehicles = []
    for row in conn.execute(query, params).fetchall():
        v = dict(row)
        listings = conn.execute(
            "SELECT source, title, price, status FROM listings WHERE vehicle_id=? ORDER BY price IS NULL, price ASC",
            (row["id"],),
        ).fetchall()
        active_listings = [l for l in listings if l["status"] == "active"]
        prices = [l["price"] for l in active_listings if l["price"] is not None]
        titles = sorted([l["title"] for l in listings], key=len, reverse=True)

        v["best_title"] = titles[0] if titles else ""
        v["best_price"] = min(prices) if prices else None
        v["listing_count"] = len(listings)
        v["sources"] = list({l["source"] for l in listings if l["status"] == "active"})

        # Price change signal
        last_change = conn.execute(
            """SELECT price, old_price FROM vehicle_events
            WHERE vehicle_id=? AND event_type='PRICE_CHANGE'
            ORDER BY timestamp DESC LIMIT 1""",
            (row["id"],),
        ).fetchone()
        if last_change and last_change["price"] and last_change["old_price"]:
            delta = last_change["price"] - last_change["old_price"]
            v["price_direction"] = "down" if delta < 0 else "up"
            v["price_delta"] = abs(delta)
        else:
            v["price_direction"] = None
            v["price_delta"] = None

        # New vehicle flag (first seen within 48h)
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        v["is_new"] = v["first_seen_at"] > cutoff

        vehicles.append(v)

    # Sort in Python since computed columns can't be sorted in SQL
    col = sort if sort in _VEHICLE_SORT else "best_price"
    reverse = order.lower() == "desc"

    def sort_key(v):
        val = v.get(col)
        match val:
            case None:
                return (1, 0)  # NULLs last
            case _:
                return (0, val)

    vehicles.sort(key=sort_key, reverse=reverse)
    return vehicles


@app.get("/api/vehicles/{vehicle_id}", response_model=VehicleDetailResponse)
def get_vehicle(vehicle_id: int, conn: Conn = Depends(db_dependency)):
    row = conn.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
    if not row:
        raise HTTPException(404, detail="Vehicle not found")

    v = dict(row)
    listings = conn.execute(
        "SELECT * FROM listings WHERE vehicle_id=? ORDER BY price IS NULL, price ASC",
        (vehicle_id,),
    ).fetchall()
    events = conn.execute(
        "SELECT * FROM vehicle_events WHERE vehicle_id=? ORDER BY timestamp ASC",
        (vehicle_id,),
    ).fetchall()

    active_listings = [l for l in listings if l["status"] == "active"]
    prices = [l["price"] for l in active_listings if l["price"] is not None]
    titles = sorted([l["title"] for l in listings], key=len, reverse=True)

    v["best_title"] = titles[0] if titles else ""
    v["best_price"] = min(prices) if prices else None
    v["listing_count"] = len(listings)
    v["sources"] = list({l["source"] for l in listings if l["status"] == "active"})
    v["listings"] = [dict(l) for l in listings]
    v["events"] = [dict(e) for e in events]
    return v


# ── raw listings (kept for compatibility) ───────────────────────────────────

_LISTING_STATUS = {"active", "gone", "all"}
_LISTING_SORT = {"price", "year", "first_seen_at", "last_seen_at", "source", "title"}


@app.get("/api/watches/{watch_id}/listings", response_model=list[ListingResponse])
def watch_listings(
    watch_id: int,
    status: str = Query("all"),
    sort: str = Query("price"),
    order: str = Query("asc"),
    conn: Conn = Depends(db_dependency),
):
    if status not in _LISTING_STATUS:
        raise HTTPException(422, detail=f"status must be one of {_LISTING_STATUS}")

    query = "SELECT * FROM listings WHERE watch_id=?"
    params: list = [watch_id]
    if status != "all":
        query += " AND status=?"
        params.append(status)

    col = sort if sort in _LISTING_SORT else "price"
    direction = "DESC" if order.lower() == "desc" else "ASC"
    query += f" ORDER BY {col} IS NULL, {col} {direction}"

    return [dict(r) for r in conn.execute(query, params).fetchall()]


# ── stats ───────────────────────────────────────────────────────────────────


@app.get("/api/watches/{watch_id}/stats", response_model=WatchStatsResponse)
def watch_stats(watch_id: int, conn: Conn = Depends(db_dependency)):
    stats = conn.execute(
        """SELECT
            SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active,
            SUM(CASE WHEN status='gone' THEN 1 ELSE 0 END) AS gone,
            COUNT(*) AS total
        FROM vehicles WHERE watch_id=?""",
        (watch_id,),
    ).fetchone()

    price_changes = conn.execute(
        """SELECT COUNT(*) AS n FROM vehicle_events e
        JOIN vehicles v ON e.vehicle_id = v.id
        WHERE v.watch_id = ? AND e.event_type = 'PRICE_CHANGE'""",
        (watch_id,),
    ).fetchone()

    last_run = conn.execute(
        "SELECT * FROM scrape_runs WHERE watch_id=? ORDER BY started_at DESC LIMIT 1",
        (watch_id,),
    ).fetchone()

    return {
        "active": stats["active"] or 0,
        "gone": stats["gone"] or 0,
        "total_vehicles": stats["total"] or 0,
        "total_price_changes": price_changes["n"],
        "last_run": dict(last_run) if last_run else None,
    }


@app.get("/api/watches/{watch_id}/runs", response_model=list[ScrapeRunResponse])
def watch_runs(
    watch_id: int,
    limit: int = Query(20, ge=1, le=200),
    conn: Conn = Depends(db_dependency),
):
    rows = conn.execute(
        "SELECT * FROM scrape_runs WHERE watch_id=? ORDER BY started_at DESC LIMIT ?",
        (watch_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/runs/{run_id}", response_model=RunDetailResponse)
def get_run(run_id: int, conn: Conn = Depends(db_dependency)):
    row = conn.execute("SELECT * FROM scrape_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, detail="Run not found")
    r = dict(row)
    r["run_events"] = [
        dict(e) for e in conn.execute(
            "SELECT * FROM run_events WHERE run_id=? ORDER BY timestamp ASC", (run_id,)
        ).fetchall()
    ]
    r["vehicle_events"] = [
        dict(e) for e in conn.execute(
            "SELECT * FROM vehicle_events WHERE run_id=? ORDER BY timestamp ASC", (run_id,)
        ).fetchall()
    ]
    return r


# ── settings ────────────────────────────────────────────────────────────────


@app.get("/api/settings")
def get_settings(conn: Conn = Depends(db_dependency)):
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


@app.put("/api/settings")
def update_settings(body: SettingsUpdate, conn: Conn = Depends(db_dependency)):
    for k, v in body.model_dump().items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=?",
            (k, str(v), str(v)),
        )
    conn.commit()
    return {"ok": True}


# ── scheduler visibility ───────────────────────────────────────────────────


@app.get("/api/scheduler/jobs", response_model=list[SchedulerJobResponse])
def scheduler_jobs():
    return get_scheduled_jobs()


# ── catalogue ───────────────────────────────────────────────────────────────


@app.get("/api/catalogue/makes")
def catalogue_makes(conn: Conn = Depends(db_dependency)):
    rows = conn.execute(
        """SELECT cm.id, cm.canonical_name AS name, cm.normalized,
                  COUNT(cmo.id) AS model_count
        FROM catalogue_makes cm
        LEFT JOIN catalogue_models cmo ON cmo.make_id = cm.id
        GROUP BY cm.id ORDER BY cm.canonical_name"""
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/catalogue/makes/{make_id}")
def catalogue_make_detail(make_id: int, conn: Conn = Depends(db_dependency)):
    row = conn.execute(
        "SELECT * FROM catalogue_makes WHERE id=?", (make_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, detail="Make not found")
    make = dict(row)

    # Per-source aliases for this make
    aliases = conn.execute(
        "SELECT source, source_make, source_make_id FROM catalogue_source_aliases WHERE make_id=? AND model_id IS NULL ORDER BY source",
        (make_id,),
    ).fetchall()
    make["source_aliases"] = [dict(a) for a in aliases]

    # Models with their source aliases
    models = []
    for m in conn.execute(
        "SELECT id, canonical_name AS name, normalized FROM catalogue_models WHERE make_id=? ORDER BY canonical_name",
        (make_id,),
    ).fetchall():
        md = dict(m)
        ma = conn.execute(
            "SELECT source, source_model, source_model_id FROM catalogue_source_aliases WHERE make_id=? AND model_id=? ORDER BY source",
            (make_id, m["id"]),
        ).fetchall()
        md["source_aliases"] = [dict(a) for a in ma]
        models.append(md)
    make["models"] = models
    return make


@app.get("/api/catalogue/makes/{make_id}/models")
def catalogue_models(make_id: int, conn: Conn = Depends(db_dependency)):
    rows = conn.execute(
        "SELECT id, canonical_name AS name, normalized FROM catalogue_models WHERE make_id=? ORDER BY canonical_name",
        (make_id,),
    ).fetchall()
    result = []
    for r in rows:
        sources = conn.execute(
            "SELECT DISTINCT source FROM catalogue_source_aliases WHERE make_id=? AND model_id=?",
            (make_id, r["id"]),
        ).fetchall()
        d = dict(r)
        d["sources"] = [s["source"] for s in sources]
        result.append(d)
    return result


@app.post("/api/catalogue/harvest")
async def trigger_harvest(conn: Conn = Depends(db_dependency)):
    from carsearch.catalogue import run_harvest
    results = await run_harvest(conn)
    return results


@app.get("/api/catalogue/harvest/status")
def harvest_status(conn: Conn = Depends(db_dependency)):
    rows = conn.execute(
        """SELECT source, status, makes_found, models_found, started_at, finished_at, error
        FROM catalogue_harvest_runs ORDER BY started_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


# ── static files ────────────────────────────────────────────────────────────


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
