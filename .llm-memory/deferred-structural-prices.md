# Deferred: structural price history, off-peak windows, context tiers
tags = #architecture-decision-record #deferral #schema #pricing

A deep-research report (bebri-chat `tmp/llm-price-scraper-research.md`,
2026-08-01, surveying pydantic genai-prices / LiteLLM / SimonW / Helicone)
was evaluated against this tool. Three of its schema ideas were deliberately
deferred, each with an explicit trigger for when to build it:

- **`ConditionalPrice` with `start_date`** (genai-prices' price-history
  mechanism). Our equivalent today: the dated fact lives in the row's `note`
  string (claude-sonnet-5 "RISES to 3.0/15.0 on 2026-09-01"), git log of
  `prices.json` is the history, and the daily check catches a flip within 24h
  because the vendor page changes. Trigger: the day a consumer needs to price
  a *past* call at the rate in force *then*.
- **Time-of-day windows** (DeepSeek's announced 2x peak pricing, encoded in
  genai-prices as the off-peak default + a time-constrained standard price).
  `Price` is scalar and cannot express it. Trigger: DeepSeek actually
  activates the policy — build together with `ConditionalPrice` above.
- **Context-length tier rows** (Gemini >200k cliff, OpenAI long-context
  columns — `OpenAISource` deliberately records the short-context cells).
  `ModelEntry.tiers` already has the axis; adding a `long_context` key is
  additive, no schema break. Trigger: first real >200k usage on a tiered
  model in a consuming app.

## Report claims rejected outright

- **"Seed from LiteLLM, ~1k models free"** — contradicts the report's own
  findings (LiteLLM has "no provenance"; "auto-merge is explicitly
  forbidden"). Seeding IS a bulk auto-merge from the least-provenanced
  source; 80 corroborated rows beat 1,300 unverified ones.
- **"OpenAI needs Playwright (tier 3)"** — false; the research agent never
  sent an `Accept` header. `developers.openai.com/api/docs/pricing` serves
  19KB of markdown covering every family, including the o-series the
  client-rendered HTML hides. Empirical contact beats survey here.
- **5% comparison tolerance** — every real conflict observed was 5x-50x;
  `EPSILON = 1e-9` plus Decimal-exact conversion at the OpenRouter edge
  ([[openrouter-aggregator-status]]) makes a tolerance knob unnecessary.

What WAS adopted from the report: the OpenRouter source, raw fetch snapshots,
and the write-time sanity gate (negative rejected in `Price`, >$1000/M refused
in `save_book` — a per-token/per-1M unit bug is a factor of exactly 1e6).
