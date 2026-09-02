# Rate history: `effective_from` + superseded rows, so a past call keeps its price

tags = #architecture-decision-record #pricing #schema #billing

Built 2026-09-02. Until then the book held only *current* rates, so any
consumer that stored token counts and derived cost on read was silently
repriced by every refresh. The trigger recorded in
[[deferred-structural-prices]] fired the same day: OpenAI cut `gpt-5.6-sol`
5.0/30.0 -> 4.0/20.0, and bebri-chat — whose `usage` table is tokens +
timestamp with **no cost column** — began under-reporting every historical sol
call by a third. gllm was unaffected: it snapshots `cost_usd` into
`calls.jsonl` at call time, which is the other valid answer to this problem.

## The shape, and why not genai-prices'

The obvious port is `tiers: dict[str, list[ConditionalPrice]]` — every rate a
dated row in one list. Rejected: it changes the type of `tiers`, so every
existing reader breaks, and it moves the current price behind an index lookup
in a file whose entire job is to be read by a human in a diff.

Shipped instead:

- `Price.effective_from: str | None` — ISO date.
- `ModelEntry.tiers` — unchanged, still the rate in force **now**.
- `ModelEntry.history: dict[str, list[Price]]` — only *superseded* rows.
- `ModelEntry.rate_history(tier)` / `price_at(at, tier)` for the walk.

So every existing reader of `tiers` keeps working untouched, a book with no
history serialises byte-identically to before (`save_book` strips an empty
`history`), and the committed diff still shows the current price where it has
always been.

## Two axes, one `at`, and the order is load-bearing

`get_price(model, at=...)` now resolves **the calendar rate first, then that
rate's peak/off-peak window** ([[deferred-structural-prices]] built the
time-of-day axis in August). The order is not arbitrary: `peak_windows` is a
property *of* a rate, so resolving the window before the rate would bill a
July call against August's peak multiplier. A test pins exactly this
(`test_calendar_rate_is_chosen_before_the_peak_window`) because the bug it
prevents is invisible — both orders return a plausible number.

Corollary: `effective_from` on a nested `peak` variant is meaningless and
stays `None`. The parent row's date governs both.

## Dates are first-observed, not announced

A stamp is the date the book first *recorded* that rate — the commit that
introduced it. No source publishes reliable announcement dates, and a rate is
usually in force for some unknown span before we read it, so the stamp is a
**lower bound**. Everything present in the book's first commit (2026-08-01)
carries that date though most rates were older.

Hence `price_at` returns the **oldest known rate** for a query that predates
every row, rather than `None`. Refusing to price a call the app definitely
made is the worse failure, and the caller can always compare `at` against the
returned row's `effective_from` to tell a record from an extrapolation.

## Backfill

`scripts/backfill_price_history.py` (PEP 723, no deps) reconstructs history by
walking `git log` of `prices.json` and recording each distinct consecutive
rate with the date of the commit that introduced it. Idempotent — it
recomputes rather than appends. Deliberately a script, not a CLI subcommand:
the package is pure and offline by design and must never shell out to git.

The first run found four real changes in the book's four-commit history
(`deepseek-v4-flash`, `deepseek-v4-pro`, `gemini-3.6-flash`, `gpt-5.6-sol`)
and stamped 101 current rows. Consumer side: see bebri-chat's
`.llm-memory/historical-cost-uses-rate-in-force.md`.
