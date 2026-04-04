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


LOCATIONS = {
    # NI county towns / capitals
    "belfast": "BT1 1AA",           # Antrim (city)
    "lisburn": "BT28 1AA",          # Antrim
    "antrim": "BT41 4AA",           # Antrim
    "ballymena": "BT43 5BS",        # Antrim
    "carrickfergus": "BT38 7DG",    # Antrim
    "larne": "BT40 1RN",            # Antrim
    "newtownards": "BT23 4YH",      # Down
    "downpatrick": "BT30 6LZ",      # Down
    "bangor": "BT20 4BN",           # Down
    "newry": "BT35 6BP",            # Down
    "armagh": "BT61 7QA",           # Armagh
    "craigavon": "BT65 5AQ",        # Armagh
    "portadown": "BT62 1AA",        # Armagh
    "lurgan": "BT66 6AA",           # Armagh
    "derry": "BT48 6HQ",            # Londonderry
    "londonderry": "BT48 6HQ",      # Londonderry
    "coleraine": "BT52 1BE",        # Londonderry
    "magherafelt": "BT45 5AA",      # Londonderry
    "omagh": "BT78 1DQ",            # Tyrone
    "dungannon": "BT70 1AR",        # Tyrone
    "cookstown": "BT80 8BG",        # Tyrone
    "strabane": "BT82 8DS",         # Tyrone
    "enniskillen": "BT74 7JD",      # Fermanagh

    # UK capitals
    "london": "SW1A 1AA",
    "edinburgh": "EH1 1YZ",
    "cardiff": "CF10 1EP",

    # Major UK cities
    "manchester": "M1 1AE",
    "birmingham": "B1 1BB",
    "leeds": "LS1 1UR",
    "glasgow": "G1 1DU",
    "liverpool": "L1 1JD",
    "newcastle upon tyne": "NE1 4ST",
    "sheffield": "S1 1WB",
    "bristol": "BS1 1JG",
    "nottingham": "NG1 1AB",
    "southampton": "SO14 7LP",
    "aberdeen": "AB10 1AQ",
    "inverness": "IV1 1EP",
}

# Backward compat alias
NI_LOCATIONS = LOCATIONS


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
    postcode: str = "BT28 1AA"
    location: str = "lisburn"
    radius: int = 80  # miles, required
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
