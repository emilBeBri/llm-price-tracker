"""Reconciliation is pure, so it gets tested with hand-built inputs.

Every case here is a real incident from the week this tool was written, not an
invented scenario. That is deliberate: these are the failures it exists to
catch, so they are the ones that must not regress.
"""

from llm_price_tracker.compare import (
    Agreement,
    Drift,
    apply_deltas,
    diff_book,
    is_corroborated,
    reconcile,
)
from llm_price_tracker.models import STANDARD, ModelEntry, Price, PriceBook, TimeWindow
from llm_price_tracker.sources.base import SourceResult


def _res(source: str, prices: dict[str, Price], ok: bool = True) -> SourceResult:
    return SourceResult(source, f'https://{source}/', ok, prices)


def test_two_sources_agreeing_is_corroboration():
    p = Price(input=1.0, output=2.0)
    verdicts = reconcile(
        [_res('openai', {'gpt-x': p}), _res('llm-prices.com', {'gpt-x': p})]
    )
    assert verdicts['gpt-x'].agreement is Agreement.AGREE
    assert verdicts['gpt-x'].corroborated_by == ['llm-prices.com', 'openai']


def test_stale_aggregator_row_is_a_conflict_not_a_silent_overwrite():
    """o3: OpenAI's page says 2/8, the feed carries its pre-cut 10/40."""
    verdicts = reconcile(
        [
            _res('openai', {'o3': Price(input=2.0, output=8.0)}),
            _res('llm-prices.com', {'o3': Price(input=10.0, output=40.0)}),
        ]
    )
    v = verdicts['o3']
    assert v.agreement is Agreement.CONFLICT
    # The vendor page wins when a value has to be chosen.
    assert v.best.input == 2.0


def test_cache_only_disagreement_still_counts_as_conflict():
    """deepseek-v4-flash: input and output agree exactly, cache is 10x apart.

    Comparing only input/output would call this agreement and hide a real error.
    """
    verdicts = reconcile(
        [
            _res('deepseek', {'d': Price(input=0.14, output=0.28, cache_read=0.0028)}),
            _res(
                'llm-prices.com',
                {'d': Price(input=0.14, output=0.28, cache_read=0.028)},
            ),
        ]
    )
    assert verdicts['d'].agreement is Agreement.CONFLICT


def test_missing_field_is_not_a_disagreement():
    """The aggregator has no cache-WRITE field at all. Absence != conflict."""
    verdicts = reconcile(
        [
            _res('openai', {'g': Price(input=1.0, output=2.0, cache_write=1.25)}),
            _res('llm-prices.com', {'g': Price(input=1.0, output=2.0)}),
        ]
    )
    assert verdicts['g'].agreement is Agreement.AGREE


def test_single_source_is_flagged_not_silently_trusted():
    verdicts = reconcile(
        [_res('llm-prices.com', {'mistral-x': Price(input=1.0, output=2.0)})]
    )
    assert verdicts['mistral-x'].agreement is Agreement.SINGLE


def test_broken_source_contributes_nothing():
    verdicts = reconcile(
        [
            _res('openai', {'a': Price(input=1.0, output=2.0)}),
            SourceResult('google', 'https://g/', False, {}, 'page structure changed'),
        ]
    )
    assert set(verdicts) == {'a'}


def test_aggregator_only_model_is_not_corroborated():
    verdicts = reconcile([_res('llm-prices.com', {'x': Price(input=1.0, output=2.0)})])
    deltas = diff_book(PriceBook(updated_at='2026-01-01'), verdicts)
    assert [is_corroborated(d) for d in deltas] == [False]


def test_vendor_seen_model_is_corroborated_even_alone():
    """One first-party page is enough. It is the primary document."""
    verdicts = reconcile([_res('deepseek', {'x': Price(input=1.0, output=2.0)})])
    deltas = diff_book(PriceBook(updated_at='2026-01-01'), verdicts)
    assert [is_corroborated(d) for d in deltas] == [True]


def _book(model_id: str, price: Price) -> PriceBook:
    return PriceBook(
        updated_at='2026-01-01',
        models={
            model_id: ModelEntry(id=model_id, vendor='openai', tiers={STANDARD: price})
        },
    )


def test_price_cut_shows_as_changed():
    """GPT-5.6 Luna's 5x cut — the change this tool exists to notice."""
    book = _book('gpt-5.6-luna', Price(input=1.0, output=6.0))
    verdicts = reconcile(
        [_res('openai', {'gpt-5.6-luna': Price(input=0.2, output=1.2)})]
    )
    (delta,) = diff_book(book, verdicts)
    assert delta.drift is Drift.CHANGED
    assert delta.old.input == 1.0 and delta.new.input == 0.2


def test_unchanged_price_is_not_drift():
    p = Price(input=1.0, output=6.0)
    book = _book('m', p)
    (delta,) = diff_book(book, reconcile([_res('openai', {'m': p})]))
    assert delta.drift is Drift.SAME


def test_model_no_source_mentioned_is_absent_and_never_deleted():
    """A drifted parser must not silently delete billing data."""
    book = _book('m', Price(input=1.0, output=6.0))
    deltas = diff_book(book, {})
    assert [d.drift for d in deltas] == [Drift.ABSENT]

    after = apply_deltas(book, deltas, updated_at='2026-02-02')
    assert 'm' in after.models
    assert after.models['m'].tiers[STANDARD].input == 1.0


