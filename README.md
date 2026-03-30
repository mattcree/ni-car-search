# NI Car Search

Search 6 car listing sites simultaneously from the command line. Built for finding cars in Northern Ireland.

**Sites scraped:** UsedCarsNI, AutoTrader, Motors.co.uk, Gumtree, Cars.ni, NI Vehicle Sales

## What it does

Runs headless Chrome with Playwright to scrape all sites in parallel, streaming results to your terminal as pages are scraped. Paginates through every page so nothing gets missed. Detects cross-site duplicates and tracks changes between runs.

```
$ .venv/bin/python -m carsearch skoda superb --location belfast --radius 80

Searching for Skoda Superb in Belfast (80 miles)...

  AutoTrader: 20 listings
    AutoTrader      £4,995  2016   182,000 miles  Hatchback     Auto    Ballyclare (12 mi)      Skoda Superb 2.0 TDI SE Business Euro 6 (s/s) 5dr
                    https://www.autotrader.co.uk/car-details/202602119847345
  ...
  UsedCarsNI: 20 listings
    UsedCarsNI      £3,200  2015    219196 Miles  Saloon        Man     County Armagh            Jun 2015 Skoda Superb 2.0 TDI CR 140 SE 5dr DSG
                    https://www.usedcarsni.com/2015-Skoda-Superb-2-0-TDI-CR-140-SE-5dr-DSG-396881305
  ...
  Motors: 22 listings
  Gumtree: 8 listings
  CarsNI: 25 listings
  NIVehicleSales: 10 listings

182 total listings (21 probable duplicates across sites)

Probable duplicates (21 cars on multiple sites):
  #1
    AutoTrader      £17,795  2022    24,742 miles  ...  Belfast (3 miles)
    UsedCarsNI      £17,795  2022     24742 Miles  ...  Belfast

Changes since 2026-03-29T21:12:28+00:00:
  150 unchanged, +12 new, -3 gone, 2 price changed

  Snapshot saved: ~/.carsearch/snapshots/skoda_superb_belfast_80mi.json
```

## Install

Requires Python 3.10+ and Google Chrome.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

## Usage

```bash
# Basic search
.venv/bin/python -m carsearch skoda superb

# With location and radius
.venv/bin/python -m carsearch vw golf --location belfast --radius 80

# With price, year, and page filters
.venv/bin/python -m carsearch bmw 3-series --min-price 5000 --max-price 15000 --min-year 2018

# Limit pages per site for faster results
.venv/bin/python -m carsearch ford focus --max-pages 2

# Skip snapshot save/compare
.venv/bin/python -m carsearch skoda kodiaq --no-snapshot

# Common aliases work
.venv/bin/python -m carsearch merc c-class    # Mercedes-Benz
.venv/bin/python -m carsearch beemer 3-series # BMW
.venv/bin/python -m carsearch landy discovery # Land Rover
```

### Options

| Flag | Description |
|---|---|
| `--location` | City name or BT postcode (default: `northern-ireland`) |
| `--radius` | Search radius in miles (default: no limit) |
| `--min-price` | Minimum price |
| `--max-price` | Maximum price |
| `--min-year` | Minimum year |
| `--max-year` | Maximum year |
| `--max-pages` | Max pages to scrape per site (default: no limit) |
| `--no-snapshot` | Don't save or compare snapshots |

### Locations

Accepts any NI town name (belfast, derry, newry, lisburn, bangor, ballymena, omagh, enniskillen, coleraine, etc.) or a BT postcode directly.

## Data extracted per listing

Each listing includes: source site, title, price, year, mileage, body style (estate/hatchback/saloon), transmission (auto/manual), location, and a direct link to the listing.

## Features

### Snapshot diffing

Every run saves results to `~/.carsearch/snapshots/`. On subsequent runs, compares against the previous snapshot and shows:
- **New** listings that weren't there before
- **Gone** listings that have been removed
- **Price changes** on the same listing

Use `--no-snapshot` to skip this.

### Cross-site duplicate detection

Dealers list the same car on multiple sites. The tool detects probable duplicates by matching on year + mileage (within 500 tolerance) + similar location across different sources.

## How each site is scraped

### UsedCarsNI

Uses opaque numeric IDs for make and model. The scraper loads the homepage, selects the make from the dropdown, waits for the model dropdown to populate via JS, and reads the options to resolve the correct model ID. Results are precise (no keyword matching).

Pagination via `&pagepc0=N`, 20 results per page. Stops when the "Next" link disappears.

### AutoTrader

Full SPA that renders via GraphQL. Delivery-only results (Cinch etc.) are excluded as they don't ship to NI.

Pagination via `&page=N`, ~25 results per page.

### Motors.co.uk

Part of the MOTORS network (same as Gumtree, Cazoo, eBay Motors). Session-based search with distance filtering. JS-based pagination to avoid modal overlay issues.

### Gumtree

Uses structured `vehicle_make` and `vehicle_model` URL params. Only collects local results, excluding the "Results from outside your search" section.

Pagination via `&page=N`. Stops when the forward-page button disappears.

### Cars.ni

NI-specific WordPress site. Cloudflare requires stealth browser. URL-based make/model filtering with standard pagination.

### NI Vehicle Sales

Direct Supabase REST API - no browser needed. 2,600+ NI dealer listings with PostgREST filtering.

## Adding a new site

Drop a file in `carsearch/scrapers/` with a class that extends `Scraper`:

```python
from ..base import Filters, Listing, Scraper

class NewSiteScraper(Scraper):
    name = "NewSite"
    needs_browser = True  # False if using requests/API

    def build_url(self, make, model, filters):
        ...

    async def scrape(self, page, make, model, filters, on_page=None):
        results = []
        # ... extract listings ...
        if on_page:
            on_page(results)  # emit results as they arrive
        return results
```

It gets auto-discovered. No other files need editing.

## Docker

```bash
docker build -t carsearch .
docker run carsearch skoda superb --location belfast --radius 80
```
