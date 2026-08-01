"""Reconcile what the sources said, and diff the result against the book.

Pure: takes already-fetched results, returns verdicts. No network, so it is
directly testable with hand-built inputs.

Two rules here are the whole point of the module:

1. **Match on exact ids only.** Fuzzy matching across sources produced garbage
   at a 0.72 similarity threshold — `glm-4.5-flash` matched *Gemini 1.5 Flash*,
   `grok-4.3` matched *Grok 3*, `claude-haiku-4-5` matched *Claude 3 Haiku*.
   Exact matching cut ~89 noisy proposals to ~10 real ones. A model that only
   one source names is reported as `single`, never guessed at.

2. **Conflicts are reported, not resolved.** When two sources disagree, neither
   is automatically right — both have been observed wrong within a week. The
   tool's job is to surface the disagreement so a human fetches the vendor page.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .models import STANDARD, ModelEntry, Price, PriceBook
from .sources.base import SourceResult

# Sub-cent-per-million differences are rounding noise between a vendor's table
# and an aggregator's transcription, not a price change.
EPSILON = 1e-9

# Vendor pages outrank these when composing a book entry. Not because the
# aggregators are bad, but because neither is the primary document: Simon's
# feed is a hand-maintained mirror, and OpenRouter prices its own routing,
# which can legitimately diverge from what the model author bills directly.
# Members here widen conflict detection but never corroborate a write.
AGGREGATOR_SOURCES = frozenset({'llm-prices.com', 'openrouter'})


class Agreement(str, Enum):
    AGREE = 'agree'  # >= 2 sources, identical
    CONFLICT = 'conflict'  # >= 2 sources, different
    SINGLE = 'single'  # exactly 1 source


class Drift(str, Enum):
    SAME = 'same'
    CHANGED = 'changed'
    NEW = 'new'
    ABSENT = 'absent'  # in the book, no source mentions it this run


@dataclass(frozen=True)
class Verdict:
    model_id: str
    agreement: Agreement
    values: dict[str, Price]  # source name -> what it said

    @property
    def vendor_value(self) -> Price | None:
        for name, price in self.values.items():
            if name not in AGGREGATOR_SOURCES:
                return price
        return None

    @property
    def best(self) -> Price:
        """The value to record: a vendor page if one saw it, else whatever did."""
        return self.vendor_value or next(iter(self.values.values()))

    @property
    def corroborated_by(self) -> list[str]:
        return sorted(self.values)


@dataclass(frozen=True)
class BookDelta:
    model_id: str
    drift: Drift
    old: Price | None
    new: Price | None
    verdict: Verdict | None


def _same(a: Price, b: Price) -> bool:
    def eq(x: float | None, y: float | None) -> bool:
        if x is None or y is None:
            # One source not publishing a cache rate is not a disagreement about
            # one — the aggregator has no cache-write field at all.
            return True
        return abs(x - y) < EPSILON

    return (
        eq(a.input, b.input)
        and eq(a.output, b.output)
        and eq(a.cache_read, b.cache_read)
        and eq(a.cache_write, b.cache_write)
    )


def reconcile(results: list[SourceResult]) -> dict[str, Verdict]:
    """Group every source's output by exact model id and classify agreement."""
    by_model: dict[str, dict[str, Price]] = {}
    for res in results:
        if not res.ok:
            continue
        for model_id, price in res.prices.items():
            by_model.setdefault(model_id, {})[res.source] = price

    verdicts: dict[str, Verdict] = {}
    for model_id, values in by_model.items():
        if len(values) == 1:
            agreement = Agreement.SINGLE
        else:
            prices = list(values.values())
            agreement = (
                Agreement.AGREE
                if all(_same(prices[0], p) for p in prices[1:])
                else Agreement.CONFLICT
            )
        verdicts[model_id] = Verdict(model_id, agreement, values)
    return verdicts


def diff_book(book: PriceBook, verdicts: dict[str, Verdict]) -> list[BookDelta]:
    """What would change if the verdicts were written into the book."""
    deltas: list[BookDelta] = []
    for model_id, verdict in sorted(verdicts.items()):
        entry = book.get(model_id)
        old = entry.tiers.get(STANDARD) if entry else None
        new = verdict.best
        if old is None:
            deltas.append(BookDelta(model_id, Drift.NEW, None, new, verdict))
        elif not _same(old, new):
            deltas.append(BookDelta(model_id, Drift.CHANGED, old, new, verdict))
        else:
            deltas.append(BookDelta(model_id, Drift.SAME, old, new, verdict))

    seen = set(verdicts)
    for model_id, entry in sorted(book.models.items()):
        if model_id not in seen:
            deltas.append(
                BookDelta(model_id, Drift.ABSENT, entry.tiers.get(STANDARD), None, None)
            )
    return deltas


def is_corroborated(delta: BookDelta) -> bool:
    """True when at least one FIRST-PARTY source saw this model.

    Aggregator-only rows are refused by default, and `o3` is why: the feed
    carries its pre-cut launch price of 10/40 while OpenAI's own page says 2/8.
    Writing that into the book would replace a loud absence (`get_price` returns
    None, caller must handle it) with a quiet 5x error. An unverified row is
    worse than a missing one precisely because nothing downstream can tell.
    """
    return delta.verdict is not None and delta.verdict.vendor_value is not None


def apply_deltas(
    book: PriceBook, deltas: list[BookDelta], updated_at: str
) -> PriceBook:
    """Return a NEW book with NEW/CHANGED deltas written in.

    ABSENT entries are left alone on purpose. A source failing to mention a model
    is not evidence the model was withdrawn — far more often the parser drifted,
    and silently dropping rows would turn one broken selector into a mass
    deletion of billing data.
    """
    models = dict(book.models)
    for d in deltas:
        if d.drift not in (Drift.NEW, Drift.CHANGED) or d.new is None:
            continue
        existing = models.get(d.model_id)
        tiers = dict(existing.tiers) if existing else {}
        tiers[STANDARD] = d.new
        models[d.model_id] = ModelEntry(
            id=d.model_id,
            vendor=_vendor_of(d),
            tiers=tiers,
            context_window=existing.context_window if existing else None,
            sources=d.verdict.corroborated_by if d.verdict else [],
            note=existing.note if existing else None,
        )
    return PriceBook(updated_at=updated_at, models=models)


def _vendor_of(delta: BookDelta) -> str:
    if delta.verdict:
        for name in delta.verdict.values:
            if name not in AGGREGATOR_SOURCES:
                return name
    return 'unknown'
