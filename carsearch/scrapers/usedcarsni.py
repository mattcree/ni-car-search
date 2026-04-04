"""UsedCarsNI scraper.

Method:
    Browser-based (Playwright + stealth). Uses opaque numeric IDs for
    make and model, resolved dynamically by visiting the homepage and
    picking from the make/model dropdowns using contains matching
    (e.g. "mercedes" matches "Mercedes-Benz").

    URL: usedcarsni.com/search_results.php?make={id}&model={id}&pagepc0={n}

    Pagination: &pagepc0=N, 20 results per page.

    Data extraction: <article class="car-line"> with DT/DD spec pairs
    and .euroPrice for price.

Limitations:
    - Cloudflare Turnstile requires stealth bypass.
    - ID resolution adds ~5s (homepage + dropdown interaction).
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from ..base import Filters, Listing, Scraper, normalise_fuel

class UsedCarsNIScraper(Scraper):
    name = "UsedCarsNI"
    needs_browser = True
    self_navigates = True

    def build_url(self, make: str, model: str, filters: Filters) -> str:
        return f"https://www.usedcarsni.com/search_results.php?search_type=1&make=0&model=0&keywords={make}+{model}"

    async def _resolve_ids(self, page, make: str, model: str) -> tuple[str, str] | None:
        """Pick make and model from dropdowns using contains matching."""
        await page.goto("https://www.usedcarsni.com", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        for sel in ['button:has-text("Accept")', ".fc-cta-consent"]:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(1000)
                break

        # Find make by contains match on dropdown options
        make_select = await page.query_selector('select[name="make"]')
        if not make_select:
            return None

        make_id = None
        make_options = await make_select.query_selector_all("option")
        target_make = make.lower()
        for opt in make_options:
            text = (await opt.inner_text()).strip()
            val = await opt.get_attribute("value") or "0"
            name = re.sub(r"\s*\(\d+\)\s*$", "", text).strip().lower()
            if val != "0" and target_make in name:
                make_id = val
                break

        if not make_id:
            return None

        await make_select.select_option(value=make_id)
        await page.wait_for_timeout(2000)

        # Find model by contains match on dropdown options
        model_select = await page.query_selector('select[name="model"]')
        if not model_select:
            return None

        model_id = None
        model_options = await model_select.query_selector_all("option")
        target_model = model.lower()
        for opt in model_options:
            text = (await opt.inner_text()).strip()
            val = await opt.get_attribute("value") or "0"
            name = re.sub(r"\s*\(\d+\)\s*$", "", text).strip().lower()
            if val != "0" and target_model in name:
                model_id = val
                break

        if not model_id:
            return None

        return make_id, model_id

    def _build_results_url(self, make_id: str, model_id: str, filters: Filters, page: int = 1) -> str:
        params = {
            "search_type": 1,
            "make": make_id,
            "model": model_id,
            "fuel_type": 0,
            "trans_type": 0,
            "body_style": 0,
            "user_type": 0,
            "mileage_to": 0,
            "distance_enabled": 1 if filters.radius else 0,
            "distance_postcode": filters.postcode if filters.radius else "",
            "distance_value": filters.radius or 0,
            "keywords": "",
            "age_from": filters.min_year or 0,
            "age_to": filters.max_year or 0,
            "price_from": filters.min_price or 0,
            "price_to": filters.max_price or 0,
        }
        if page > 1:
            params["pagepc0"] = page
        return f"https://www.usedcarsni.com/search_results.php?{urlencode(params)}"

    async def scrape(self, page, make: str, model: str, filters: Filters, on_page=None, source_params=None) -> list[Listing]:
        # Use pre-resolved IDs from catalogue if available (skips homepage visit)
        if source_params and source_params.make_id and source_params.model_id:
            make_id, model_id = source_params.make_id, source_params.model_id
        else:
            ids = await self._resolve_ids(page, make, model)
            if not ids:
                raise ValueError(f"Could not find '{make} {model}' on UsedCarsNI")
            make_id, model_id = ids
        results = []
        page_num = 1
        seen_links: set[str] = set()

        while True:
            url = self._build_results_url(make_id, model_id, filters, page=page_num)
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            for sel in ['button:has-text("Accept")', ".fc-cta-consent"]:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click()
                    await page.wait_for_timeout(1000)
                    break

            try:
                await page.wait_for_selector("article.car-line", timeout=5000)
            except Exception:
                break

            articles = await page.query_selector_all("article.car-line")
            if not articles:
                break

            page_results = []
            for article in articles:
                listing = await self._extract_listing(article)
                if listing.link not in seen_links:
                    seen_links.add(listing.link)
                    page_results.append(listing)

            if not page_results:
                break

            results.extend(page_results)
            if on_page:
                on_page(page_results)

            # Check for "Next" pagination link
            has_next = await page.query_selector('.pagination a:has-text("Next")')
            if not has_next or (filters.max_pages and page_num >= filters.max_pages):
                break
            page_num += 1

        return results

    async def _extract_listing(self, article) -> Listing:
        title_el = await article.query_selector(".car-title a, .car-caption a")
        raw_title = (await title_el.inner_text()).strip() if title_el else "-"
        title = " ".join(raw_title.split())
        href = (await title_el.get_attribute("href")) if title_el else ""
        if href:
            clean = href.split("?")[0]
            link = f"https://www.usedcarsni.com{clean}" if not clean.startswith("http") else clean
        else:
            link = "-"

        price_el = await article.query_selector(".euroPrice")
        raw_price = (await price_el.inner_text()).strip() if price_el else ""
        raw_price = re.sub(r"[^\d]", "", raw_price)
        price = f"\u00a3{int(raw_price):,}" if raw_price else "-"

        specs = await self._extract_specs(article)

        year_match = re.search(r"\b(19|20)\d{2}\b", title)
        year = year_match.group(0) if year_match else "-"

        return Listing(
            source=self.name, title=title, price=price, year=year,
            mileage=specs.get("Mileage", "-"), location=specs.get("Location", "-"),
            link=link, body=specs.get("Body Style", "-"),
            transmission=specs.get("Transmission", "-"),
            fuel_type=normalise_fuel(specs.get("Fuel Type", specs.get("Fuel", "-"))),
        )

    @staticmethod
    async def _extract_specs(article) -> dict[str, str]:
        specs = {}
        dts = await article.query_selector_all("dl.dl-horizontal dt")
        dds = await article.query_selector_all("dl.dl-horizontal dd")
        for dt, dd in zip(dts, dds):
            key = (await dt.inner_text()).strip()
            val = (await dd.inner_text()).strip()
            if key and val:
                specs[key] = val
        return specs
