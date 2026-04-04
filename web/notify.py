from __future__ import annotations

import logging

import httpx

from .db import get_db

log = logging.getLogger(__name__)


def _get_settings() -> dict[str, str]:
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def _format_price(p: int | None) -> str:
    if p is None:
        return "?"
    return f"\u00a3{p:,}"


def _build_body(run_id: int) -> str:
    """Build a detailed notification body from the run's vehicle events."""
    with get_db() as conn:
        events = conn.execute(
            """SELECT ve.event_type, ve.price, ve.old_price, ve.source,
                      v.year, v.mileage_bucket, v.transmission,
                      (SELECT l.title FROM listings l
                       WHERE l.vehicle_id = v.id
                       ORDER BY LENGTH(l.title) DESC LIMIT 1) AS title
            FROM vehicle_events ve
            JOIN vehicles v ON ve.vehicle_id = v.id
            WHERE ve.run_id = ?
            AND ve.event_type IN ('FOUND', 'PRICE_CHANGE', 'RETURNED')
            ORDER BY ve.event_type, ve.price ASC""",
            (run_id,),
        ).fetchall()

    if not events:
        return ""

    lines = []
    current_type = None

    for e in events:
        if e["event_type"] != current_type:
            current_type = e["event_type"]
            label = {"FOUND": "New", "PRICE_CHANGE": "Price changed", "RETURNED": "Returned"}
            lines.append(f"\n{label.get(current_type, current_type)}:")

        year = e["year"] or "?"
        miles = f"~{e['mileage_bucket']}k" if e["mileage_bucket"] else "?"
        source = e["source"] or "?"

        if current_type == "PRICE_CHANGE":
            lines.append(f"  {year} {miles}mi {_format_price(e['old_price'])}\u2192{_format_price(e['price'])} ({source})")
        else:
            lines.append(f"  {year} {miles}mi {_format_price(e['price'])} ({source})")

    return "\n".join(lines)


async def send_notification(watch: dict, result: dict) -> None:
    """Send a push notification via ntfy when a scrape finds changes."""
    settings = _get_settings()
    ntfy_url = settings.get("ntfy_url", "").rstrip("/")
    ntfy_topic = settings.get("ntfy_topic", "")
    app_url = settings.get("app_url", "").rstrip("/")  # e.g. http://192.168.1.50:8000

    if not ntfy_url or not ntfy_topic:
        return

    summary = []
    if result["new"]:
        summary.append(f"{result['new']} new")
    if result["price_changed"]:
        n = result["price_changed"]
        summary.append(f"{n} price change{'s' if n != 1 else ''}")
    if result["returned"]:
        summary.append(f"{result['returned']} returned")

    if not summary:
        return

    title = f"{watch['make'].title()} {watch['model'].title()}: {', '.join(summary)}"
    body = _build_body(result.get("run_id"))

    try:
        headers = {
            "Title": title,
            "Tags": "car",
            "Priority": "high" if result["new"] else "default",
        }
        # Tap notification to open the app at this watch
        if app_url and watch.get("id"):
            headers["Click"] = f"{app_url}/#watch/{watch['id']}"
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{ntfy_url}/{ntfy_topic}",
                content=body or ", ".join(summary),
                headers=headers,
                timeout=10,
            )
    except Exception as e:
        log.warning("Failed to send notification: %s", e)
