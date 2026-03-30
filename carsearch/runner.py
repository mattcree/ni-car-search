from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from .base import Filters, Listing, Scraper
from .scrapers import get_all_scrapers


async def _dismiss_cookies(page):
    for sel in [
        'button:has-text("Reject All")',
        'button:has-text("Accept All")',
        'button:has-text("Accept")',
        ".fc-cta-consent",
        "#onetrust-accept-btn-handler",
    ]:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(1000)
                return
        except Exception:
            continue


async def run(
    make: str,
    model: str,
    filters: Filters,
    on_results=None,
    scrapers: list[Scraper] | None = None,
) -> tuple[list[Listing], dict[str, str]]:
    if scrapers is None:
        scrapers = get_all_scrapers()

    all_listings: list[Listing] = []
    errors: dict[str, str] = {}

    def on_page(source: str):
        """Return a callback that deduplicates across retries."""
        seen: set[str] = set()
        def _callback(listings: list[Listing]):
            new = [l for l in listings if l.link not in seen]
            for l in new:
                seen.add(l.link)
            all_listings.extend(new)
            if on_results and new:
                on_results(source, new)
        return _callback

    browser_scrapers = [s for s in scrapers if s.needs_browser]
    request_scrapers = [s for s in scrapers if not s.needs_browser]

    async def run_request_scraper(scraper: Scraper):
        for attempt in range(3):
            try:
                await scraper.scrape(None, make, model, filters, on_page=on_page(scraper.name))
                return
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                else:
                    errors[scraper.name] = str(e)

    async def run_browser_scrapers():
        if not browser_scrapers:
            return

        stealth = Stealth()
        async with async_playwright() as p:
            stealth.hook_playwright_context(p)
            browser = await p.chromium.launch(headless=True, channel="chrome")

            async def _scrape(scraper: Scraper):
                for attempt in range(3):
                    page = await browser.new_page()
                    try:
                        if scraper.self_navigates:
                            await scraper.scrape(page, make, model, filters, on_page=on_page(scraper.name))
                        else:
                            url = scraper.build_url(make, model, filters)
                            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                            await page.wait_for_timeout(3000)
                            await _dismiss_cookies(page)
                            await page.wait_for_timeout(2000)
                            await scraper.scrape(page, make, model, filters, on_page=on_page(scraper.name))
                        return
                    except Exception as e:
                        if attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                        else:
                            errors[scraper.name] = str(e)
                    finally:
                        await page.close()

            await asyncio.gather(*[_scrape(s) for s in browser_scrapers])
            await browser.close()

    await asyncio.gather(
        *[run_request_scraper(s) for s in request_scrapers],
        run_browser_scrapers(),
    )

    return all_listings, errors
