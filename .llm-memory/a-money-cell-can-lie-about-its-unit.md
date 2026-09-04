# A money cell can lie about its unit, and about being the price

tags = #gotcha #sources #parsing #pricing #google #zai

`dollars()` takes the **first** `$` amount in a cell. That heuristic is right
often enough to be dangerous, and two cases found on 2026-09-04 show both ways
it fails. Neither is caught by any guard the book has: `Price` rejects
negatives, `save_book` refuses anything above $1,000/M because a per-token vs
per-1M mix-up is a factor of exactly 1e6 (see [[deferred-structural-prices]]),
and both of these errors land comfortably inside the legal range.

## 1. The first amount is the price the vendor is NOT charging

z.ai renders a promoted model's list price struck through, ahead of the
effective one:

    | GLM-5.3-Flash | ~~\$0.15~~ \$0.075 | ~~\$0.03~~ \$0.015 | ... | ~~\$0.50~~ \$0.25 |

Read first-amount-first, that records $0.15/$0.50 for a model z.ai bills at
$0.075/$0.25 — every estimate 2x high. `ZaiSource._money` strips `~~…~~`
before the amount is read.

**The rule this instance serves: the book records the rate in force, not the
list rate.** It is the same call `AnthropicSource` makes by reading the
pricing page rather than the models overview — the overview shows Sonnet 5 at
3/15 while Anthropic charges 2/10 under a dated introductory rate. A
struck-through figure and a superseded overview figure are the same lie.

The stripping lives in the **source**, not in `dollars()`: its docstring
records that the effective price is not always the second amount, so a shared
"skip the first" rule would break another vendor.

Consequence to expect: when a promotion ends the rate **rises**, and `check`
reports it as drift. GLM-5.3-Flash's ends 2026-09-09 (24:00 UTC+8), so a
0.075 -> 0.15 delta then is the vendor honouring its own footnote, not a
parser fault. A `note` on the row would say so — but per
[[deferred-structural-prices]], `apply_deltas` only carries a note on a row
that already exists, so it can be added after the first refresh lands it.

## 2. The amount is not priced per token at all

Google's pricing tables are headed *"Paid Tier, per 1M tokens in USD"*, and an
individual cell is free to contradict that. Image models carry both units in
one cell —

    $3 (text and thinking)$60.00 (images)Equivalent to $0.045 per 0.5K image*

— where the first amount is the token rate, so first-amount-first is correct.
But `gemini-2.5-flash-image` publishes **no** token rate:

    Output price | Not available | $0.039 per image*

That went into the book as `output: 0.039` per 1M tokens. The true text-output
rate is **$2.50/M**: OpenRouter's `completion` for the model, corroborated
arithmetically by its `image_output` of $30/M, which reproduces Google's
$0.039 at Google's documented 1290 tokens per image. So the book undercharged
that row by 64x from 2026-08-01 to 2026-09-04, and nothing could have noticed
— an error of 64x is invisible to a ceiling built for 1e6.

`GoogleSource._token_rate` now disqualifies an amount whose **trailing**
context is a different unit (`per image`, `per video`, `per minute`, `/ use`,
…). Trailing, not anywhere in the cell: the cache cell reads
`$0.15 $1.00 / 1,000,000 tokens per hour`, where the first figure is a token
rate and the hourly one is storage. No output price means no model, so the row
is reported absent rather than recorded wrong — [[wrong-document-200]]'s
lesson that a parser refusing to guess is itself the guardrail.

## The book row was corrected by hand

`gemini-2.5-flash-image` now reads `0.3 / 2.5`, sourced `openrouter`, with a
`note` saying the output figure is the text rate and that Google publishes
only $0.039/image. Three deliberate choices:

- **Output is the TEXT rate.** The book has one output field and the existing
  image rows already hold text output (`gemini-3-pro-image` at 12.0, whose
  image output is 120.0). Consistency beats picking the modality a given
  reader probably meant.
- **No history row.** Pushing 0.039 onto `history` would assert Google once
  charged it ([[rate-history-effective-from]] makes that a retrievable claim).
  It never did — that number was ours, not theirs — so `effective_from` stays
  2026-08-01 and the wrong value leaves no trace outside git.
- **`refresh` cannot self-heal this one.** With the cell disqualified, only
  OpenRouter names the model, and an aggregator-only row is refused a write by
  design. A hand-edit was the only route, and the daily run stays quiet
  because the book now agrees with the one source that has it.

See [[parse-the-table-not-the-heading]] for the other half of the same day's
z.ai work.

# sources

session 2026-09-04; snapshots
`$XDG_CACHE_HOME/llm-price-tracker/raw/2026-09-04/{zai.md,google.html,openrouter.json}`.
