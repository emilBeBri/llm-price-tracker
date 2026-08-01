"""The Source port, plus the guardrail every source inherits.

A source is one place that claims to know a price. There is deliberately more
than one, because the failure mode this tracker exists to catch is not "no data"
— it is **a single source being confidently wrong**. Observed within one week:

  * llm-prices.com had `o3` at its pre-cut launch price of 10/40 (real: 2/8) and
    DeepSeek's cache rate off by 10x.
  * A local docs mirror crawled three days stale missed a 5x cut to GPT-5.6 Luna
    and reported the old price with no indication anything had changed.

Either source alone reads as authoritative. Only the disagreement between them
is informative, which is why `fetch_all` collects everything rather than picking
a winner, and `compare` reports conflicts instead of resolving them.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..models import Price


@dataclass(frozen=True)
class SourceResult:
    """What one source returned on one run."""

    source: str
    url: str
    ok: bool
    prices: dict[str, Price] = field(default_factory=dict)
    # Why it failed, or what looked off about a success.
    note: str | None = None
    # The raw fetched document, kept so a bad parse can be post-mortemed after
    # the live page has moved on. None only when the HTTP request itself failed.
    body: str | None = field(default=None, repr=False)


def dollars(text: str) -> float | None:
    """First USD amount in a string. '$0.075 (text/image)' -> 0.075.

    Takes the FIRST match on purpose: vendors render a struck-through list price
    before the effective one, and the effective price is not always second.
    Sources that need the other cell should split the text themselves.
    """
    m = re.search(r'\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)', text)
    return float(m.group(1).replace(',', '')) if m else None


class Source(ABC):
    """One price source. Subclasses implement `parse`; the base handles the rest.

    `accept` is per-source and load-bearing, not boilerplate. These docs sites
    content-negotiate, and the same URL yields genuinely different documents:
    developers.openai.com returns 19KB of markdown pipe-tables or 587KB of HTML
    depending on this header alone. A single shared Accept across sources fed
    markdown to an HTML parser and produced zero models for days.

    The rule is that `accept` must match what `parse` expects — NOT that any one
    format is better. It cuts both ways: OpenAI's markdown is the richer
    document (the HTML renders half its tables client-side), while Google's and
    DeepSeek's parsers need the HTML.
    """

    name: str
    url: str
    accept: str = 'text/html,*/*'
    # Model-id fragments that must appear in a successful parse. These are
    # identifiers, not prices, so they change rarely — which makes them a clean
    # signal that the page structure drifted rather than that prices moved.
    expect: tuple[str, ...] = ()

    @abstractmethod
    def parse(self, body: str) -> dict[str, Price]:
        """Turn a fetched page into {vendor model id: Price}. Empty == drifted."""

    def fetch(self, client) -> SourceResult:
        body: str | None = None
        try:
            resp = client.get(self.url, headers={'Accept': self.accept})
            resp.raise_for_status()
            body = resp.text
            prices = self.parse(body)
        except Exception as e:  # noqa: BLE001 - one bad source must not kill a run
            return SourceResult(
                self.name, self.url, False, note=f'{type(e).__name__}: {e}', body=body
            )

        if not prices:
            return SourceResult(
                self.name,
                self.url,
                False,
                note='fetched, but parsed 0 models — page structure changed',
                body=body,
            )
        missing = [a for a in self.expect if not any(a in mid for mid in prices)]
        if self.expect and len(missing) == len(self.expect):
            return SourceResult(
                self.name,
                self.url,
                False,
                prices=prices,
                note=(
                    f'parsed {len(prices)} rows but none match {list(self.expect)} '
                    f'— page structure changed'
                ),
                body=body,
            )
        note = (
            f'expected id(s) absent (renamed?): {", ".join(missing)}'
            if missing
            else None
        )
        return SourceResult(
            self.name, self.url, True, prices=prices, note=note, body=body
        )
