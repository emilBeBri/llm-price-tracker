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
    ANNOUNCED = 'announced'  # same rates, a new/changed not-yet-in-force one
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


def _agree(a: Price, b: Price) -> bool:
    """Cross-source agreement: compare the scalar rates only.

    Lenient on the time-of-day variant: a source without a peak rate (every
    aggregator) is not disagreeing with the one vendor that publishes one —
    absence of a field the source doesn't model is not a conflict, exactly
    like the cache-write column.
    """

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


def _same(a: Price, b: Price) -> bool:
    """Book-diff equality: fully strict. Also the history-push test, because a
    row that is not the same as the incoming one is exactly the row that has to
    be remembered.

    Where `_agree` is lenient (cross-source), the book diff must be strict.
    `_agree` tolerates a field one source does not publish, because an
    aggregator with no cache column is not disagreeing with the vendor that has
    one. A book diff has no such excuse — both sides are supposed to carry every
    field, so:

    * "the vendor added a peak window" / "moved the window by an hour" is a fact
      change, or a window edit upstream would never reach the book;
    * "the vendor stopped publishing a cache rate" (Gemini's TTS rows dropped
      their caching row) is one too. Keeping the old figure silently charges a
      rate nobody publishes any more — and it is HIGHER than the input-rate
      fallback a missing cache rate gets, so the silence costs money.
    """
    if any(
        not _rate_eq(getattr(a, f), getattr(b, f))
        for f in ('input', 'output', 'cache_read', 'cache_write')
    ):
        return False
    if (a.peak is None) != (b.peak is None):
        return False
    if a.peak is None:
        return True
    # Whole-model equality, not a hand-listed tuple of fields: `days` was
    # added to TimeWindow in 2026-09 and a field-by-field comparison would
    # have kept a weekday restriction out of the book by simply not looking
    # at it. Pydantic compares by value, so the next field is covered too.
    return _agree(a.peak, b.peak) and (a.peak_windows or []) == (b.peak_windows or [])


def _same_scheduled(a: Price, b: Price) -> bool:
    """Announcement equality, whole-row.

    A selected comparison rather than `a.scheduled == b.scheduled`, for the
    reason `_same` uses whole-model equality on the peak variant: `days` was
    added to `TimeWindow` in 2026-09 and a hand-listed tuple would have kept a
    weekday restriction out of the book by simply not looking at it. A future
    rate's own `scheduled` is dropped because announcements do not stack.
    """

    def rows(p: Price) -> list[dict]:
        return [r.model_dump(exclude={'scheduled'}) for r in (p.scheduled or [])]

    return rows(a) == rows(b)


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
                if all(_agree(prices[0], p) for p in prices[1:])
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
        elif not _same_scheduled(old, new):
            # The rate in force today did not move; the vendor published (or
            # withdrew, or re-dated) a later one. That is a fact worth writing
            # and reporting, and NOT a price change: making it a CHANGED row
            # would exit 1 on a watched model every day until the announced
            # date arrived, which is how you train yourself to dismiss the one
            # that matters.
            deltas.append(BookDelta(model_id, Drift.ANNOUNCED, old, new, verdict))
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

    A CHANGED rate does not overwrite its predecessor: the outgoing row is
    pushed onto `ModelEntry.history[tier]` and the incoming one is stamped
    `effective_from=updated_at`. That is what lets a consumer price a past call
    at the rate it actually cost instead of at today's — without it, every
    vendor price move silently rewrites the whole billing history.

    An ANNOUNCED delta writes only the future row, and the rate in force keeps
    the date it was first observed on — restamping an unchanged rate would walk
    today's start date forward on every refresh, which is precisely the lie
    history exists to prevent.
    """
    models = dict(book.models)
    for d in deltas:
        if d.drift not in (Drift.NEW, Drift.CHANGED, Drift.ANNOUNCED):
            continue
        if d.new is None:
            continue
        existing = models.get(d.model_id)
        tiers = dict(existing.tiers) if existing else {}
        history = (
            {k: list(v) for k, v in existing.history.items()} if existing else {}
        )
        outgoing = tiers.get(STANDARD)
        if outgoing is not None and not _same(outgoing, d.new):
            history.setdefault(STANDARD, []).append(_plain(outgoing))
            stamp = updated_at
        else:
            stamp = outgoing.effective_from if outgoing is not None else updated_at
        tiers[STANDARD] = d.new.model_copy(update={'effective_from': stamp})
        models[d.model_id] = ModelEntry(
            id=d.model_id,
            vendor=_vendor_of(d),
            tiers=tiers,
            context_window=existing.context_window if existing else None,
            sources=d.verdict.corroborated_by if d.verdict else [],
            note=existing.note if existing else None,
            history=history,
        )
    for model_id, entry in list(models.items()):
        models[model_id] = _promote_arrived(entry, updated_at)
    return PriceBook(updated_at=updated_at, models=models)


def _plain(price: Price) -> Price:
    """The rate alone, with no announcements riding along.

    History records what a rate WAS. An announcement is a claim about a later
    date and gets recomputed from the page every refresh; copying one into a
    superseded row would resurrect it the day the vendor retracts it.
    """
    return price.model_copy(update={'scheduled': None})


def _promote_arrived(entry: ModelEntry, today: str) -> ModelEntry:
    """Fold an announced rate whose start date has arrived into `tiers`.

    Runs over EVERY model, not only the ones this run changed. A vendor page
    that keeps publishing 'through <date>' after that date has passed parses to
    exactly the row already in the book, so no delta is raised and nothing
    would ever move it — the book would go on quoting a discount that ended,
    and `at=None` callers pay a rate nobody is charging any more. (`price_at`
    is correct throughout: the announced row is dated, so any query on or after
    that date already picks it. This is the `at=None` half.)

    The promoted row keeps its PUBLISHED start date rather than `today` — the
    one place in this book where `effective_from` is a vendor date instead of a
    first-observed one, because here the vendor said it out loud.
    """
    price = entry.tiers.get(STANDARD)
    if price is None or not price.scheduled:
        return entry
    arrived = [
        p for p in price.scheduled if p.effective_from and p.effective_from <= today
    ]
    if not arrived:
        return entry
    winner = max(arrived, key=lambda p: p.effective_from or '')
    remaining = [p for p in price.scheduled if p is not winner]
    promoted = winner.model_copy(update={'scheduled': remaining or None})
    history = {k: list(v) for k, v in entry.history.items()}
    if not _same(price, promoted):
        history.setdefault(STANDARD, []).append(_plain(price))
    tiers = dict(entry.tiers)
    tiers[STANDARD] = promoted
    return entry.model_copy(update={'tiers': tiers, 'history': history})


def _rate_eq(x: float | None, y: float | None) -> bool:
    """Strict on presence, tolerant on float noise: `None` and a number are
    different facts, 0.075 and 0.07500000000001 are the same one."""
    if x is None or y is None:
        return x is y
    return abs(x - y) < EPSILON


def _vendor_of(delta: BookDelta) -> str:
    if delta.verdict:
        for name in delta.verdict.values:
            if name not in AGGREGATOR_SOURCES:
                return name
    return 'unknown'
