# NI Car Search

Monitor car listings across 5 Northern Irish and UK sites. Runs as a self-hosted web app with automated polling, cross-site deduplication, price tracking, and push notifications.

**Sites scraped:** UsedCarsNI, AutoTrader, Motors.co.uk, Gumtree, NI Vehicle Sales

## What it does

- **Watches** — save a make/model/location search and it polls automatically on a jittered schedule
- **Cross-site dedup** — content-addressed fingerprinting groups the same physical car across sites
- **Price tracking** — every price change is recorded with full event history per vehicle
- **Activity feed** — see what changed across all watches since you last looked
- **Push notifications** — ntfy.sh integration sends alerts to your phone for new listings and price drops
- **Make/model catalogue** — harvested from all 5 sources with per-site name/ID aliases
- **Fuel type detection** — extracts fuel type from structured data and engine codes in titles

## Deploy to Proxmox

One command on your Proxmox host creates an LXC container with everything running:

```bash
bash <(curl -sL https://raw.githubusercontent.com/mattcree/ni-car-search/main/setup-lxc.sh)
```

Customise with environment variables:

```bash
CTID=201 STORAGE=local-zfs bash <(curl -sL ...)
```

**Update later:**

```bash
pct exec 200 -- carsearch-update
```

**Other commands:**

```bash
pct enter 200                                      # shell into container
pct exec 200 -- journalctl -u carsearch -f         # tail logs
pct exec 200 -- systemctl restart carsearch        # restart
```

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/playwright install chromium
.venv/bin/python -m web
```

Open `http://localhost:8000`.

## Docker

```bash
docker compose up -d
```

Or for the CLI only:

```bash
docker build -t carsearch .
docker run carsearch skoda superb --location belfast --radius 80
```

## Web app

### Feed

The home view shows a chronological stream of events across all watches — new listings, price drops, gone vehicles. Answers "anything interesting since I last looked?"

### Watches

Each watch tracks a make + model with filters (location, radius, year range, price range). Polling runs on a jittered schedule (e.g. 30min ±20%) to avoid detection.

The vehicle table shows cross-site grouped results with signal stripes (green = new, gold = price changed) and inline price deltas.

### Poll history

Every scrape run is logged with per-scraper status (success/warning/error with counts), vehicle-level changes (FOUND, PRICE_CHANGE, GONE, RETURNED), and a full operational event log.

### Catalogue

Harvested make/model lists from all 5 sources. Shows per-source name and ID aliases. Used to populate dropdowns and resolve correct names when scraping each site.

Sync from the Catalogue page or via API:

```bash
curl -X POST http://localhost:8000/api/catalogue/harvest
```

### Notifications

Configure ntfy.sh in Settings:
1. Set server URL (`https://ntfy.sh` or self-hosted)
2. Set a topic name
3. Install the ntfy app on your phone and subscribe to the same topic

Notifications fire when a poll finds new listings or price changes.

## CLI

The original CLI still works:

```bash
.venv/bin/python -m carsearch skoda superb --location belfast --radius 80
```

### Options

| Flag | Description |
|---|---|
| `--location` | City name or BT postcode (default: `lisburn`) |
| `--radius` | Search radius in miles (default: `80`) |
| `--min-price` | Minimum price |
| `--max-price` | Maximum price |
| `--min-year` | Minimum year |
| `--max-year` | Maximum year |
| `--max-pages` | Max pages per site (default: no limit) |
| `--no-snapshot` | Don't save or compare snapshots |
| `--json` | Output as JSON |
| `--stream` | Show per-source results as they arrive |

### Locations

Accepts NI towns (belfast, lisburn, derry, newry, bangor, ballymena, omagh, enniskillen, etc.), UK capitals (london, edinburgh, cardiff), major GB cities, or a BT/UK postcode directly.

## Architecture

```
carsearch/              CLI scraper package (unchanged, works standalone)
  base.py               Listing, Filters, SourceParams, locations, fuel detection
  runner.py             Async orchestrator — browser + request scrapers in parallel
  catalogue.py          Make/model harvesting, normalization, merge, resolve
  scrapers/             One file per site, auto-discovered
    autotrader.py
    motors.py
    gumtree.py
    usedcarsni.py
    nivehiclesales.py

web/                    FastAPI web app
  app.py                API endpoints, SSE streaming for polls
  db.py                 SQLite schema (vehicles, listings, events, catalogue)
  scrape_job.py         Scrape-to-DB bridge with fingerprinting and match/case
  scheduler.py          APScheduler with jitter, concurrency guard
  notify.py             ntfy.sh push notifications
  config.py             Environment variable config
  models.py             Pydantic request/response models
  static/               Vanilla HTML/CSS/JS frontend
```

### Data model

- **Vehicles** — physical cars, identified by content fingerprint (year:mileage_band:transmission)
- **Listings** — individual URLs from individual sites, linked to vehicles
- **Vehicle events** — FOUND, NEW_SOURCE, PRICE_CHANGE, SOURCE_GONE, GONE, RETURNED
- **Scrape runs** — per-poll record with counts, errors, and per-scraper breakdown
- **Run events** — operational log (scraper start/progress/done/error)

Cross-site dedup: same fingerprint + different source = same car. Same fingerprint + same source = different cars.

## Adding a new scraper

Drop a file in `carsearch/scrapers/`:

```python
from ..base import Filters, Listing, Scraper, detect_fuel, normalise_fuel

class NewSiteScraper(Scraper):
    name = "NewSite"
    needs_browser = True  # False for API-based

    def build_url(self, make, model, filters):
        ...

    async def scrape(self, page, make, model, filters, on_page=None, source_params=None):
        results = []
        # ... extract Listing objects ...
        if on_page:
            on_page(results)
        return results
```

Auto-discovered. No other files need editing.
