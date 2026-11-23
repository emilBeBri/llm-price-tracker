"""The book's calendar is instants, not dates.

A date-only `effective_from` is a first-observed lower bound and means 00:00Z.
A vendor that states a TIME — OpenAI's Pacific-time promotion boundary — stores
an offset-bearing instant, and both `price_at` and the promotion step compare
instants. Comparing date strings instead is how the ultrafast promotion ended
eight hours early.
"""

from __future__ import annotations

from datetime import UTC, datetime

from llm_price_tracker.compare import _promote_arrived
from llm_price_tracker.models import STANDARD, ModelEntry, Price


def _entry() -> ModelEntry:
    promo = Price(
        input=6.0, output=30.0, effective_from='2026-11-22',
        scheduled=[Price(input=12.0, output=60.0, effective_from='2027-03-01T08:00:00+00:00')],
    )
    return ModelEntry(
        id='gpt-6.2-sol', vendor='openai',
        tiers={STANDARD: Price(input=2.0, output=10.0, effective_from='2026-11-22'),
               'ultrafast': promo},
    )


def test_price_at_uses_the_instant_on_a_tier_row() -> None:
    entry = _entry()
    before = entry.price_at(datetime(2027, 3, 1, 7, 59, tzinfo=UTC), tier='ultrafast')
    at = entry.price_at(datetime(2027, 3, 1, 8, 0, tzinfo=UTC), tier='ultrafast')
    after = entry.price_at(datetime(2027, 3, 1, 9, 0, tzinfo=UTC), tier='ultrafast')
    assert before.input == 6.0
    assert at.input == 12.0
    assert after.input == 12.0


def test_promotion_covers_every_tier_not_only_standard() -> None:
    """The ultrafast row would otherwise stay promotional for `at=None`
    callers for ever, quoting 3x after the vendor doubled it."""
    promoted = _promote_arrived(_entry(), '2027-03-01T08:00:00+00:00')
    assert promoted.tiers['ultrafast'].input == 12.0
    assert promoted.history['ultrafast'][-1].input == 6.0
    # Standard was untouched: a premium-only arrival is not a standard change.
    assert promoted.tiers[STANDARD].input == 2.0
    assert STANDARD not in promoted.history


def test_promotion_does_not_fire_before_the_instant() -> None:
    not_yet = _promote_arrived(_entry(), '2027-03-01T07:59:00+00:00')
    assert not_yet.tiers['ultrafast'].input == 6.0
