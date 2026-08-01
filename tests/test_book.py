"""The offline read path: lookup, cost estimation, round-tripping the book."""

import pytest

from llm_price_tracker import estimate_cost, get_price, load_book, save_book
from llm_price_tracker.book import DATA_PATH
from llm_price_tracker.models import STANDARD, ModelEntry, Price, PriceBook


@pytest.fixture
def book(tmp_path):
    b = PriceBook(
        updated_at='2026-08-01',
        models={
            'cheap': ModelEntry(
                id='cheap',
                vendor='acme',
                tiers={
                    STANDARD: Price(
                        input=1.0, output=2.0, cache_read=0.1, cache_write=1.25
                    )
                },
            ),
            'no-cache': ModelEntry(
                id='no-cache',
                vendor='acme',
                tiers={STANDARD: Price(input=10.0, output=20.0)},
            ),
        },
    )
    path = tmp_path / 'prices.json'
    save_book(b, path)
    return load_book(path)


def test_unknown_model_returns_none_never_zero(book):
    # A silent $0 is how a billing bug hides; absence must be loud.
    assert get_price('nope', book=book) is None
    assert estimate_cost('nope', 1_000_000, 1_000_000, book=book) is None


def test_unknown_tier_returns_none(book):
    assert get_price('cheap', tier='batch', book=book) is None


def test_plain_cost(book):
    assert estimate_cost('cheap', 1_000_000, 1_000_000, book=book) == pytest.approx(3.0)


def test_cached_tokens_are_subtracted_from_input_not_billed_twice(book):
    # 1M prompt of which 400k was a cache read: 600k fresh @1.0 + 400k @0.1
    cost = estimate_cost('cheap', 1_000_000, 0, cache_read_tokens=400_000, book=book)
    assert cost == pytest.approx(0.6 + 0.04)


def test_cache_write_uses_its_own_rate(book):
    cost = estimate_cost('cheap', 1_000_000, 0, cache_write_tokens=200_000, book=book)
    assert cost == pytest.approx(0.8 + 0.25)


def test_no_published_cache_rate_bills_at_full_input(book):
    """Over-state rather than invent a discount multiplier."""
    cost = estimate_cost(
        'no-cache', 1_000_000, 0, cache_read_tokens=1_000_000, book=book
    )
    assert cost == pytest.approx(10.0)


def test_negative_fresh_input_is_clamped(book):
    """A malformed usage row must not produce a negative cost."""
    cost = estimate_cost('cheap', 100, 0, cache_read_tokens=1_000_000, book=book)
    assert cost >= 0


def test_round_trip_preserves_values(tmp_path, book):
    path = tmp_path / 'again.json'
    save_book(book, path)
    assert load_book(path).model_dump() == book.model_dump()


def test_shipped_book_parses_and_is_non_empty():
    """The committed artifact is the product; a malformed one is a broken build."""
    shipped = load_book(DATA_PATH)
    assert shipped.models
    for model_id, entry in shipped.models.items():
        assert entry.id == model_id
        std = entry.tiers.get(STANDARD)
        assert std is not None, f'{model_id} has no standard tier'
        assert std.input >= 0 and std.output >= 0
