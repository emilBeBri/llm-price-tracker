# Parse the table, not the heading it sits under

tags = #gotcha #sources #parsing #zai #architecture-decision-record

2026-09-04: `zai` reported BROKEN — `parsed 10 rows but none match
['glm-5.2']`. The parser was fine and the page was fine. What changed is that
z.ai added a **`### Latest Models`** table above `### Text Models` and moved
GLM-5.3, GLM-5.3-Flash and GLM-5.2 into it. `ZaiSource` anchored on the
`### Text Models` heading, so it went on parsing ten rows of superseded models
and returned not one current flagship.

The failure shape is worth naming: **the parse did not break, its subject
moved.** Ten plausible rows came back, every price in them correct, and only
the `expect` tripwire — pinned then at `glm-5.2` — noticed that the models
anyone bills against had gone missing. A parse that returns *something* is the
hardest kind to catch, which is the same lesson as [[wrong-document-200]] from
the other direction: there a valid document was the wrong one; here a valid
table was the wrong table.

## The fix: qualify tables by shape

`ZaiSource` now reads **every** table on the page whose header carries model,
input and output columns under a 'per 1M tokens' caption (`_pipe_tables`
yields each markdown table with the prose line above it). A section the vendor
adds tomorrow lands on the day it appears; the per-image, per-video and
per-use tables further down are rejected on shape, not on a list of section
names to skip. A model listed twice keeps its first appearance — the page
leads with the current table.

That took the source from 10 models to 17: the three flagships plus the four
vision rows (`glm-4.6v`, `glm-4.6v-flashx`, `glm-4.5v`, `glm-ocr`) that the
Text-Models anchor had never been able to see. They are token-priced vendor
facts like everything else in the book, so they are recorded.

## Not a rule against heading anchors

`OpenAISource` anchors on `### Standard pricing data` and must keep doing so.
The distinction is what the heading is *doing*:

- z.ai's headings **group models** — every table under them is the same kind
  of fact, so the heading carries no information the table header lacks, and
  binding to it buys nothing while adding a way to miss a section.
- OpenAI's heading **disambiguates tiers** — Standard, Batch, Flex and Fast
  tables are structurally identical, so shape cannot tell them apart and
  dropping the anchor would silently swap in batch prices.

Anchor on a heading only when shape is ambiguous. Never to find the models.

## A third case: a heading that does neither (2026-09-09)

OpenAI's image, cyber, realtime/audio, video and fine-tuning tables all sit
under the *same* heading, `### Grouped Pricing Table data` — a component render
artefact, not an editorial choice. It groups nothing and disambiguates nothing,
so it is useless in both directions: as an anchor it finds whichever table
comes first (the cyber one), and shape cannot separate the image section's
Standard table from its Batch table because they are identical.

What actually disambiguates there is a bare prose line — the section title
`Image generation models`, then the tier tab `Standard` — so
`_parse_image_models` anchors on those and **refuses outright** if another tier
label (`Batch`, `Flex`, `Fast`, `Priority`) turns up before the table. That
refusal is the point: the alternative to a wrong anchor is no rows, never
best-effort rows. The standard-table parser was left byte-identical rather than
generalised into a shared helper, because the one thing a shared "find the next
table" helper would buy is the ability for one table's absence to silently pick
up another table's rows.

## `expect` was retuned, deliberately

`expect` went from `('glm-5.2',)` to `('glm-5',)`, the family-level form the
other sources use. The version-pinned fragment is what caught this, so
loosening it looks like removing the smoke detector — but it was only ever a
proxy for "did we miss a section", and shape-based parsing removes that
failure at the source. Left pinned, it would fire falsely on the day z.ai
retires GLM-5.2 from the page, and a guard that cries wolf is
[[alert-tiers-are-not-data-tiers]] all over again.

## A fourth case: one page, two table shapes (2026-10-19)

Moonshot rewrote its pricing page on 2026-10-16 and `check` reported
`parsed 3 rows but none match ['kimi-k3']` — the same shape as the z.ai break:
plausible rows, correct prices, the flagship missing. This time the subject
did not move; the **schema did**. K3 got its own table:

```
Model | Unit | Cache Write (5min) | Cache Write (1h) | Cached Input | Input | Output | Context
```

while the K2 tables below it kept the older split-by-cache-outcome columns
(`Input Price (Cache Hit)` / `(Cache Miss)`). `MoonshotSource` only knew the
second shape, so it qualified the K2 table, parsed three real rows, and
skipped the flagship. `expect` caught it — which is the argument for pinning
the fragment on the current flagship even though a pinned fragment cries wolf
when that model retires.

Two lessons the shape rule did not already carry:

- **A source may have to know every generation's schema at once.** "Qualify
  tables by shape" silently means "…by the shape you were written for", and a
  vendor migrating one product line does not migrate the rest. Both are read
  now, and a new `_col(header, needle, without=...)` exists because the K3
  header puts `Cached Input Price` immediately before `Input Price`: a plain
  substring search matches the cached column first and records every fresh
  token at the cache-hit rate, a silent 10x under-billing that no guardrail
  sees. The exclusion is the load-bearing part, not the extra shape.
- **A second shape brings new facts, not just the old ones back.** K3
  publishes cache-write tiers, which the book had no value for; the 5-minute
  tier is recorded as `cache_write` because the page says that is what applies
  when no TTL is requested. The 1h tier has no field and is not guessed at.

See [[a-money-cell-can-lie-about-its-unit]] for the other z.ai trap found the
same day (the struck-through promo price), and for Google's version of "the
cell is not what the header says it is".

# sources

session 2026-09-04; snapshot
`$XDG_CACHE_HOME/llm-price-tracker/raw/2026-09-04/zai.md`.
