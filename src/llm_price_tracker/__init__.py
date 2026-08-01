"""Published LLM prices, cross-checked across vendor pages.

The import surface is the offline read path — no network, no scraping deps:

    from llm_price_tracker import estimate_cost, get_price, load_book

Fetching lives in `llm_price_tracker.sources` behind the `fetch` extra, and is
reached only by the CLI. Keeping it out of this module is deliberate: cost
estimation has to be deterministic, and the same call priced twice must not
depend on whether a 24h cache had expired.
"""

from .book import estimate_cost, get_entry, get_price, load_book, save_book
from .models import STANDARD, ModelEntry, Price, PriceBook

__all__ = [
    'STANDARD',
    'ModelEntry',
    'Price',
    'PriceBook',
    'estimate_cost',
    'get_entry',
    'get_price',
    'load_book',
    'save_book',
]
