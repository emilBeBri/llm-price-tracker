# Consumers bind the package, not a JSON export
tags = #convention #schema #delivery

bebri-chat (`src/bebri_chat/config/llm_models.py`) and gllm
(`src/gllm/pricing.py`) both `from llm_price_tracker import load_book` and read
`ModelEntry`/`Price` objects directly. There is no JSON export step and no
schema_version negotiation — so a NEW field on `Price` or `ModelEntry` is inert
for both until they opt in, and a field REMOVED or renamed breaks them at import
time, not at parse time. Verify format changes by running their suites, not by
reading a file.

The fragile surface is not our model, it is the vocabulary across the seam:

- our field is `cache_write`; bebri-chat's local delta rows spell the same
  concept `cache_creation` (`PriceSpec.cache_creation`), and its
  `_spec_from_json_row` treats an ABSENT key as "inherit from the book" while an
  explicit `null` means "vendor publishes no such rate". A delta row that writes
  `cache_write` is silently ignored — it inherits, it does not override.
- gllm's `data/prices.json` is overrides only, and a registry test forbids a
  bundled override row for a model the book already covers ("the stale-copy
  trap"). Adding a model to the book can therefore turn a green gllm test red,
  which is the mechanism working, not a regression.

Both consumers keep *derived* things on their own side on purpose (bebri-chat
turns a null cache read into the full input rate for gpt-5.5-pro; gllm reports
`price_window` so a 2x peak swing reads as policy). That is the same boundary
[[deferred-structural-prices]] draws: the book records vendor facts, consumers
apply multipliers.

# sources

conversation 2026-11-09 — evaluating git-clones/106-llm-pricing against our consumers — uid: 4f46da7e8930
