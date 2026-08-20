# A 200 can serve the wrong document, and the id guardrail won't see it

tags = #gotcha #sources #guardrails #deepseek

2026-08-20: `deepseek` reported BROKEN. The cause was not the parser, which was
correct and untouched since the peak-window work two days earlier. It was the
URL.

`https://api-docs.deepseek.com/quick_start/pricing` (no trailing slash) began
serving **'Your First API Call'** — a completely different page. Status 200, no
redirect, `text/html`, 45KB, containing one syntactically perfect `<table>` of
`base_url` and `api_key` rows. The slashed form,
`…/quick_start/pricing/`, serves 'Models & Pricing' as it always did. Nothing
else about the request changed; a browser User-Agent made no difference. The
trailing slash on `DeepSeekSource.url` is therefore load-bearing and a test
pins it, because it reads exactly like the kind of thing a tidy-up deletes.

## Why `expect` would not have caught this

`Source.expect` checks that known model-id fragments survive the parse. The
wrong page **contained both model ids** — `deepseek-v4-flash` and
`deepseek-v4-pro` sit in its `model` row, as the ids you pass to the API. So an
id-presence guard run against that document passes.

The only thing that stood between a swapped document and a silent failure was
`parse` finding no `MODEL` header row and returning `{}`, which
`Source.fetch` turns into `fetched, but parsed 0 models — page structure
changed`. Generalisation worth keeping: **a parser that refuses to guess is
itself the guardrail.** Any "helpful" fallback — first table on the page, first
numeric column, nearest heading — converts this failure from loud to silent.

## Cost of the outage: detection, not accuracy

The book's DeepSeek rows already matched the live page exactly, so no price was
missed. What was lost was the ability to *notice*: for two days DeepSeek's rows
were single-sourced from the aggregators, and OpenRouter's routing price for
them is legitimately lower than the vendor's. Had DeepSeek moved a rate in that
window, the disagreement would have read as the usual routing-vs-list noise
described in [[openrouter-aggregator-status]].

The daily timer would have surfaced it as exit 2 — which is why exit 2 outranks
the watch list unconditionally in [[alert-tiers-are-not-data-tiers]].
