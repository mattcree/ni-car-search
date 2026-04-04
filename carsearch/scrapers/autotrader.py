"""AutoTrader UK scraper.

Method:
    Browser-based (Playwright + stealth). AutoTrader is a full SPA -
    plain HTTP gets an empty shell. Cloudflare blocks vanilla headless.

    URL: autotrader.co.uk/car-search?postcode={}&make={}&model={}&page={n}

    Pagination: &page=N, ~25 results per page. Stops when no cards found.

    Data extraction: div[data-testid="advertCard-N"] elements with
    data-testid attributes for title, subtitle, price, year, mileage,
    location. Location includes distance from postcode.

Limitations:
    - Delivery-only results (e.g. Cinch) are excluded as they don't
      ship to NI.
    - CSS classes are hashed; we rely on data-testid attributes.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from ..base import Filters, Listing, Scraper, detect_fuel


class AutoTraderScraper(Scraper):
    name = "AutoTrader"
    needs_browser = True

    def build_url(self, make: str, model: str, filters: Filters, page: int = 1) -> str:
        params: dict = {
            "postcode": filters.postcode,
            "make": make.title(),
            "model": model.title(),
            "advertising-location": "at_cars",
            "page": str(page),
        }
        if filters.radius:
            params["radius"] = filters.radius
        if filters.min_price:
            params["price-from"] = filters.min_price
        if filters.max_price:
            params["price-to"] = filters.max_price
        if filters.min_year:
            params["year-from"] = filters.min_year
        if filters.max_year:
            params["year-to"] = filters.max_year
        return f"https://www.autotrader.co.uk/car-search?{urlencode(params)}"

    async def scrape(self, page, make: str, model: str, filters: Filters, on_page=None) -> list[Listing]:
        results = []
        page_num = 1
        seen_links: set[str] = set()

        while True:
            if page_num > 1:
                url = self.build_url(make, model, filters, page=page_num)
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(5000)

            try:
                await page.wait_for_selector('[data-testid^="advertCard-"]', timeout=5000)
            except Exception:
                break

            cards = await page.query_selector_all('[data-testid^="advertCard-"]')
            if not cards:
                break

            page_results = []
            for card in cards:
                listing = await self._extract_listing(card)
                if listing and listing.link not in seen_links:
                    seen_links.add(listing.link)
                    page_results.append(listing)

            if not page_results:
                break

            results.extend(page_results)
            if on_page:
                on_page(page_results)

            # AutoTrader has no visible pagination controls - we navigate
            # via &page=N params. Stop when a page returns fewer cards than
            # a full page, or when all results are duplicates (seen_links).
            if len(cards) < 20 or (filters.max_pages and page_num >= filters.max_pages):
                break
            page_num += 1

        return results

    async def _extract_listing(self, card) -> Listing | None:
        title_el = await card.query_selector('[data-testid="search-listing-title"]')
        if not title_el:
            return None
        raw_text = (await title_el.inner_text()).strip()

        href = (await title_el.get_attribute("href")) or ""
        # Strip tracking params - just keep /car-details/{id}
        if href:
            clean = href.split("?")[0]
            link = f"https://www.autotrader.co.uk{clean}" if not clean.startswith("http") else clean
        else:
            link = "-"

        lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
        title = lines[0] if lines else "-"

        subtitle_el = await card.query_selector('[data-testid="search-listing-subtitle"]')
        subtitle = (await subtitle_el.inner_text()).strip() if subtitle_el else ""
        if subtitle:
            title = f"{title} {subtitle}"

        price = "-"
        price_match = re.search(r"\u00a3[\d,]+", raw_text)
        if price_match:
            price = price_match.group(0)

        year_el = await card.query_selector('[data-testid="registered_year"]')
        year_text = (await year_el.inner_text()).strip() if year_el else ""
        year_match = re.search(r"\b(19|20)\d{2}\b", year_text)
        year = year_match.group(0) if year_match else "-"

        mileage_el = await card.query_selector('[data-testid="mileage"]')
        mileage = (await mileage_el.inner_text()).strip() if mileage_el else "-"

        loc_el = await card.query_selector('[data-testid="search-listing-location"] span')
        location = (await loc_el.inner_text()).strip() if loc_el else "-"

        # Body type from subtitle text
        body = "-"
        sub_lower = subtitle.lower()
        for bt in ["estate", "hatchback", "saloon", "suv", "coupe", "convertible", "mpv", "pickup"]:
            if bt in sub_lower:
                body = bt.title()
                break

        # Transmission from subtitle
        transmission = "-"
        full_lower = title.lower()
        if any(x in full_lower for x in ["dsg", " auto", "s tronic", "tiptronic", "s-tronic"]):
            transmission = "Automatic"
        elif "manual" in full_lower:
            transmission = "Manual"

        # Fuel type: try data-testid, fall back to title parsing
        fuel_el = await card.query_selector('[data-testid="fuel-type"]')
        fuel_type = (await fuel_el.inner_text()).strip() if fuel_el else detect_fuel(title)

        return Listing(
            source=self.name, title=title, price=price, year=year,
            mileage=mileage, location=location, link=link, body=body,
            transmission=transmission, fuel_type=fuel_type,
        )
