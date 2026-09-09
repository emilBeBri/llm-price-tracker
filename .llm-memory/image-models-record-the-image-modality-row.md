# An image model publishes two rates; the book records the image one

tags = #architecture-decision-record #sources #parsing #openai #pricing

2026-09-09: OpenAI shipped `gpt-image-2.5-sunburst` and `gpt-image-2.5-flare`.
Neither appeared in the book, and neither had for the whole `gpt-image` line —
`OpenAISource` read only `### Standard pricing data`, and the image models are
in a separate section the parser never looked at. See
[[parse-the-table-not-the-heading]] for how that section is anchored.

The decision worth recording is not the anchor, it is **which row**. Every
image model occupies two rows in that table, one per modality:

```
| gpt-image-2.5-sunburst | Image | $8.00 | $2.00 | $30.00 |
| gpt-image-2.5-sunburst | Text  | $5.00 | $1.25 | -      |
```

Three readings were available and only one is a vendor fact:

1. **The Image row, as published** — 8.00 / 2.00 / 30.00. What we record.
2. The Text row — no output price on five of the seven models (they emit no
   text at all), so it cannot stand alone.
3. Text input paired with image output — 5.00 / 30.00. This is the *most
   accurate description of a text-prompt generation call*, which is exactly
   why it is wrong here. `models.py` states the contract: the book holds "what
   a vendor charges, as published… deliberately free of any consuming app's
   billing choices". Which modality a caller sends is such a choice. A blended
   row would be a number no vendor page contains, and nothing downstream could
   tell it apart from one that does.

The cost of choice 1 is that a text-prompt call over-states input by 60%
(8.00 vs 5.00) — on a token count two orders of magnitude below the image
output that dominates the bill. The per-model text rate is carried in each
row's `note` so the figure is not lost, following the note convention in
[[deferred-structural-prices]].

**Precedent this does NOT follow.** The Gemini image rows
(`gemini-3.1-flash-image` and siblings) record the *text* token rate and drop
Google's `$60.00 (images)` output rate entirely — `GoogleSource` takes the
first amount in a cell and that is the text one. So the book now holds image
models priced on two different conventions. That is a known inconsistency, not
an oversight: fixing it means teaching `GoogleSource` to split a
multi-unit cell, which is the same job as
[[a-money-cell-can-lie-about-its-unit]] and has no consumer asking for it yet.
Trigger to revisit: the first time a caller prices Gemini image generation and
gets a bill 20x its estimate.

`gpt-image-2.5-*` is watched (`watch.toml` `ids`) because the `gpt` family
rule reads its `2.5` as a text-line version and files a current-generation
model four versions stale — see [[alert-tiers-are-not-data-tiers]].

# sources

session 2026-09-09; `developers.openai.com/api/docs/pricing`, section
"Image generation models".
