"""Motors.co.uk scraper.

Method:
    Browser-based (Playwright + stealth). Navigate to search URL with
    Make/Model/Postcode query params which sets a server-side session.
    Extract listing data from DOM result cards.

    URL: motors.co.uk/search/car/?Make={make}&Model={model}&Postcode={postcode}
    Pagination: Click through page buttons in the DOM.

    Part of the MOTORS network (also powers Gumtree, Cazoo, eBay Motors).
    Cinch/Cazoo delivery-only listings are excluded by not enabling the
    "Include online only" checkbox.

Limitations:
    - Session-based: search params live in a cookie, not the URL.
    - ~22 results per page (PAGE_SIZE).
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from ..base import Filters, Listing, Scraper, detect_fuel, normalise_fuel


PAGE_SIZE = 22


class MotorsScraper(Scraper):
    name = "Motors"
    needs_browser = True
    self_navigates = True

    def build_url(self, make: str, model: str, filters: Filters) -> str:
        params = {"Make": make.title(), "Model": model.title(), "Postcode": filters.postcode}
        return f"https://www.motors.co.uk/search/car/?{urlencode(params)}"

    async def scrape(self, page, make: str, model: str, filters: Filters, on_page=None, source_params=None) -> list[Listing]:
        url = self.build_url(make, model, filters)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)

        # Dismiss cookies + modals, set distance - all via JS to avoid overlay issues
        if filters.radius:
            options = [1, 10, 20, 30, 40, 50, 60, 100, 200, 1000]
            best = min(options, key=lambda x: abs(x - filters.radius) if x >= filters.radius else 9999)
        else:
            best = 1000

        await page.evaluate(f"""(dist) => {{
            // Remove overlays
            document.querySelectorAll('.radix_modal__overlay, [data-state="open"]')
                .forEach(el => el.remove());
            // Accept cookies
            const cb = [...document.querySelectorAll('button')].find(b => b.textContent.includes('Accept All'));
            if (cb) cb.click();
            // Set distance
            const sel = document.getElementById('Distance');
            if (sel) {{ sel.value = String(dist); sel.dispatchEvent(new Event('change', {{bubbles: true}})); }}
            // Submit
            setTimeout(() => {{
                const btn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Search');
                if (btn) btn.click();
            }}, 500);
        }}""", best)
        await page.wait_for_timeout(5000)

        results = []
        seen_links: set[str] = set()
        page_num = 1

        while True:
            # Remove any modal overlays blocking the page
            await page.evaluate("""() => {
                document.querySelectorAll('.radix_modal__overlay, [data-state="open"]')
                    .forEach(el => el.remove());
            }""")

            try:
                await page.wait_for_selector(".result-card", timeout=5000)
            except Exception:
                break

            cards = await page.query_selector_all(".result-card")
            if not cards:
                break

            page_results = []
            for card in cards:
                listing = await self._extract_card(card)
                if listing and listing.link not in seen_links:
                    seen_links.add(listing.link)
                    page_results.append(listing)

            if not page_results:
                break

            results.extend(page_results)
            if on_page:
                on_page(page_results)

            if len(cards) < PAGE_SIZE:
                break

            # Paginate via JS click (Playwright clicks are blocked by overlays)
            has_next = await page.evaluate("""() => {
                const btn = document.querySelector('button.pgn__next:not([disabled])');
                if (btn) { btn.click(); return true; }
                return false;
            }""")
            if not has_next or (filters.max_pages and page_num >= filters.max_pages):
                break
            await page.wait_for_timeout(3000)
            page_num += 1

        return results

    @staticmethod
    async def _extract_card(card) -> Listing | None:
        # Link
        link_el = await card.query_selector("a.result-card__link")
        href = (await link_el.get_attribute("href")) if link_el else ""
        link = f"https://www.motors.co.uk{href.split('?')[0]}" if href else "-"

        # Title: h3 + h4 subtitle
        h3 = await card.query_selector("h3")
        h4 = await card.query_selector("h4")
        make_model = (await h3.inner_text()).strip() if h3 else "-"
        subtitle = (await h4.inner_text()).strip() if h4 else ""

        title = f"{make_model} {subtitle}" if subtitle else make_model

        # Year from h4 (starts with "2024 - ..." or "2024 (24) - ...")
        year = "-"
        if subtitle:
            year_match = re.match(r"((?:19|20)\d{2})", subtitle)
            year = year_match.group(1) if year_match else "-"

        # Price
        price_el = await card.query_selector(".result-card__body .title-4.no-scale")
        price = (await price_el.inner_text()).strip() if price_el else "-"

        # Specs: list items in .result-card__vehicle-info
        # Typical order: engine size, mileage, fuel, transmission, body type
        mileage = "-"
        body = "-"
        transmission = "-"
        fuel_type = "-"
        body_types = {"hatchback", "estate", "saloon", "suv", "coupe", "convertible",
                      "mpv", "pickup", "van", "sedan", "cabriolet", "limousine"}
        trans_types = {"auto", "automatic", "manual", "semi-auto", "semi-automatic"}
        fuel_types = {"petrol", "diesel", "electric", "hybrid",
                      "petrol/electric", "diesel/electric",
                      "plug-in hybrid", "mild hybrid"}
        specs = await card.query_selector_all(".result-card__vehicle-info li")
        for s in specs:
            text = (await s.inner_text()).strip().replace("\n", " ")
            low = text.lower()
            if "mile" in low or re.search(r'\d+k\b', low):
                mileage = text
            elif low in body_types:
                body = text
            elif low in trans_types:
                transmission = text
            elif low in fuel_types:
                fuel_type = normalise_fuel(text)

        if fuel_type == "-":
            fuel_type = detect_fuel(title)

        # Location: dealer name + distance
        dealer_el = await card.query_selector(".result-card__dealer")
        dealer = ""
        if dealer_el:
            # First line is dealer name, rest is phone etc
            text = (await dealer_el.inner_text()).strip()
            dealer = text.split("\n")[0].strip()

        footer_el = await card.query_selector(".result-card__footer")
        distance = ""
        if footer_el:
            text = (await footer_el.inner_text()).strip()
            dist_match = re.search(r"(\d+)\s*miles?\s*away", text, re.IGNORECASE)
            if dist_match:
                distance = f"({dist_match.group(1)} mi)"

        location = f"{dealer} {distance}".strip() if dealer else (distance or "-")

        return Listing(
            source="Motors",
            title=title,
            price=price,
            year=year,
            mileage=mileage,
            location=location,
            link=link,
            body=body,
            transmission=transmission,
            fuel_type=fuel_type,
        )
