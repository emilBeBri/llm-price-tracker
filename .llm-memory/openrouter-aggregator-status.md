# OpenRouter widens conflict detection, never write authority
tags = #architecture-decision-record #sources #openrouter #corroboration

`OpenRouterSource` (2026-08-01) is in `AGGREGATOR_SOURCES`, so a model only it
and/or the llm-prices feed know stays uncorroborated and `refresh` refuses to
write it. Two reasons it is not a vendor source, both from the deep-research
report that prompted it: it prices **its own routing** (observed live:
gpt-5.6-luna/terra routed at exactly half OpenAI's list price), and its ids are
`author/model` slugs, not vendor wire ids. Its value is breadth of *detection*:
on its first run it agreed with OpenAI's page on `o3` at 2/8, isolating the
llm-prices feed's stale 10/40 as the outlier — two independent sources ganging
up on the bad row is the corroboration model working.

## Parser edges that were learned, not designed

- **`-1` is a sentinel, not a price.** OpenRouter's own meta-routers
  (`openrouter/auto`, `fusion`, `pareto-code`, …) publish `"-1"` meaning
  "varies by routed model". Five such rows took the whole source down on the
  first live run because `Price` rejects negatives (`ge=0`); the fix maps
  negative → `None` in `_per_token_to_mtok`. Diagnosed from the raw snapshot —
  the snapshot feature paid for itself within minutes of existing.
- **Per-token decimal strings scale in `Decimal`, not float**, so the closest
  float to the printed decimal value reaches the book with no intermediate
  binary rounding. `compare.EPSILON` would absorb float dust anyway; this keeps
  dust out of committed diffs.
- **`:free`/`:thinking`/`:extended` variants and 0/0 promo rows are dropped**
  at the parse edge: they are OpenRouter routing products, and one exact-matching
  a real vendor id would render as a fake conflict.
- **Author-prefix collisions keep the first row seen** (`setdefault`), so one
  author's input price can never pair with another's output.

## Conflict display split (main.py check)

Adding a second aggregator moved conflicts 3 → 12; the six new ones were
aggregator-vs-aggregator rows about models no vendor page mentions — permanent
by nature (routing vs list price). Only conflicts a primary document
participates in (`Verdict.vendor_value is not None`) get the full table;
aggregator-only ones collapse to a one-line dim count. Rationale: a table that
shows the same benign rows every day trains you to ignore the day a real one
appears — the same reasoning as the check/refresh filter symmetry.

## Moonshot and Z.ai confirmed the boundary

Adding first-party Moonshot and Z.ai sources on 2026-08-10 produced exactly the
case this design exists for: Kimi K3's international vendor rate is
$3/$15/$0.30 cached while OpenRouter advertises a lower routing rate, and Z.ai
publishes GLM-5.2 at $1.40/$4.40/$0.26 cached while OpenRouter can route it much
more cheaply. These are legitimate reseller prices, not corrections to the
vendor facts. The vendor source therefore wins the committed row and OpenRouter
remains disagreement evidence.

Moonshot has a second trap: its Chinese platform publishes CNY prices, but the
book's unit is USD. `MoonshotSource` reads the international Kimi announcement's
USD table and never converts the CNY page through a live exchange rate; doing so
would turn a recorded vendor fact into a time-dependent derived estimate.

See [[deferred-structural-prices]] for what the same research report proposed
that was deliberately NOT adopted.
