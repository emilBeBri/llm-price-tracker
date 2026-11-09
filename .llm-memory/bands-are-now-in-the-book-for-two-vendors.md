# Bands are in the book for Anthropic and OpenAI, not yet for consumers
tags = #schema #pricing #consumers

`Price.long_context` + `long_context_above` (strictly above, first-observed
dating) now carry the prompt-size surcharge for two vendors: Anthropic from the
second published row (`claude-haiku-5.5`, >100,000, 0.50/2.50) and OpenAI from
the Long-context columns of the same table (twelve models, >272,000; astra
20/75, cache 2/1M read and 25/M write). Google's 200k cliff came in with the
MDX rewrite (gemini-2.5-pro, gemini-3.1-pro-preview).

What it is worth: priced against the book alone, a real 74-call session costs
$37.60 at standard rates against $23.81 without the band — the cliff is 37% of
the bill, and 31 of its 74 calls crossed the line. A flat rate table does not
answer "what did this cost".

Two things it does NOT do. The book records the **standard** tier only; flex is
not ingested at all, so a flex-priced session is still priced by bebri-chat's
hand-typed override. And bebri-chat's `_spec_from_book_price` does not copy
`long_context`, so the band is recorded and inert downstream — an `above + 1`
adapter mapping was agreed as the phase-2 design and has never shipped. Bands
in the book buy drift detection and a corrected standard tier; they do not yet
change a consumer's bill.

First-observed dating means enrichment archives the bandless predecessor: a
`--at` before the enrichment date still prices a large prompt at the cheap
rate. That is pinned by a test so it cannot be "fixed" with an invented start
date.

# sources

conversation 2026-11-09 — OpenAI 272k band, verification against the 126 dkk session — uid: 4f46da7e8930
