import importlib
import pkgutil

from ..base import Scraper


def get_all_scrapers() -> list[Scraper]:
    scrapers = []
    for _, name, _ in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f".{name}", __package__)
        for attr in dir(module):
            cls = getattr(module, attr)
            if (
                isinstance(cls, type)
                and issubclass(cls, Scraper)
                and cls is not Scraper
            ):
                scrapers.append(cls())
    return scrapers
