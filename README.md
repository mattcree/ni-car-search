# NI Car Search

Search Gumtree, UsedCarsNI and AutoTrader simultaneously from the command line. Built for finding cars in Northern Ireland.

## What it does

Runs headless Chrome with Playwright to scrape all three sites in parallel, streaming results to your terminal as they come in. Paginates through every page of results so nothing gets missed.

```
$ python -m carsearch skoda superb --location belfast --radius 80

Searching for Skoda Superb in Belfast (80 miles)...

  AutoTrader: 21 listings found
      £3,900  2011   111,490 miles  Antrim (14 miles)     Skoda Superb 1.8 TSI SE DSG Euro 5 5dr
           https://www.autotrader.co.uk/car-details/202602280287957
      £4,995  2016   182,000 miles  Ballyclare (12 miles) Skoda Superb 2.0 TDI SE Business Euro 6 5dr
           https://www.autotrader.co.uk/car-details/202602119847345
  ...

  UsedCarsNI: 20 listings found
      £3,200  2015    219196 Miles  County Armagh         Jun 2015 Skoda Superb 2.0 TDI CR 140 SE 5dr DSG
           https://www.usedcarsni.com/2015-Skoda-Superb-2-0-TDI-CR-140-SE-5dr-DSG-396881305
  ...

  Gumtree: 8 listings found
      £2,000  2015       220 miles  County Antrim         2015 skoda
           https://www.gumtree.com/p/skoda/2015-skoda/1511726553
  ...

112 total listings
```

## Install

Requires Python 3.10+ and Google Chrome installed on your system.

```bash
pip install requests playwright playwright-stealth rich
playwright install chromium
```

## Usage

```bash
# Basic search
python -m carsearch skoda superb

# With location and radius
python -m carsearch vw golf --location belfast --radius 80

# With price and year filters
python -m carsearch bmw 3-series --min-price 5000 --max-price 15000 --min-year 2018

# Limit pages per site (for faster results on broad searches)
python -m carsearch ford focus --max-pages 2

# Common aliases work
python -m carsearch merc c-class    # Mercedes-Benz
python -m carsearch beemer 3-series # BMW
python -m carsearch landy discovery # Land Rover
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

### Locations

Accepts any NI town name (belfast, derry, newry, lisburn, bangor, ballymena, omagh, enniskillen, coleraine, etc.) or a BT postcode directly.

## How each site is scraped

### UsedCarsNI

UsedCarsNI uses opaque numeric IDs for make and model. The scraper loads the homepage, selects the make from the dropdown, waits for the model dropdown to populate via JS, and reads the options to resolve the correct model ID. It then constructs the search URL with exact IDs, so results are precise (no keyword matching).

Pagination via `&pagepc0=N`, 20 results per page. Stops when the "Next" link disappears. All listings on the site for a given make/model are retrieved.

### AutoTrader

AutoTrader is a full SPA that renders via GraphQL after page load. The scraper navigates to the search URL and waits for `advertCard` elements to render. Delivery-only results (Cinch etc.) are excluded as they don't ship to NI.

Pagination via `&page=N`, ~25 results per page. Stops when a page returns fewer than 20 cards.

### Gumtree

Gumtree uses structured `vehicle_make` and `vehicle_model` URL params for exact filtering. The scraper only collects local results, excluding Gumtree's "Results from outside your search" section which pads results with nationwide listings.

Pagination via `&page=N`, 25 results per page. Stops when the forward-page button disappears.

## Adding a new site

Drop a file in `carsearch/scrapers/` with a class that extends `Scraper`:

```python
from ..base import Filters, Listing, Scraper

class DoneDealScraper(Scraper):
    name = "DoneDeal"
    needs_browser = True

    def build_url(self, make, model, filters):
        ...

    async def scrape(self, page, make, model, filters, on_page=None):
        results = []
        # ... extract listings ...
        if on_page:
            on_page(results)  # emit results immediately
        return results
```

It gets auto-discovered. No other files need editing.

## Docker

```bash
docker build -t carsearch .
docker run carsearch skoda superb --location belfast --radius 80
```
