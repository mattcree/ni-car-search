"""NI Vehicle Sales scraper.

Method:
    Direct REST API (Supabase). No browser needed. The site is built on
    Softr backed by Supabase with a publicly accessible anon key.

    API: https://ymhtnsjjdjemqoygqmna.supabase.co/rest/v1/vehicle_listings
    Auth: Public anon JWT in apikey + Authorization headers.
    Filter: PostgREST syntax, e.g. ?make=eq.Skoda&model=ilike.*superb*
    Pagination: offset/limit, max 1000 per request.

    No location field per listing - all listings are NI by definition.

Limitations:
    - No location/distance filtering (all listings are NI dealers).
    - Model matching is fuzzy (ilike) since model names may vary.
    - Supabase anon key is public but could be rotated.
"""

from __future__ import annotations

import requests

from ..base import Filters, Listing, Scraper, normalise_fuel

API_URL = "https://ymhtnsjjdjemqoygqmna.supabase.co/rest/v1/vehicle_listings"
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InltaHRuc2pqZGplbXFveWdxbW5hIiwi"
    "cm9sZSI6ImFub24iLCJpYXQiOjE3NjcwNjA2NDEsImV4cCI6MjA4MjYzNjY0MX0."
    "Dd7l28niPwWP087Z3yW-bANmVEKSnUWl35EK3EdNxbQ"
)
HEADERS = {
    "apikey": ANON_KEY,
    "Authorization": f"Bearer {ANON_KEY}",
}
PAGE_SIZE = 1000


class NIVehicleSalesScraper(Scraper):
    name = "NIVehicleSales"
    needs_browser = False

    def build_url(self, make: str, model: str, filters: Filters) -> str:
        return f"https://nivehiclesales.com/?make={make}&model={model}"

    async def scrape(self, _page, make: str, model: str, filters: Filters, on_page=None) -> list[Listing]:
        params = {
            "select": "*",
            "make": f"ilike.*{make}*",
            "model": f"ilike.*{model}*",
            "order": "price.asc",
            "limit": PAGE_SIZE,
            "offset": 0,
        }
        if filters.min_price:
            params["price"] = f"gte.{filters.min_price}"
        if filters.max_price:
            # PostgREST can't do two filters on same column in params,
            # use the "and" syntax
            if "price" in params:
                params.pop("price")
                params["and"] = f"(price.gte.{filters.min_price},price.lte.{filters.max_price})"
            else:
                params["price"] = f"lte.{filters.max_price}"
        if filters.min_year:
            params["year"] = f"gte.{filters.min_year}"
        if filters.max_year:
            if "year" in params:
                params.pop("year")
                yr_filter = f"(year.gte.{filters.min_year},year.lte.{filters.max_year})"
                if "and" in params:
                    params["and"] = params["and"][:-1] + f",{yr_filter[1:]}"
                else:
                    params["and"] = yr_filter
            else:
                params["year"] = f"lte.{filters.max_year}"

        results = []
        offset = 0

        while True:
            params["offset"] = offset
            resp = requests.get(API_URL, headers=HEADERS, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            if not data:
                break

            page_results = []
            for item in data:
                price = item.get("price")
                price_str = f"\u00a3{int(price):,}" if price else "-"
                year = str(item.get("year", "-"))
                mileage = item.get("mileage")
                mileage_str = f"{int(mileage):,} miles" if mileage else "-"
                dealer = item.get("dealership_name", "-")
                link = item.get("more_details_url", "-")

                title_parts = [year, item.get("make", ""), item.get("model", "")]
                variant = item.get("variant", "")
                if variant:
                    title_parts.append(variant)
                title = " ".join(p for p in title_parts if p and p != "-")

                page_results.append(Listing(
                    source=self.name,
                    title=title,
                    price=price_str,
                    year=year,
                    mileage=mileage_str,
                    location=dealer,
                    link=link,
                    body=item.get("body_type", "-") or "-",
                    transmission=item.get("transmission", "-") or "-",
                    fuel_type=normalise_fuel(item.get("fuel_type", "") or item.get("fuel", "") or "-"),
                ))

            results.extend(page_results)
            if on_page:
                on_page(page_results)

            if len(data) < PAGE_SIZE or (filters.max_pages and (offset // PAGE_SIZE + 1) >= filters.max_pages):
                break
            offset += PAGE_SIZE

        return results
