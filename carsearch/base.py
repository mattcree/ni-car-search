from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SourceParams:
    """Per-source make/model resolved from the catalogue.

    When available, scrapers use these instead of the raw watch strings,
    giving correct casing and pre-resolved IDs.
    """
    make: str
    model: str
    make_id: str | None = None
    model_id: str | None = None


@dataclass
class Listing:
    source: str
    title: str
    price: str
    year: str
    mileage: str
    location: str
    link: str
    body: str = "-"
    transmission: str = "-"
    fuel_type: str = "-"


NI_LOCATIONS = {
    "belfast": "BT1 1AA",
    "derry": "BT48 6HQ",
    "londonderry": "BT48 6HQ",
    "newry": "BT35 6BP",
    "lisburn": "BT28 1AA",
    "bangor": "BT20 4BN",
    "ballymena": "BT43 5BS",
    "craigavon": "BT65 5AQ",
    "omagh": "BT78 1DQ",
    "enniskillen": "BT74 7JD",
    "coleraine": "BT52 1BE",
    "antrim": "BT41 4AA",
    "newtownards": "BT23 4YH",
    "downpatrick": "BT30 6LZ",
    "armagh": "BT61 7QA",
    "dungannon": "BT70 1AR",
    "cookstown": "BT80 8BG",
    "strabane": "BT82 8DS",
    "ballyclare": "BT39 9AA",
    "larne": "BT40 1RN",
    "carrickfergus": "BT38 7DG",
    "portadown": "BT62 1AA",
    "lurgan": "BT66 6AA",
    "magherafelt": "BT45 5AA",
    "dungiven": "BT47 4AA",
    "newcastle": "BT33 0AA",
}


def resolve_location(location: str) -> str:
    """Resolve a location name to a postcode. Pass through if already a postcode."""
    if location.upper().startswith("BT"):
        return location
    return NI_LOCATIONS.get(location.lower(), location)


@dataclass
class Filters:
    min_price: int | None = None
    max_price: int | None = None
    min_year: int | None = None
    max_year: int | None = None
    postcode: str = "BT1 1AA"
    location: str = "northern-ireland"
    radius: int = 0  # miles, 0 = no limit
    max_pages: int = 0  # 0 = no limit



def normalise_fuel(raw: str) -> str:
    """Normalise a fuel type string into a canonical label."""
    if not raw or raw == "-":
        return "-"
    t = raw.lower().strip()
    if t in ("electric", "full electric", "battery electric"):
        return "Electric"
    if any(x in t for x in ["plug-in", "plugin", "phev"]):
        return "Plug-in Hybrid"
    if any(x in t for x in ["mild hybrid", "mhev"]):
        return "Mild Hybrid"
    if "hybrid" in t:
        return "Hybrid"
    if t in ("diesel", "diesel/electric"):
        return "Diesel"
    if t in ("petrol", "petrol/electric", "unleaded"):
        return "Petrol"
    return raw.title()


def detect_fuel(text: str) -> str:
    """Detect fuel type from title/subtitle text using engine code heuristics."""
    t = text.lower()
    if any(x in t for x in ["electric", " ev ", " bev"]):
        return "Electric"
    if any(x in t for x in ["plug-in", "plugin", "phev"]):
        return "Plug-in Hybrid"
    if any(x in t for x in ["mild hybrid", "mhev"]):
        return "Mild Hybrid"
    if "hybrid" in t:
        return "Hybrid"
    if any(x in t for x in [" tdi", " cdi", " dci", " hdi", " jtd", " d4d", " crdi",
                             "diesel", "bluetec"]):
        return "Diesel"
    if any(x in t for x in [" tsi", " tfsi", " fsi", " gdi", " vtec", " mpi",
                             "petrol", " turbo "]):
        return "Petrol"
    return "-"


class Scraper(ABC):
    """Base class for all site scrapers.

    To add a new site, subclass this and place the file in carsearch/scrapers/.
    It will be auto-discovered.
    """

    name: str
    needs_browser: bool = True
    self_navigates: bool = False  # True if scraper handles its own page navigation

    def build_url(self, make: str, model: str, filters: Filters) -> str:
        """Build the search URL. Not needed for self-navigating scrapers."""
        return ""

    @abstractmethod
    async def scrape(self, page, make: str, model: str, filters: Filters, on_page=None) -> list[Listing]:
        """Scrape all pages. Call on_page(listings) after each page if provided."""
        ...
