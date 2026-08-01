"""Load, save and query the committed price book.

The import path here is deliberately pure: stdlib + pydantic, nothing that
touches the network. Cost estimation must be deterministic and offline — the
same call priced twice must give the same answer, which rules out a live fetch
anywhere near this module. Fetching lives in `sources/`, behind the `fetch`
extra, and is only reached by the CLI.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import STANDARD, ModelEntry, Price, PriceBook

DATA_PATH = Path(__file__).resolve().parent / 'data' / 'prices.json'

_CACHE: PriceBook | None = None


def load_book(path: Path | None = None, *, refresh: bool = False) -> PriceBook:
    """Return the price book, cached in-process after the first read.

    `refresh=True` drops the cache — the test seam, and the hook a long-running
    process would use after a `refresh --write`.
    """
    global _CACHE
    if path is not None:
        return PriceBook.model_validate_json(path.read_text(encoding='utf-8'))
    if _CACHE is None or refresh:
        _CACHE = PriceBook.model_validate_json(DATA_PATH.read_text(encoding='utf-8'))
    return _CACHE


def save_book(book: PriceBook, path: Path | None = None) -> None:
    target = path or DATA_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    # Trailing newline and sorted keys keep the committed diff readable — this
    # file's whole job is to be reviewed by a human before it lands.
    payload = book.model_dump(exclude_none=True)
    payload['models'] = dict(sorted(payload['models'].items()))
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
    )


def get_entry(model_id: str, book: PriceBook | None = None) -> ModelEntry | None:
    return (book or load_book()).get(model_id)


def get_price(
    model_id: str, tier: str = STANDARD, book: PriceBook | None = None
) -> Price | None:
    """Return one tier's rates, or None if the model or tier is unknown.

    None means "not published here" and callers must handle it. This function
    will not substitute a zero, because a silent $0 is how a billing bug hides.
    """
    entry = get_entry(model_id, book)
    return entry.tiers.get(tier) if entry else None


def estimate_cost(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    tier: str = STANDARD,
    book: PriceBook | None = None,
) -> float | None:
    """USD for one call's usage, or None when the model/tier is unknown.

    `input_tokens` is assumed to be the provider's reported prompt count, which
    already INCLUDES the cached subsets — so those are subtracted out and
    repriced at their own rates rather than billed twice.

    A vendor that publishes no cache rate gets its cached tokens billed at the
    full input rate. That over-states rather than under-states, and it beats
    inventing a discount multiplier: this book records what vendors publish.
    """
    price = get_price(model_id, tier, book)
    if price is None:
        return None

    read_rate = price.cache_read if price.cache_read is not None else price.input
    write_rate = price.cache_write if price.cache_write is not None else price.input

    # Clamp so a malformed usage row can never produce negative fresh input.
    fresh = max(input_tokens - cache_read_tokens - cache_write_tokens, 0)
    return (
        fresh / 1_000_000 * price.input
        + cache_read_tokens / 1_000_000 * read_rate
        + cache_write_tokens / 1_000_000 * write_rate
        + output_tokens / 1_000_000 * price.output
    )