def test_apply_writes_new_and_changed_only():
    book = _book('old', Price(input=1.0, output=2.0))
    verdicts = reconcile(
        [
            _res(
                'openai',
                {
                    'old': Price(input=9.0, output=9.0),
                    'fresh': Price(input=1.0, output=1.0),
                },
            ),
        ]
    )
    after = apply_deltas(book, diff_book(book, verdicts), updated_at='2026-02-02')
    assert after.updated_at == '2026-02-02'
    assert after.models['old'].tiers[STANDARD].input == 9.0
    assert after.models['fresh'].vendor == 'openai'
    assert after.models['fresh'].sources == ['openai']


def test_two_aggregators_agreeing_still_do_not_corroborate():
    """OpenRouter and the feed can echo the same stale or routing-margin price.

    Agreement between two non-primary documents is not vendor confirmation —
    only a first-party page unlocks a write.
    """
    p = Price(input=10.0, output=40.0)
    verdicts = reconcile(
        [_res('llm-prices.com', {'o3': p}), _res('openrouter', {'o3': p})]
    )
    assert verdicts['o3'].agreement is Agreement.AGREE
    deltas = diff_book(PriceBook(updated_at='2026-01-01'), verdicts)
    assert [is_corroborated(d) for d in deltas] == [False]


# --------------------------------------------------------------------------- #
# Time-of-day variant. Cross-source comparison is lenient (an aggregator
# without a peak field is not disagreeing); the book diff is strict (a vendor
# adding or moving the window is a fact change that must reach the book).
# --------------------------------------------------------------------------- #
def _peak_price(input_: float, window: str = '01:00-04:00') -> Price:
    start, end = window.split('-')
    return Price(
        input=input_,
        output=input_,
        peak=Price(input=input_ * 2, output=input_ * 2),
        peak_windows=[TimeWindow(start=start, end=end)],
    )


def test_vendor_peak_vs_aggregator_without_peak_is_agreement_not_conflict():
    verdicts = reconcile(
        [
            _res('deepseek', {'d': _peak_price(0.22)}),
            _res('openrouter', {'d': Price(input=0.22, output=0.22)}),
        ]
    )
    assert verdicts['d'].agreement is Agreement.AGREE


def test_book_without_peak_vs_vendor_with_peak_is_changed():
    """The vendor added a peak policy. The book must learn about it even
    though the scalar (off-peak) rate did not move."""
    book = _book('d', Price(input=0.22, output=0.22))
    verdicts = reconcile([_res('deepseek', {'d': _peak_price(0.22)})])
    (delta,) = diff_book(book, verdicts)
    assert delta.drift is Drift.CHANGED


def test_window_move_is_changed_even_if_rates_are_identical():
    book = _book('d', _peak_price(0.22))
    verdicts = reconcile(
        [_res('deepseek', {'d': _peak_price(0.22, window='02:00-05:00')})]
    )
    (delta,) = diff_book(book, verdicts)
    assert delta.drift is Drift.CHANGED


def test_identical_peak_policy_is_not_drift():
    p = _peak_price(0.22)
    book = _book('d', p)
    (delta,) = diff_book(book, reconcile([_res('deepseek', {'d': p})]))
    assert delta.drift is Drift.SAME


# --------------------------------------------------------------------------- #
# Rate history: a refresh must not overwrite what a call used to cost.
# --------------------------------------------------------------------------- #


def test_changed_rate_pushes_the_old_row_into_history():
    book = _book('old', Price(input=1.0, output=2.0))
    verdicts = reconcile([_res('openai', {'old': Price(input=9.0, output=9.0)})])
    after = apply_deltas(book, diff_book(book, verdicts), updated_at='2026-02-02')

    entry = after.models['old']
    assert entry.tiers[STANDARD].input == 9.0
    assert entry.tiers[STANDARD].effective_from == '2026-02-02'
    assert [p.input for p in entry.history[STANDARD]] == [1.0]
    # The superseded row keeps whatever stamp it had — None here, because it
    # predates history tracking, which sorts it first in rate_history().
    assert entry.history[STANDARD][0].effective_from is None
    assert [p.input for p in entry.rate_history()] == [1.0, 9.0]


def test_a_new_model_is_stamped_but_gets_no_history():
    book = _book('old', Price(input=1.0, output=2.0))
    verdicts = reconcile([_res('openai', {'fresh': Price(input=3.0, output=4.0)})])
    after = apply_deltas(book, diff_book(book, verdicts), updated_at='2026-02-02')

    entry = after.models['fresh']
    assert entry.tiers[STANDARD].effective_from == '2026-02-02'
    assert entry.history == {}


def test_restamping_the_same_rate_does_not_duplicate_history():
    """Idempotence: a refresh that reconfirms today's price must not grow the
    history by a row every time it runs."""
    book = _book('m', Price(input=1.0, output=2.0))
    verdicts = reconcile([_res('openai', {'m': Price(input=9.0, output=9.0)})])
    once = apply_deltas(book, diff_book(book, verdicts), updated_at='2026-02-02')
    twice = apply_deltas(once, diff_book(once, verdicts), updated_at='2026-02-03')

    assert [p.input for p in twice.models['m'].history[STANDARD]] == [1.0]
    assert twice.models['m'].tiers[STANDARD].effective_from == '2026-02-02'


def test_history_accumulates_across_successive_cuts():
    book = _book('m', Price(input=5.0, output=30.0))
    after = book
    for date, rate in (('2026-02-02', 3.0), ('2026-03-03', 1.0)):
        verdicts = reconcile([_res('openai', {'m': Price(input=rate, output=rate * 6)})])
        after = apply_deltas(after, diff_book(after, verdicts), updated_at=date)

    assert [p.input for p in after.models['m'].rate_history()] == [5.0, 3.0, 1.0]
    assert [p.effective_from for p in after.models['m'].rate_history()] == [
        None,
        '2026-02-02',
        '2026-03-03',
    ]
