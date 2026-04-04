from __future__ import annotations

import logging

import httpx

from .db import get_db

log = logging.getLogger(__name__)


def _get_settings() -> dict[str, str]:
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


async def send_notification(watch: dict, result: dict) -> None:
    """Send a push notification via ntfy when a scrape finds changes."""
    settings = _get_settings()
    ntfy_url = settings.get("ntfy_url", "").rstrip("/")
    ntfy_topic = settings.get("ntfy_topic", "")

    if not ntfy_url or not ntfy_topic:
        return

    parts = []
    if result["new"]:
        parts.append(f"{result['new']} new")
    if result["price_changed"]:
        n = result["price_changed"]
        parts.append(f"{n} price change{'s' if n != 1 else ''}")
    if result["gone"]:
        parts.append(f"{result['gone']} gone")
    if result["returned"]:
        parts.append(f"{result['returned']} returned")

    if not parts:
        return

    title = f"{watch['make'].title()} {watch['model'].title()}"
    body = ", ".join(parts)

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{ntfy_url}/{ntfy_topic}",
                content=body,
                headers={"Title": title, "Tags": "car,mag"},
                timeout=10,
            )
    except Exception as e:
        log.warning("Failed to send notification: %s", e)
