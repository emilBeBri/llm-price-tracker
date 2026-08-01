"""Simon Willison's llm-prices feed — the one non-vendor source.

Kept deliberately, as a cross-check rather than a replacement. It is a curated
human artifact, not a scraper: `simonw/llm-prices` holds hand-edited per-vendor
`data/*.json`, `scripts/build.py` aggregates them, and a gh-pages workflow
publishes the result on push to main and manual dispatch — **there is no
schedule**. So it is exactly as fresh as its author's last commit, which is why
it can carry a stale row indefinitely and why it must never be the only voice.

Its value is that it is *independent*. When it and a vendor page agree, that is
real corroboration; when they disagree, that is the signal to go look.
"""

from __future__ import annotations

from ..models import Price
from .base import Source


class LlmPricesSource(Source):
    name = 'llm-prices.com'
    url = 'https://www.llm-prices.com/current-v1.json'
    accept = 'application/json,*/*'
    expect = ('gpt-5', 'claude', 'gemini')

    def parse(self, body: str) -> dict[str, Price]:
        import json

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
