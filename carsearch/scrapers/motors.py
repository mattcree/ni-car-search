"""Motors.co.uk scraper.

Method:
    Browser-based (Playwright + stealth). Navigate to search URL with
    Make/Model/Postcode query params which sets a server-side session
    and redirects to results. Subsequent pages via POST /search/car/results
    JSON API using the session cookie.

    URL: motors.co.uk/search/car/?Make={make}&Model={model}&Postcode={postcode}
    Pagination: POST /search/car/results with PageNumber, returns JSON.

    Part of the MOTORS network (also powers Gumtree, Cazoo, eBay Motors).

Limitations:
    - Session-based: search params live in a cookie, not the URL.
    - 21 results per page.
"""

from __future__ import annotations

from urllib.parse import urlencode

from ..base import Filters, Listing, Scraper


class MotorsScraper(Scraper):
    name = "Motors"
    needs_browser = True
    self_navigates = True

    def build_url(self, make: str, model: str, filters: Filters) -> str:
        params = {"Make": make.title(), "Model": model.title(), "Postcode": filters.postcode}
        return f"https://www.motors.co.uk/search/car/?{urlencode(params)}"

    async def scrape(self, page, make: str, model: str, filters: Filters, on_page=None) -> list[Listing]:
        url = self.build_url(make, model, filters)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)

        # Dismiss cookies
        for sel in ['button:has-text("Accept All")', '#onetrust-accept-btn-handler']:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(1000)
                break

        results = []
        seen_links: set[str] = set()

        # Page 1: extract from embedded React JSON or DOM
        page1 = await self._extract_from_page(page)
        new = [l for l in page1 if l.link not in seen_links]
        for l in new:
            seen_links.add(l.link)
        results.extend(new)
        if on_page and new:
            on_page(new)

        # Get total pages
        pag = await self._get_pagination(page)
        total_pages = pag.get("last_page", 1) if pag else 1

        # Pages 2+ via JSON API
        for pg in range(2, total_pages + 1):
            if filters.max_pages and pg > filters.max_pages:
                break

            page_listings = await self._fetch_json_page(page, pg)
            new = [l for l in page_listings if l.link not in seen_links]
            for l in new:
                seen_links.add(l.link)
            if not new:
                break
            results.extend(new)
            if on_page:
                on_page(new)

        return results

    async def _extract_from_page(self, page) -> list[Listing]:
        """Extract from embedded React props or fall back to DOM."""
        try:
            data = await page.evaluate("""() => {
                const scripts = document.querySelectorAll('script');
                for (const s of scripts) {
                    const t = s.textContent;
                    if (t && t.includes('initialResults')) {
                        const m = t.match(/m\\.SearchResults,\\s*({.*})\\s*\\)/s);
                        if (m) { try { return JSON.parse(m[1]); } catch(e) {} }
                    }
                }
                return null;
            }""")
            if data and "initialResults" in data:
                return [self._to_listing(v) for v in data["initialResults"]]
        except Exception:
            pass

        # DOM fallback
        cards = await page.query_selector_all('.result-card')
        listings = []
        for card in cards:
            l = await self._from_card(card)
            if l:
                listings.append(l)
        return listings

    async def _get_pagination(self, page) -> dict | None:
        try:
            return await page.evaluate("""() => {
                const scripts = document.querySelectorAll('script');
                for (const s of scripts) {
                    const t = s.textContent;
                    if (t && t.includes('initialPagination')) {
                        const m = t.match(/"initialPagination"\\s*:\\s*({[^}]+})/);
                        if (m) {
                            const p = JSON.parse(m[1]);
                            return {last_page: p.LastPage, total: p.TotalRecords};
                        }
                    }
                }
                return null;
            }""")
        except Exception:
            return None

    async def _fetch_json_page(self, page, page_num: int) -> list[Listing]:
        try:
            data = await page.evaluate("""(n) => {
                return fetch('/search/car/results', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                    body: 'PageNumber=' + n,
                    credentials: 'include',
                }).then(r => r.json());
            }""", page_num)
            if data and "Results" in data:
                return [self._to_listing(v) for v in data["Results"]]
        except Exception:
            pass
        return []

    @staticmethod
    def _to_listing(v: dict) -> Listing:
        price = v.get("Price") or v.get("GBPPrice")
        if isinstance(price, (int, float)):
            price_str = f"\u00a3{int(price):,}"
        elif isinstance(price, str) and price:
            price_str = price if "\u00a3" in price else f"\u00a3{price}"
        else:
            price_str = "-"

        year = str(v.get("RegistrationYear", "-"))
        mileage = v.get("MileageInt") or v.get("Mileage")
        mileage_str = f"{int(mileage):,} miles" if mileage and str(mileage).replace(",", "").isdigit() else "-"

        title = v.get("Title", "")
        if not title:
            parts = [year, v.get("Manufacturer", ""), v.get("Model", ""), v.get("Variant", "")]
            title = " ".join(p for p in parts if p and p != "-")

        distance = v.get("Distance")
        location = f"{int(distance)} mi away" if distance else "-"

        detail_url = v.get("DetailsPageUrl", "")
        link = f"https://www.motors.co.uk{detail_url}" if detail_url else "-"

        return Listing(source="Motors", title=title, price=price_str, year=year,
                       mileage=mileage_str, location=location, link=link)

    @staticmethod
    async def _from_card(card) -> Listing | None:
        title_el = await card.query_selector('h3')
        title = (await title_el.inner_text()).strip() if title_el else "-"
        price_el = await card.query_selector('.title-4')
        price = (await price_el.inner_text()).strip() if price_el else "-"
        link_el = await card.query_selector('a.result-card__link')
        href = (await link_el.get_attribute('href')) if link_el else ""
        link = f"https://www.motors.co.uk{href}" if href else "-"
        return Listing(source="Motors", title=title, price=price, year="-",
                       mileage="-", location="-", link=link)
