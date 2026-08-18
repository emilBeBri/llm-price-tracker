"""Load, save and query the committed price book.

The import path here is deliberately pure: stdlib + pydantic, nothing that
touches the network. Cost estimation must be deterministic and offline — the
same call priced twice must give the same answer, which rules out a live fetch
anywhere near this module. Fetching lives in `sources/`, behind the `fetch`
extra, and is only reached by the CLI.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import STANDARD, ModelEntry, Price, PriceBook

DATA_PATH = Path(__file__).resolve().parent / 'data' / 'prices.json'

# The classic unit bug in this domain is per-token vs per-1M-tokens — a factor
# of exactly 1e6, which turns $2/M into $2,000,000/M. No published rate today
# is within an order of magnitude of this ceiling, so anything above it is far
# more likely a conversion bug than a real price. A write-time heuristic, not a
# model invariant: if a real $1,000+/M rate ever ships, raise the constant.
MAX_SANE_MTOK = 1000.0

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


def _implausible_rates(book: PriceBook) -> list[str]:
    bad: list[str] = []
    for model_id, entry in book.models.items():
        for tier, price in entry.tiers.items():
            # The peak variant nests a full Price: a unit bug in IT must trip
            # the gate too, or the factor-1e6 error walks in through the back
            # door.
            for suffix, variant in (('', price), ('.peak', price.peak)):
                if variant is None:
                    continue
                for rate_field in ('input', 'output', 'cache_read', 'cache_write'):
                    value = getattr(variant, rate_field)
                    if value is not None and value > MAX_SANE_MTOK:
                        bad.append(
                            f'{model_id}.{tier}{suffix}.{rate_field} = ${value}/M'
                        )
    return bad


def save_book(book: PriceBook, path: Path | None = None) -> None:
    bad = _implausible_rates(book)
    if bad:
        raise ValueError(
            'refusing to write implausible rates (per-token/per-1M unit bug?): '
            + '; '.join(bad)
        )
    target = path or DATA_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    # Trailing newline and sorted keys keep the committed diff readable — this
    # file's whole job is to be reviewed by a human before it lands.
    payload = book.model_dump(exclude_none=True)
    payload['models'] = dict(sorted(payload['models'].items()))
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
    )


def _fold(model_id: str) -> str:
    """Fold the separators vendors themselves can't agree on.

    The same model appears as `claude-opus-4.6` on Anthropic's pricing page
    (display-name slug) and `claude-opus-4-6` on the wire; Gemini ids have the
    same dot/dash split between docs anchors and API ids. Case is folded for
    hosts that stylize ids (`Llama-3.3-70B-Instruct`).
    """
    return model_id.replace('.', '-').lower()


def get_entry(model_id: str, book: PriceBook | None = None) -> ModelEntry | None:
    """Exact lookup first; on a miss, a unique dot/dash-insensitive match.

    Ambiguity returns None rather than a guess — two book ids folding to the
    same key means the caller's id genuinely underdetermines the model, and a
    wrong price is worse than a missing one.
    """
    b = book or load_book()
    entry = b.get(model_id)
    if entry is not None:
        return entry
    folded = _fold(model_id)
    matches = [k for k in b.models if _fold(k) == folded]
    return b.models[matches[0]] if len(matches) == 1 else None


def get_price(
    model_id: str,
    tier: str = STANDARD,
    book: PriceBook | None = None,
    at: datetime | None = None,
) -> Price | None:
    """Return one tier's rates, or None if the model or tier is unknown.

    `at` (a UTC datetime) selects the time-of-day variant: when the vendor
    publishes a peak window and `at` falls inside it, the peak rate is
    returned. `at=None` — the default, and the only option that is
    deterministic without a moment to anchor on — means the off-peak scalar.

    None means "not published here" and callers must handle it. This function
    will not substitute a zero, because a silent $0 is how a billing bug hides.
    """
    entry = get_entry(model_id, book)
    if entry is None:
        return None
    price = entry.tiers.get(tier)
    if price is None or at is None:
        return price
    return price.for_time(at)


def estimate_cost(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    tier: str = STANDARD,
    book: PriceBook | None = None,
    at: datetime | None = None,
) -> float | None:
    """USD for one call's usage, or None when the model/tier is unknown.

    `input_tokens` is assumed to be the provider's reported prompt count, which
    already INCLUDES the cached subsets — so those are subtracted out and
    repriced at their own rates rather than billed twice.

    `at` (UTC) prices the call at the rate in force at that moment — see
    `get_price`. Deterministic either way: the book is a snapshot, never a
    live fetch.

    A vendor that publishes no cache rate gets its cached tokens billed at the
    full input rate. That over-states rather than under-states, and it beats
    inventing a discount multiplier: this book records what vendors publish.
    """
    price = get_price(model_id, tier, book, at=at)
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
