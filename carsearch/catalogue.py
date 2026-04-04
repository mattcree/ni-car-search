"""Make/model catalogue: harvest, normalize, merge, resolve.

Provides a unified catalogue of car makes and models harvested from
multiple listing sites, with per-source aliases for correct querying.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

import requests
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from .base import SourceParams

log = logging.getLogger(__name__)


# ── normalization ───────────────────────────────────────────────────────────

# Known brand name variations that normalization alone can't resolve.
KNOWN_MAKE_ALIASES: dict[str, str] = {
    "mercedes": "mercedes-benz",
    "merc": "mercedes-benz",
    "vw": "volkswagen",
    "chevy": "chevrolet",
    "landrover": "land rover",
    "rangerover": "land rover",
    "alfa": "alfa romeo",
    "aston": "aston martin",
    "rolls": "rolls-royce",
    "rollsroyce": "rolls-royce",
}


def normalize(name: str) -> str:
    """Reduce a make or model name to a canonical matching key."""
    s = name.lower().strip()
    s = re.sub(r"\s*\([\d,]+\)\s*$", "", s)  # strip count suffixes like "(125)" or "(2,280)"
    s = s.replace("-", "").replace(".", "").replace(" ", "")
    return s


def _resolve_alias(normalized: str) -> str:
    """Check known aliases and return the canonical normalized form."""
    if normalized in KNOWN_MAKE_ALIASES:
        return normalize(KNOWN_MAKE_ALIASES[normalized])
    return normalized


def _find_make(conn, normalized: str) -> dict | None:
    """Find a catalogue make by normalized key, checking aliases."""
    # Direct match
    row = conn.execute(
        "SELECT * FROM catalogue_makes WHERE normalized=?", (normalized,)
    ).fetchone()
    if row:
        return dict(row)

    # Check known alias
    aliased = _resolve_alias(normalized)
    if aliased != normalized:
        row = conn.execute(
            "SELECT * FROM catalogue_makes WHERE normalized=?", (aliased,)
        ).fetchone()
        if row:
            return dict(row)

    # Prefix match (min 4 chars): "mercedes" matches "mercedesbenz"
    if len(normalized) >= 4:
        row = conn.execute(
            "SELECT * FROM catalogue_makes WHERE normalized LIKE ? OR ? LIKE normalized || '%' ORDER BY LENGTH(normalized) DESC LIMIT 1",
            (normalized + "%", normalized),
        ).fetchone()
        if row:
            return dict(row)

    return None


# ── merge logic ─────────────────────────────────────────────────────────────

# Source priority for choosing canonical display names.
SOURCE_PRIORITY = {
    "UsedCarsNI": 0,
    "AutoTrader": 1,
    "Motors": 2,
    "NIVehicleSales": 3,
    "Gumtree": 4,
}


def merge_into_catalogue(
    conn,
    source: str,
    makes: list[dict],
) -> tuple[int, int]:
    """Merge harvested make/model data into the catalogue.

    Each entry in *makes* should have:
        name: str           — display name (e.g. "Honda")
        source_id: str|None — opaque ID for this source (e.g. "47")
        models: list[dict]  — each with name, source_id

    Returns (makes_added, models_added).
    """
    now = datetime.now(timezone.utc).isoformat()
    makes_added = 0
    models_added = 0
    current_priority = SOURCE_PRIORITY.get(source, 99)

    for make_data in makes:
        make_name = make_data["name"]
        make_norm = normalize(make_name)
        make_source_id = make_data.get("source_id")

        # Find or create the canonical make
        existing = _find_make(conn, make_norm)
        if existing:
            make_id = existing["id"]
            # Update canonical name if this source has higher priority
            existing_norm = existing["normalized"]
            existing_source = conn.execute(
                "SELECT source FROM catalogue_source_aliases WHERE make_id=? ORDER BY rowid LIMIT 1",
                (make_id,),
            ).fetchone()
            if existing_source:
                existing_priority = SOURCE_PRIORITY.get(existing_source["source"], 99)
                if current_priority < existing_priority:
                    conn.execute(
                        "UPDATE catalogue_makes SET canonical_name=? WHERE id=?",
                        (make_name, make_id),
                    )
        else:
            # Resolve alias before creating
            resolved_norm = _resolve_alias(make_norm)
            existing = _find_make(conn, resolved_norm)
            if existing:
                make_id = existing["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO catalogue_makes (canonical_name, normalized, created_at) VALUES (?, ?, ?)",
                    (make_name, resolved_norm, now),
                )
                make_id = cur.lastrowid
                makes_added += 1

        # Upsert make-level alias
        conn.execute(
            """INSERT INTO catalogue_source_aliases
            (source, make_id, model_id, source_make, source_make_id)
            VALUES (?, ?, NULL, ?, ?)
            ON CONFLICT(source, make_id, model_id) DO UPDATE
            SET source_make=?, source_make_id=?""",
            (source, make_id, make_name, make_source_id,
             make_name, make_source_id),
        )

        # Process models
        for model_data in make_data.get("models", []):
            model_name = model_data["name"]
            model_norm = normalize(model_name)
            model_source_id = model_data.get("source_id")

            if not model_norm:
                continue

            # Find or create model
            model_row = conn.execute(
                "SELECT * FROM catalogue_models WHERE make_id=? AND normalized=?",
                (make_id, model_norm),
            ).fetchone()
            if model_row:
                model_id = model_row["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO catalogue_models (make_id, canonical_name, normalized, created_at) VALUES (?, ?, ?, ?)",
                    (make_id, model_name, model_norm, now),
                )
                model_id = cur.lastrowid
                models_added += 1

            # Upsert model-level alias
            conn.execute(
                """INSERT INTO catalogue_source_aliases
                (source, make_id, model_id, source_make, source_model,
                 source_make_id, source_model_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, make_id, model_id) DO UPDATE
                SET source_make=?, source_model=?, source_make_id=?, source_model_id=?""",
                (source, make_id, model_id, make_name, model_name,
                 make_source_id, model_source_id,
                 make_name, model_name, make_source_id, model_source_id),
            )

    conn.commit()
    return makes_added, models_added


# ── resolve (used during scraping) ──────────────────────────────────────────


def resolve_source_params(conn, make_normalized: str, model_normalized: str) -> dict[str, SourceParams]:
    """Look up per-source params for a make/model from the catalogue.

    Returns a dict mapping source name to SourceParams. Sources with no
    alias for this make/model are absent (scraper will be skipped or
    fall back to free text).
    """
    rows = conn.execute(
        """SELECT csa.source, csa.source_make, csa.source_model,
                  csa.source_make_id, csa.source_model_id
        FROM catalogue_source_aliases csa
        JOIN catalogue_makes cm ON csa.make_id = cm.id
        JOIN catalogue_models cmo ON csa.model_id = cmo.id
        WHERE cm.normalized = ? AND cmo.normalized = ?""",
        (normalize(make_normalized), normalize(model_normalized)),
    ).fetchall()

    result: dict[str, SourceParams] = {}
    for r in rows:
        result[r["source"]] = SourceParams(
            make=r["source_make"],
            model=r["source_model"] or "",
            make_id=r["source_make_id"],
            model_id=r["source_model_id"],
        )
    return result


# ── harvesters ──────────────────────────────────────────────────────────────


async def harvest_usedcarsni() -> list[dict]:
    """Harvest all makes and models from UsedCarsNI's dropdowns."""
    log.info("Harvesting UsedCarsNI makes/models...")
    stealth = Stealth()

    async with async_playwright() as p:
        stealth.hook_playwright_context(p)
        browser = await p.chromium.launch(headless=True, channel="chrome")
        page = await browser.new_page()

        await page.goto("https://www.usedcarsni.com", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # Dismiss cookies
        for sel in ['button:has-text("Accept")', ".fc-cta-consent"]:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(1000)
                break

        # Read all makes
        make_select = await page.query_selector('select[name="make"]')
        if not make_select:
            await browser.close()
            raise RuntimeError("Could not find make dropdown on UsedCarsNI")

        make_options = await make_select.query_selector_all("option")
        makes = []

        # Entries to skip (non-make options in the dropdown)
        skip = {"not sure", "search by class", "any make"}

        for opt in make_options:
            val = await opt.get_attribute("value") or "0"
            if val == "0":
                continue
            text = (await opt.inner_text()).strip()
            name = re.sub(r"\s*\([\d,]+\)\s*$", "", text).strip()
            if any(s in name.lower() for s in skip):
                continue
            makes.append({"name": name, "source_id": val, "models": []})

        log.info("UsedCarsNI: found %d makes, reading models...", len(makes))

        # For each make, select it and read model dropdown
        for make in makes:
            await make_select.select_option(value=make["source_id"])
            await page.wait_for_timeout(1500)

            model_select = await page.query_selector('select[name="model"]')
            if not model_select:
                continue

            model_options = await model_select.query_selector_all("option")
            for opt in model_options:
                val = await opt.get_attribute("value") or "0"
                if val == "0":
                    continue
                text = (await opt.inner_text()).strip()
                name = re.sub(r"\s*\([\d,]+\)\s*$", "", text).strip()
                make["models"].append({"name": name, "source_id": val})

        await browser.close()

    total_models = sum(len(m["models"]) for m in makes)
    log.info("UsedCarsNI: harvested %d makes, %d models", len(makes), total_models)
    return makes


def harvest_nivehiclesales() -> list[dict]:
    """Harvest distinct makes/models from the NIVehicleSales Supabase API."""
    from .scrapers.nivehiclesales import API_URL, HEADERS

    log.info("Harvesting NIVehicleSales makes/models...")

    resp = requests.get(
        API_URL,
        headers=HEADERS,
        params={"select": "make,model", "order": "make,model", "limit": 10000},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    # Group by make
    makes_map: dict[str, dict] = {}
    for item in data:
        make_name = (item.get("make") or "").strip()
        model_name = (item.get("model") or "").strip()
        if not make_name or not model_name:
            continue

        if make_name not in makes_map:
            makes_map[make_name] = {"name": make_name, "source_id": None, "models": {}}
        makes_map[make_name]["models"][model_name] = {"name": model_name, "source_id": None}

    makes = []
    for m in makes_map.values():
        makes.append({
            "name": m["name"],
            "source_id": m["source_id"],
            "models": list(m["models"].values()),
        })

    total_models = sum(len(m["models"]) for m in makes)
    log.info("NIVehicleSales: harvested %d makes, %d models", len(makes), total_models)
    return makes


async def harvest_motors() -> list[dict]:
    """Harvest makes/models from Motors.co.uk embedded page data.

    Motors embeds the full taxonomy in a ``window.m.Store.setSearchPanel()``
    call containing a ``MakeModels`` array with nested ``Models`` arrays.
    """
    log.info("Harvesting Motors makes/models...")
    stealth = Stealth()

    async with async_playwright() as p:
        stealth.hook_playwright_context(p)
        browser = await p.chromium.launch(headless=True, channel="chrome")
        page = await browser.new_page()

        await page.goto("https://www.motors.co.uk/search/car/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)

        # Extract MakeModels from the embedded setSearchPanel JSON.
        # The JSON is huge so we use brace-depth tracking to find the end.
        make_models = await page.evaluate("""() => {
            const scripts = [...document.querySelectorAll('script')];
            for (const s of scripts) {
                const text = s.textContent;
                const idx = text.indexOf('setSearchPanel(');
                if (idx === -1) continue;
                const start = text.indexOf('{', idx);
                let depth = 0;
                for (let i = start; i < text.length; i++) {
                    if (text[i] === '{') depth++;
                    if (text[i] === '}') depth--;
                    if (depth === 0) {
                        try {
                            const data = JSON.parse(text.slice(start, i + 1));
                            if (data.MakeModels) {
                                return data.MakeModels.map(m => ({
                                    name: m.Name || m.Value,
                                    models: (m.Models || []).map(mo => ({ name: mo.Name || mo.Value }))
                                }));
                            }
                        } catch(e) {}
                        break;
                    }
                }
            }
            return [];
        }""")

        await browser.close()

    makes = [
        {"name": mm["name"], "source_id": None,
         "models": [{"name": m["name"], "source_id": None} for m in mm.get("models", [])]}
        for mm in make_models
    ]

    total_models = sum(len(m["models"]) for m in makes)
    log.info("Motors: harvested %d makes, %d models", len(makes), total_models)
    return makes


def derive_aliases_for_source(conn, source: str, transform) -> tuple[int, int]:
    """Create aliases for a source by transforming existing catalogue names.

    Used for sites where we can't directly harvest (e.g. AutoTrader's SPA)
    but know the naming convention. *transform(canonical_name)* should
    return the string the source expects.
    """
    log.info("Deriving %s aliases from existing catalogue...", source)
    makes_done = 0
    models_done = 0

    for make in conn.execute("SELECT * FROM catalogue_makes").fetchall():
        source_make = transform(make["canonical_name"])
        conn.execute(
            """INSERT INTO catalogue_source_aliases
            (source, make_id, model_id, source_make, source_make_id)
            VALUES (?, ?, NULL, ?, NULL)
            ON CONFLICT(source, make_id, model_id) DO UPDATE SET source_make=?""",
            (source, make["id"], source_make, source_make),
        )
        makes_done += 1

        for model in conn.execute(
            "SELECT * FROM catalogue_models WHERE make_id=?", (make["id"],)
        ).fetchall():
            source_model = transform(model["canonical_name"])
            conn.execute(
                """INSERT INTO catalogue_source_aliases
                (source, make_id, model_id, source_make, source_model)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, make_id, model_id) DO UPDATE
                SET source_make=?, source_model=?""",
                (source, make["id"], model["id"], source_make, source_model,
                 source_make, source_model),
            )
            models_done += 1

    conn.commit()
    log.info("%s: derived %d make aliases, %d model aliases", source, makes_done, models_done)
    return makes_done, models_done


def harvest_autotrader_derived(conn) -> tuple[int, int]:
    """Derive AutoTrader aliases from existing catalogue (Title Case names)."""
    return derive_aliases_for_source(conn, "AutoTrader", lambda name: name)


def harvest_gumtree_derived(conn) -> tuple[int, int]:
    """Derive Gumtree aliases from existing catalogue (lowercase names)."""
    return derive_aliases_for_source(conn, "Gumtree", lambda name: name.lower())


# ── orchestrator ────────────────────────────────────────────────────────────


async def run_harvest(conn, sources: list[str] | None = None):
    """Run harvest for the specified sources (or all available).

    Results are merged into the catalogue tables via *conn*.
    """
    now = datetime.now(timezone.utc).isoformat()
    # Real harvesters: scrape the source directly
    harvesters = {
        "UsedCarsNI": harvest_usedcarsni,
        "NIVehicleSales": harvest_nivehiclesales,
        "Motors": harvest_motors,
    }
    # Derived harvesters: generate aliases from existing catalogue data.
    # Run AFTER real harvesters so the catalogue is populated.
    derived = {
        "AutoTrader": harvest_autotrader_derived,
        "Gumtree": harvest_gumtree_derived,
    }

    all_sources = {**harvesters, **derived}
    targets = sources or list(all_sources.keys())
    results = {}

    # Phase 1: real harvesters
    for source in targets:
        harvester = harvesters.get(source)
        if not harvester:
            continue

        cur = conn.execute(
            "INSERT INTO catalogue_harvest_runs (source, started_at, status) VALUES (?, ?, 'running')",
            (source, now),
        )
        run_id = cur.lastrowid
        conn.commit()

        try:
            if asyncio.iscoroutinefunction(harvester):
                makes = await harvester()
            else:
                makes = harvester()

            makes_added, models_added = merge_into_catalogue(conn, source, makes)

            total_makes = len(makes)
            total_models = sum(len(m.get("models", [])) for m in makes)
            conn.execute(
                """UPDATE catalogue_harvest_runs
                SET finished_at=?, makes_found=?, models_found=?, status='completed'
                WHERE id=?""",
                (datetime.now(timezone.utc).isoformat(), total_makes, total_models, run_id),
            )
            conn.commit()
            results[source] = {"status": "completed", "makes": total_makes, "models": total_models}
            log.info("Harvest %s: %d makes, %d models (%d new makes, %d new models)",
                     source, total_makes, total_models, makes_added, models_added)

        except Exception as exc:
            conn.execute(
                "UPDATE catalogue_harvest_runs SET finished_at=?, status='failed', error=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), str(exc), run_id),
            )
            conn.commit()
            results[source] = {"status": "failed", "error": str(exc)}
            log.exception("Harvest failed for %s", source)

    # Phase 2: derived harvesters (need catalogue populated first)
    for source in targets:
        deriver = derived.get(source)
        if not deriver:
            continue

        cur = conn.execute(
            "INSERT INTO catalogue_harvest_runs (source, started_at, status) VALUES (?, ?, 'running')",
            (source, now),
        )
        run_id = cur.lastrowid
        conn.commit()

        try:
            makes_done, models_done = deriver(conn)
            conn.execute(
                """UPDATE catalogue_harvest_runs
                SET finished_at=?, makes_found=?, models_found=?, status='completed'
                WHERE id=?""",
                (datetime.now(timezone.utc).isoformat(), makes_done, models_done, run_id),
            )
            conn.commit()
            results[source] = {"status": "completed", "makes": makes_done, "models": models_done}
        except Exception as exc:
            conn.execute(
                "UPDATE catalogue_harvest_runs SET finished_at=?, status='failed', error=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), str(exc), run_id),
            )
            conn.commit()
            results[source] = {"status": "failed", "error": str(exc)}
            log.exception("Derived harvest failed for %s", source)

    return results
