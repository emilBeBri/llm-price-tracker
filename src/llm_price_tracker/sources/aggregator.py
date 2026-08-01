"""The non-vendor cross-check sources: Simon Willison's feed and OpenRouter.

Both are kept deliberately as cross-checks, never as write authority — they
join `AGGREGATOR_SOURCES` in compare.py, so a model only they know is refused
by `refresh` unless a first-party page corroborates it. Their value is that
they are *independent* of the vendors and of each other: agreement with a
vendor page is real corroboration, and disagreement is the signal to go look.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from ..models import Price
from .base import Source


class LlmPricesSource(Source):
    """Simon Willison's aggregated feed.

    A curated human artifact, not a scraper: `simonw/llm-prices` holds
    hand-edited per-vendor `data/*.json`, `scripts/build.py` aggregates them,
    and a gh-pages workflow publishes on push to main — **there is no
    schedule**. So it is exactly as fresh as its author's last commit, which is
    why it can carry a stale row indefinitely (o3 at its pre-cut 10/40 for
    months) and why it must never be the only voice.
    """

    name = 'llm-prices.com'
    url = 'https://www.llm-prices.com/current-v1.json'
    accept = 'application/json,*/*'
    expect = ('gpt-5', 'claude', 'gemini')

    def parse(self, body: str) -> dict[str, Price]:
        rows = json.loads(body).get('prices', [])
        out: dict[str, Price] = {}
        for r in rows:
            mid, inp, outp = r.get('id'), r.get('input'), r.get('output')
            if not mid or inp is None or outp is None:
                continue
            out[str(mid)] = Price(
                input=float(inp),
                output=float(outp),
                # The feed carries a single cached-input field and no
                # cache-WRITE equivalent, so cache_write stays None here. A
                # vendor source is the only way to fill that in.
                cache_read=None
                if r.get('input_cached') is None
                else float(r['input_cached']),
            )
        return out


class OpenRouterSource(Source):
    """OpenRouter's `/api/v1/models` — the one clean machine-readable feed.

    ~690 models with per-TOKEN prices as decimal strings. Two things keep it an
    aggregator rather than a vendor source: it prices *its own routing*, which
    can diverge from what the model author bills directly, and its ids are
    `author/model` slugs rather than vendor wire ids. So it widens conflict
    DETECTION (any exact id match after stripping the author prefix), but it
    never grants write authority — that stays with first-party pages.

    Ids that cannot correspond to a vendor list price are dropped at the edge:
    `:free`/`:extended`/`:thinking` variants are OpenRouter routing products,
    and rows priced 0/0 are promos. Either kind, exact-matching a real vendor
    id, would render as a conflict that teaches you to ignore the table.
    """

    name = 'openrouter'
    url = 'https://openrouter.ai/api/v1/models'
    accept = 'application/json,*/*'
    expect = ('gpt-5', 'claude', 'gemini')

    def parse(self, body: str) -> dict[str, Price]:
        out: dict[str, Price] = {}
        for rec in json.loads(body).get('data', []):
            rid = str(rec.get('id') or '')
            pricing = rec.get('pricing') or {}
            # 'author/model' -> 'model'; the author prefix is OpenRouter
            # taxonomy, not part of any vendor's wire id.
            _, _, model_id = rid.partition('/')
            if not model_id or ':' in model_id:
                continue
            inp = _per_token_to_mtok(pricing.get('prompt'))
            outp = _per_token_to_mtok(pricing.get('completion'))
            if inp is None or outp is None or (inp == 0.0 and outp == 0.0):
                continue
            # Same-suffix ids from two authors keep the first row seen; a
            # silent overwrite here could pair one author's input price with
            # another's output in the conflict table.
            out.setdefault(
                model_id,
                Price(
                    input=inp,
                    output=outp,
                    cache_read=_per_token_to_mtok(pricing.get('input_cache_read')),
                    cache_write=_per_token_to_mtok(pricing.get('input_cache_write')),
                ),
            )
        return out


def _per_token_to_mtok(raw: str | None) -> float | None:
    """'0.00000014' USD/token -> 0.14 USD/Mtok, exactly.

    Scaled in Decimal so the result is the closest float to the DECIMAL value
    the feed printed, with no intermediate binary rounding. Float scaling
    happens to land clean for today's price points, and compare.EPSILON would
    absorb the dust anyway — but a value like 0.13999999999999998 reaching the
    committed book would put noise in every human-reviewed diff, and exactness
    at the edge costs one line.

    Negative maps to None: OpenRouter's own meta-routers (`openrouter/auto`,
    `openrouter/fusion`, ...) publish "-1" as a your-price-varies sentinel,
    not a rate. Found on the first live run, via the raw snapshot.
    """
    if raw in (None, ''):
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return float(value * 1_000_000) if value >= 0 else None
