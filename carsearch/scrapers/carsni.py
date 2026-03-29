"""Cars.ni scraper.

Method:
    Browser-based (Playwright + stealth). WordPress site with STM Motors
    Listing theme. Cloudflare blocks plain HTTP requests.

    URL: cars.ni/used/?make={make}&model={model}
    Pagination: /used/page/{n}/?make={make}&model={model}, 25 per page.

    Data extraction: .listing-list-loop elements with data-title attribute
    containing "YEAR MAKE MODEL". Price, mileage, fuel, location parsed
    from listing card text content.

    All listings are NI by definition.

Limitations:
    - Cloudflare requires stealth browser.
    - No distance/radius filtering (all NI).
    - Model names must match their slugs exactly.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from ..base import Filters, Listing, Scraper


class CarsNIScraper(Scraper):
    name = "CarsNI"
    needs_browser = True

    def build_url(self, make: str, model: str, filters: Filters, page: int = 1) -> str:
        params = {"make": make.lower(), "model": model.lower()}
        base = "https://cars.ni/used/"
        if page > 1:
            base = f"https://cars.ni/used/page/{page}/"
        return f"{base}?{urlencode(params)}"

    async def scrape(self, page, make: str, model: str, filters: Filters, on_page=None) -> list[Listing]:
        results = []
        page_num = 1
        seen_links: set[str] = set()

        while True:
            if page_num > 1:
                url = self.build_url(make, model, filters, page=page_num)
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)

            try:
                await page.wait_for_selector('.listing-list-loop, .stm-listing-directory-item', timeout=5000)
            except Exception:
                break

            cards = await page.query_selector_all('.listing-list-loop, .stm-listing-directory-item')
            if not cards:
                break

            page_results = []
            for card in cards:
                listing = await self._extract_listing(card, filters)
                if listing and listing.link not in seen_links:
                    seen_links.add(listing.link)
                    page_results.append(listing)

            if not page_results:
                break

            results.extend(page_results)
            if on_page:
                on_page(page_results)

            # Check for next page
            has_next = await page.query_selector('a.next, .stm-next-page a, a[rel="next"]')
            if not has_next or (filters.max_pages and page_num >= filters.max_pages):
                break
            page_num += 1

        return results

    async def _extract_listing(self, card, filters: Filters) -> Listing | None:
        # Link
        link_el = await card.query_selector('a[href*="/used/"]')
        href = (await link_el.get_attribute('href')) if link_el else ""
        link = href if href and href.startswith("http") else (f"https://cars.ni{href}" if href else "-")

        # Title from data-title or heading
        data_title = await card.get_attribute('data-title')
        if data_title:
            title = data_title.strip()
        else:
            title_el = await card.query_selector('h4 a, h3 a, .title a')
            title = (await title_el.inner_text()).strip() if title_el else "-"

        # Get the full text content for parsing
        full_text = (await card.inner_text()).strip()

        # Price
        price_match = re.search(r'[£€][\d,]+', full_text)
        price = price_match.group(0) if price_match else "-"

        # Year
        year_match = re.search(r'\b(19|20)\d{2}\b', title)
        year = year_match.group(0) if year_match else "-"

        # Mileage
        mileage_match = re.search(r'([\d,]+)\s*(?:miles|mi)', full_text, re.IGNORECASE)
        mileage = f"{mileage_match.group(1)} miles" if mileage_match else "-"

        # Location - often the dealer name or area
        location = "-"
        loc_el = await card.query_selector('.stm-dealer-name, .dealer-name, [class*="location"]')
        if loc_el:
            location = (await loc_el.inner_text()).strip()

        # Apply year filters
        if filters.min_year or filters.max_year:
            try:
                y = int(year)
            except ValueError:
                y = None
            if y and filters.min_year and y < filters.min_year:
                return None
            if y and filters.max_year and y > filters.max_year:
                return None

        return Listing(
            source=self.name,
            title=title,
            price=price,
            year=year,
            mileage=mileage,
            location=location,
            link=link,
        )
