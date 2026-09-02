"""The data model. Pure — stdlib + pydantic only, no network, no scraping.

Everything here is a *vendor fact*: what a vendor charges, as published. It is
deliberately free of any consuming app's billing choices (which tier to assume,
whether to project a future price). Those belong to the app, not to this book.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# The tier every entry must carry. Vendors publish several live rates for the
# same model at once — OpenAI standard/batch/priority, xAI and Gemini <200k vs
# >=200k, Anthropic standard/batch — and a cost estimator that sees only a model
# name and token counts cannot know which applied. So the book stores every tier
# it can read and names this one as the default a caller gets when it does not
# ask. Choosing among them is the caller's job, not the book's.
STANDARD = 'standard'


class TimeWindow(BaseModel):
    """One daily window when a time-variant rate is in force.

    `start` is inclusive, `end` exclusive, both 'HH:MM' (24h). A window may
    wrap midnight (`start` > `end`). Implicitly UTC: a vendor that publishes
    another timezone's window is converted to UTC at the parse edge, so every
    window in the book is directly comparable and callers never guess a zone.
    """

    start: str
    end: str

    @staticmethod
    def _minutes(hhmm: str) -> int:
        h, m = hhmm.split(':')
        return int(h) * 60 + int(m)

    def contains(self, at: datetime) -> bool:
        """True when `at` (a UTC datetime) falls inside [start, end)."""
        t = at.hour * 60 + at.minute
        s, e = self._minutes(self.start), self._minutes(self.end)
        if s == e:
            return False  # a zero-length window matches nothing
        if s < e:
            return s <= t < e
        return t >= s or t < e  # wraps midnight


class Price(BaseModel):
    """One tier's rates for one model, USD per 1,000,000 tokens.

    `None` means the vendor publishes no such rate (not "free", and not
    "unknown-so-guess"). A consumer that wants a fallback multiplier must apply
    it itself — inventing one here would launder a guess into a recorded fact.

    Negative rates are rejected at the model level: no vendor pays you to use
    their API, so a negative can only be a parser bug. The upper sanity bound
    lives in `save_book`, not here — a heuristic ceiling belongs at write time,
    where it blocks recording garbage without bricking reads of a book that a
    legitimately expensive future model would otherwise invalidate.
    """

    input: float = Field(ge=0.0)
    output: float = Field(ge=0.0)
    cache_read: float | None = Field(default=None, ge=0.0)
    cache_write: float | None = Field(default=None, ge=0.0)
    # ISO date (YYYY-MM-DD) this rate was FIRST OBSERVED in this book — not a
    # vendor announcement date, which no source publishes reliably. A rate was
    # usually in force for some unknown span before we first read it, so this
    # is a lower bound on its start, and `ModelEntry.price_at` says how it
    # treats a query that predates every recorded row. `None` means the row
    # predates history tracking (every row did, before 2026-09-02). On a
    # nested `peak` variant it is meaningless and stays None: a peak rate is a
    # property of the row carrying it, so the PARENT's date governs both.
    effective_from: str | None = None
    # Time-of-day variant: a different rate the vendor applies during
    # `peak_windows` (DeepSeek's 2x peak hours are the live instance). The
    # scalar fields above hold the default (off-peak) rate — the first
    # DeepSeek policy made off-peak the announced headline rate, so the scalar
    # must remain what a time-unaware caller gets. A caller pricing a call at
    # a known moment uses for_time(). Without windows the variant would never
    # apply, so `peak` is only meaningful when `peak_windows` is non-empty.
    peak: Price | None = None
    peak_windows: list[TimeWindow] | None = None

    def for_time(self, at: datetime) -> Price:
        """The scalar rate in force at `at` (UTC): the peak variant if `at`
        falls inside a published window, otherwise this price."""
        if (
            self.peak is not None
            and self.peak_windows
            and any(w.contains(at) for w in self.peak_windows)
        ):
            return self.peak
        return self


class ModelEntry(BaseModel):
    """One model, as one vendor publishes it.

    `id` is the vendor's own wire id, NOT a consuming app's registry key. Apps
    namespace and alias freely (`groq:openai/gpt-oss-120b`, Azure deployment
    names, `-dev` suffixes); mapping their keys onto these ids is their job.
    """

    # Deliberately `id`, not `model_id`: pydantic reserves the `model_` prefix
    # and warns on fields that use it.
    id: str
    vendor: str
    tiers: dict[str, Price]
    context_window: int | None = None
    # Which sources corroborated this entry at the last refresh. Two independent
    # sources agreeing is the closest thing to confidence available here.
    sources: list[str] = Field(default_factory=list)
    note: str | None = None
    # SUPERSEDED rates per tier, oldest first. `tiers` always holds the rate in
    # force now; a refresh that changes a rate pushes the outgoing row here.
    # Split this way on purpose: every existing reader of `tiers` keeps working
    # untouched, the committed diff still shows the current price where it has
    # always been, and a book with no history serialises exactly as before.
    history: dict[str, list[Price]] = Field(default_factory=dict)

    @property
    def standard(self) -> Price | None:
        return self.tiers.get(STANDARD)

    def rate_history(self, tier: str = STANDARD) -> list[Price]:
        """Every rate this book has recorded for `tier`, oldest first, with
        the current one last. Undated rows sort before dated ones — an undated
        row predates history tracking, so it is the oldest thing we know."""
        rows = [*self.history.get(tier, [])]
        current = self.tiers.get(tier)
        if current is not None:
            rows.append(current)
        return sorted(rows, key=lambda p: p.effective_from or '')

    def price_at(self, at: datetime | None = None, tier: str = STANDARD) -> Price | None:
        """The rate in force for `tier` on the date of `at`.

        `at=None` means now, i.e. the current row — the only answer that needs
        no history at all, and the default everywhere.

        A query that predates every recorded row returns the OLDEST known rate
        rather than None. That is a deliberate best-effort: the alternative is
        refusing to price a call the app definitely made, and this book's dates
        are first-observed lower bounds anyway (see `Price.effective_from`), so
        the oldest row is genuinely the best available answer. Compare `at`
        against the returned row's `effective_from` when it matters whether the
        answer is a record or an extrapolation.
        """
        rows = self.rate_history(tier)
        if not rows or at is None:
            return self.tiers.get(tier)
        day = at.date().isoformat()
        in_force = [p for p in rows if p.effective_from is None or p.effective_from <= day]
        return in_force[-1] if in_force else rows[0]


class PriceBook(BaseModel):
    """The committed artifact: every model this tracker knows a price for."""

    updated_at: str
    models: dict[str, ModelEntry] = Field(default_factory=dict)

    def get(self, model_id: str) -> ModelEntry | None:
        return self.models.get(model_id)

    def by_vendor(self, vendor: str) -> dict[str, ModelEntry]:
        return {k: v for k, v in self.models.items() if v.vendor == vendor}
