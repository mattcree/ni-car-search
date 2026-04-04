"""Gumtree scraper.

Method:
    Browser-based (Playwright + stealth). Navigates to the structured car
    search URL using vehicle_make and vehicle_model params, which filters
    to exact make/model (no keyword spam).

    URL: gumtree.com/cars-vans-motorbikes/cars/uk/{location}
         ?vehicle_make={make}&vehicle_model={model}&page={n}

    Pagination: &page=N, ~25 results per page. Stops when a page returns
    fewer results or no results.

    Data extraction: <article data-q="search-result"> elements with
    data-q attributes for title, price, year, mileage, location.

Limitations:
    - Gumtree's distance param doesn't reliably filter by radius.
    - Year filtering is client-side (no year URL param).
"""

from __future__ import annotations

from urllib.parse import urlencode

from ..base import Filters, Listing, Scraper, detect_fuel, normalise_fuel

LOCATION_SLUGS = {
    "northern-ireland": "northern-ireland",
    "belfast": "belfast",
    "derry": "derry",
    "londonderry": "derry",
    "newry": "newry",
    "lisburn": "lisburn",
    "bangor": "bangor",
    "ballymena": "ballymena",
    "antrim": "antrim",
    "omagh": "omagh",
    "enniskillen": "enniskillen",
    "coleraine": "coleraine",
    "newtownards": "newtownards",
    "downpatrick": "downpatrick",
    "dungannon": "dungannon",
    "larne": "larne",
    "carrickfergus": "carrickfergus",
    "portadown": "portadown",
}


class GumtreeScraper(Scraper):
    name = "Gumtree"
    needs_browser = True

    def build_url(self, make: str, model: str, filters: Filters, page: int = 1) -> str:
        location_slug = LOCATION_SLUGS.get(filters.location, "northern-ireland")
        params: dict = {
            "vehicle_make": make.lower(),
            "vehicle_model": model.lower(),
        }
        if filters.min_price:
            params["min_price"] = filters.min_price
        if filters.max_price:
            params["max_price"] = filters.max_price
        if filters.radius:
            params["distance"] = filters.radius
        if page > 1:
            params["page"] = page
        return (
            f"https://www.gumtree.com/cars-vans-motorbikes/cars/uk/{location_slug}"
            f"?{urlencode(params)}"
        )

    async def scrape(self, page, make: str, model: str, filters: Filters, on_page=None, source_params=None) -> list[Listing]:
        results = []
        page_num = 1
        seen_links: set[str] = set()

        while True:
            if page_num > 1:
                url = self.build_url(make, model, filters, page=page_num)
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)

            try:
                await page.wait_for_selector('article[data-q="search-result"]', timeout=5000)
            except Exception:
                break

            all_articles = await page.query_selector_all('article[data-q="search-result"]')
            # Filter out articles inside the "nearby-results" (wider area) section
            articles = []
            for a in all_articles:
                in_nearby = await a.evaluate('el => el.closest("[data-q=nearby-results]") !== null')
                if not in_nearby:
                    articles.append(a)
            if not articles:
                break

            page_results = []
            for article in articles:
                listing = await self._extract_listing(article, filters)
                if listing and listing.link not in seen_links:
                    seen_links.add(listing.link)
                    page_results.append(listing)

            if not page_results:
                break

            results.extend(page_results)
            if on_page:
                on_page(page_results)

            # Check if there's a next page (forward button)
            has_next = await page.query_selector('[data-q="pagination-forward-page"]')
            if not has_next or (filters.max_pages and page_num >= filters.max_pages):
                break
            page_num += 1

        return results

    async def _extract_listing(self, article, filters: Filters) -> Listing | None:
        link_el = await article.query_selector('a[data-q="search-result-anchor"]')
        href = (await link_el.get_attribute("href")) if link_el else ""
        link = f"https://www.gumtree.com{href}" if href else "-"

        title_el = await article.query_selector('[data-q="tile-title"]')
        title = (await title_el.inner_text()).strip() if title_el else "-"

        price_el = await article.query_selector('[data-q="tile-price"]')
        price = (await price_el.inner_text()).strip() if price_el else "-"
        price = price.split("(")[0].strip()

        year_el = await article.query_selector('[data-q="motors-year"]')
        year = (await year_el.inner_text()).strip() if year_el else "-"

        mileage_el = await article.query_selector('[data-q="motors-mileage"]')
        mileage = (await mileage_el.inner_text()).strip() if mileage_el else "-"

        location_el = await article.query_selector('[data-q="tile-location"]')
        location = (await location_el.inner_text()).strip() if location_el else "-"

        # Body type from data attribute or title text
        body_el = await article.query_selector('[data-q="motors-body-type"]')
        body = (await body_el.inner_text()).strip() if body_el else "-"
        if body == "-":
            title_lower = title.lower()
            for bt in ["estate", "hatchback", "saloon", "suv", "coupe", "convertible", "mpv"]:
                if bt in title_lower:
                    body = bt.title()
                    break

        trans_el = await article.query_selector('[data-q="motors-transmission"]')
        transmission = (await trans_el.inner_text()).strip() if trans_el else "-"
        if transmission == "-":
            for t in ["automatic", "semi-auto", "manual"]:
                if t in title.lower():
                    transmission = t.title()
                    break

        fuel_el = await article.query_selector('[data-q="motors-fuel-type"]')
        fuel_type = normalise_fuel((await fuel_el.inner_text()).strip()) if fuel_el else detect_fuel(title)

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
            source=self.name, title=title, price=price, year=year,
            mileage=mileage, location=location, link=link, body=body,
            transmission=transmission, fuel_type=fuel_type,
        )
