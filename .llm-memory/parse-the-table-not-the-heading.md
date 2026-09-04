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

## `expect` was retuned, deliberately

`expect` went from `('glm-5.2',)` to `('glm-5',)`, the family-level form the
other sources use. The version-pinned fragment is what caught this, so
loosening it looks like removing the smoke detector — but it was only ever a
proxy for "did we miss a section", and shape-based parsing removes that
failure at the source. Left pinned, it would fire falsely on the day z.ai
retires GLM-5.2 from the page, and a guard that cries wolf is
[[alert-tiers-are-not-data-tiers]] all over again.

See [[a-money-cell-can-lie-about-its-unit]] for the other z.ai trap found the
same day (the struck-through promo price), and for Google's version of "the
cell is not what the header says it is".

# sources

session 2026-09-04; snapshot
`$XDG_CACHE_HOME/llm-price-tracker/raw/2026-09-04/zai.md`.
