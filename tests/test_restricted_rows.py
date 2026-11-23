"""A published prompt restriction is a rate fact the diff must see.

`Price.max_context` is where the book has a price, not where the API stops —
but adding, moving or withdrawing it changes what a caller may be billed, so it
participates in strict equality and rides into history like any other field.
"""

from __future__ import annotations

from llm_price_tracker.compare import _same
from llm_price_tracker.models import Price


def test_adding_a_restriction_is_a_change() -> None:
    plain = Price(input=12.5, output=75.0, effective_from='2026-11-22')
    restricted = plain.model_copy(update={'max_context': 272_000})
    assert not _same(plain, restricted)


def test_moving_the_restriction_is_a_change() -> None:
    a = Price(input=12.5, output=75.0, max_context=272_000)
    b = Price(input=12.5, output=75.0, max_context=200_000)
    assert not _same(a, b)


def test_the_same_restriction_is_not_a_change() -> None:
    a = Price(input=12.5, output=75.0, max_context=272_000)
    b = Price(input=12.5, output=75.0, max_context=272_000)
    assert _same(a, b)


def test_a_restriction_cannot_coexist_with_a_long_band() -> None:
    import pytest

    with pytest.raises(ValueError):
        Price(
            input=12.5, output=75.0, max_context=272_000,
            long_context=Price(input=25.0, output=112.5),
            long_context_above=272_000,
        )
