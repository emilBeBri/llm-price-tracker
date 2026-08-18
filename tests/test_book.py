"""The offline read path: lookup, cost estimation, round-tripping the book."""

from datetime import UTC, datetime


def _utc(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 18, hour, minute, tzinfo=UTC)


import pytest

from llm_price_tracker import estimate_cost, get_price, load_book, save_book
from llm_price_tracker.book import DATA_PATH
from llm_price_tracker.models import (
    STANDARD,
    ModelEntry,
    Price,
    PriceBook,
    TimeWindow,
)


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


def test_save_book_refuses_a_unit_explosion(tmp_path):
    """Per-token vs per-1M is a factor of exactly 1e6 — the classic bug this
    gate exists for. It must fail the WRITE, before garbage reaches the diff."""
    b = PriceBook(
        updated_at='2026-08-01',
        models={
            'buggy': ModelEntry(
                id='buggy',
                vendor='acme',
                tiers={STANDARD: Price(input=140_000.0, output=280_000.0)},
            )
        },
    )
    with pytest.raises(ValueError, match='implausible'):
        save_book(b, tmp_path / 'prices.json')


def test_negative_price_is_rejected_by_the_model():
    with pytest.raises(ValueError):
        Price(input=-1.0, output=2.0)


def test_lookup_folds_dots_and_dashes_both_ways(tmp_path):
    """Anthropic's page slugs say claude-opus-4.6; the wire says claude-opus-4-6."""
    b = PriceBook(
        updated_at='2026-08-01',
        models={
            'claude-opus-4.6': ModelEntry(
                id='claude-opus-4.6',
                vendor='anthropic',
                tiers={STANDARD: Price(input=5.0, output=25.0)},
            )
        },
    )
    assert get_price('claude-opus-4-6', book=b).input == 5.0
    assert get_price('Claude-Opus-4.6', book=b).input == 5.0
    assert get_price('claude-opus-4-7', book=b) is None


def test_exact_id_beats_a_normalized_match(tmp_path):
    entries = {
        mid: ModelEntry(id=mid, vendor='x', tiers={STANDARD: Price(input=p, output=p)})
        for mid, p in (('m-1.0', 1.0), ('m-1-0', 2.0))
    }
    b = PriceBook(updated_at='2026-08-01', models=entries)
    assert get_price('m-1-0', book=b).input == 2.0, 'exact must win'


def test_ambiguous_normalized_match_returns_none_never_a_guess(tmp_path):
    entries = {
        mid: ModelEntry(
            id=mid, vendor='x', tiers={STANDARD: Price(input=1.0, output=1.0)}
        )
        for mid in ('m-1.0', 'm-1-0')
    }
    b = PriceBook(updated_at='2026-08-01', models=entries)
    # 'M.1.0' is an exact miss and folds onto BOTH ids.
    assert get_price('M-1.0', book=b) is None


# --------------------------------------------------------------------------- #
# Time-of-day variant: DeepSeek's peak/off-peak policy is the live instance.
# The scalar fields stay the off-peak rate; `at` selects the peak variant.
# --------------------------------------------------------------------------- #
PEAK_WINDOWS = [
    TimeWindow(start='01:00', end='04:00'),
    TimeWindow(start='06:00', end='10:00'),
]


def _peak_book() -> PriceBook:
    return PriceBook(
        updated_at='2026-08-18',
        models={
            'deepseek-v4-flash': ModelEntry(
                id='deepseek-v4-flash',
                vendor='deepseek',
                tiers={
                    STANDARD: Price(
                        input=0.22,
                        output=0.66,
                        cache_read=0.007,
                        peak=Price(input=0.44, output=1.32, cache_read=0.014),
                        peak_windows=PEAK_WINDOWS,
                    )
                },
            )
        },
    )


def test_for_time_returns_peak_inside_a_window_and_off_peak_outside():
    price = get_price('deepseek-v4-flash', book=_peak_book())
    inside = price.for_time(_utc(2, 30))  # 02:30 UTC is peak
    assert inside.input == 0.44 and inside.cache_read == 0.014
    outside = price.for_time(_utc(12, 0))  # noon is off-peak
    assert outside.input == 0.22


def test_for_time_window_edges_are_half_open():
    price = get_price('deepseek-v4-flash', book=_peak_book())
    assert price.for_time(_utc(1, 0)).input == 0.44  # start incl.
    assert price.for_time(_utc(4, 0)).input == 0.22  # end excl.


def test_for_time_wraps_midnight():
    wrapped = Price(
        input=1.0,
        output=1.0,
        peak=Price(input=2.0, output=2.0),
        peak_windows=[TimeWindow(start='22:00', end='02:00')],
    )
    assert wrapped.for_time(_utc(23, 0)).input == 2.0
    assert wrapped.for_time(_utc(1, 0)).input == 2.0
    assert wrapped.for_time(_utc(15, 0)).input == 1.0


def test_no_at_is_off_peak_and_deterministic():
    """The default must not consult a clock: same call, same answer, and the
    headline (off-peak) rate, never a peak surprise for a time-unaware caller."""
    book = _peak_book()
    assert get_price('deepseek-v4-flash', book=book).input == 0.22
    assert estimate_cost('deepseek-v4-flash', 1_000_000, 0, book=book) == pytest.approx(
        0.22
    )


def test_estimate_cost_with_at_prices_peak_tokens_at_peak_rate():
    book = _peak_book()
    cost = estimate_cost(
        'deepseek-v4-flash',
        1_000_000,
        0,
        book=book,
        at=_utc(7, 0),  # inside 06:00-10:00
    )
    assert cost == pytest.approx(0.44)
