# Announced rates are dated facts, not projections
tags = #architecture-decision-record #pricing #schema #parsing #alerting

Built 2026-10-19, after a live `check --all` showed zero drift while Google's
whole pricing page read `$2.00 through November 30, 2026. $4.00 starting
December 1, 2026` on every row. `GoogleSource._token_rate` takes the FIRST
amount — correct while the promo runs — and dropped the second, so the book
recorded a six-week discount as a standing rate and would have had nothing to
say on 2026-12-01 when eleven Gemini models doubled at once, plus the 2.5/TTS
family on 2027-01-01.

The two numbers are not the same kind of thing, which is why neither "just take
the last amount" nor "record only today" was right:

- the first amount is the rate **in force**, and `tiers` keeps meaning exactly
  that, so an `at=None` caller is never charged tomorrow's price today;
- the second is a **dated vendor statement** about the future, and is recorded
  as such: `Price.scheduled: list[Price]`, each row carrying the published date
  in its own `effective_from`.

`ModelEntry.rate_history()` merges `scheduled` into the sort, so `price_at()`
and `show --at 2026-12-01` answer correctly the day the vendor publishes,
with no new lookup machinery. `GoogleSource` reads the in-cell promo pattern;
`AnthropicSource` had the same shape as two separate rows (`[through August 31,
2026]` and `starting September 1, 2026`) and used to throw the second away.

## Recomputed every refresh, never carried forward

An announcement is a live claim by a page, not a historical fact, so
`apply_deltas` overwrites it from each parse instead of accumulating it. The
trigger that proves it: Anthropic's pricing page now says Sonnet 5's scheduled
September 1 increase **"will not occur"** — the $2/$10 intro rate became the
standard price. A sticky scheduled row would have predicted a phantom doubling
forever. History rows are stripped of `scheduled` for the same reason
(`compare._plain`).

## ANNOUNCED is a drift class of its own

`Drift.ANNOUNCED` = same rates, different announcement. It is written by
`refresh` and printed by `check`, and deliberately does **not** exit 1 or
notify: the rate in force has not moved, and a watched model exiting 1 every
day for six weeks because Google dated its promo is how you learn to dismiss
the one that matters ([[alert-tiers-are-not-data-tiers]]). Making it CHANGED
would have done exactly that.

## Arrival: promotion, and the date is the vendor's

`compare._promote_arrived` runs over EVERY model after the deltas, not just the
changed ones — because a page that keeps publishing a `through <date>` clause
after it has passed parses to the row already in the book, so no delta fires at
all and nothing would ever move it. The promoted row keeps the **published**
start date; this is the one place in the book where `effective_from` is a
vendor date rather than a first-observed one (see
[[rate-history-effective-from]]). `price_at` was already correct in the interim
— the row is dated — so promotion is the `at=None` half. `check` prints a
one-line "now in force, run `refresh --write`" reminder for the window between
the two.

## Side effect: the book diff became strict about cache rates

Folding the announcement rewrote two Gemini TTS rows whose page had lost its
context-caching row entirely, which surfaced an inconsistency: `_same` reused
`_agree`, and `_agree` is lenient about a field a source does not publish
(correct across sources — an aggregator has no cache column). In a BOOK diff
that leniency is wrong: keeping a retired cache rate silently charges a rate
nobody publishes any more, and it is *higher* than the input-rate fallback a
missing cache rate gets. `_same` is now `_rate_eq`-strict on the four rates and
is the single history-push test (`_same_rates` is gone). `_drift_table` gained
`_cache_note` so a cache-only change renders `·cache $0.125→—` instead of
`same -> same`.

The write gate covers announced rows too (`book._implausible_rates`): they are
read from the same cell and stored the same way, so the factor-1e6 bug would
otherwise walk in through a future date.

# sources

conversation 2026-10-19 — "get gemini 4 argon in there, and others too if" — uid: 27a2f458c774

# sources

agent capture (in-session)
