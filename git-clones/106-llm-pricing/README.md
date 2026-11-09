# 106 — What LLM pricing pages can't tell you

Published list prices for 13 models from Anthropic, OpenAI and Google, run
through four workload shapes.

## What's measured and what isn't

**Measured:** every price. Each figure in `prices.json` is transcribed from the
vendor's own public pricing page on the date recorded in that file. None of the
three publishes a pricing API, so the transcription is by hand — and every
number is checkable against the cited URL.

**Assumed:** the token counts in each workload. These are my estimates of what
an agentic loop, a RAG chat, a classification job and a long-document synthesis
look like. They are parameters at the top of `compute.py`. Change them and the
model gives you your answer instead of mine.

That makes this a **model, not a measurement**, and it is weaker than it would
be with real traffic logs. `sensitivity.py` exists because of that.

## Findings

**List prices have converged.** 11 of 13 models charge exactly 5.0x more for
output than input. Claude Sonnet 5.5, gpt-6.1-sol and Gemini 4 Argon all list
at $2.00 in / $10.00 out — identical, from three competitors.

**The bills have not.** On an agentic workload those three identical models
cost $112.36, $79.36 and $79.36 per 1,000 requests. The gap is a line item that
never appears in a headline comparison: Anthropic bills prompt-cache writes,
OpenAI and Google don't.

**For one pair, the question has no answer.** Claude Opus 5.5 and gpt-5.6-sol
both list at $4.00 / $20.00. Opus reads cache at half the price but pays to
write it. Which is cheaper **inverts** on cache rewrite frequency alone — 3.82x
worse when the cache expires every request, 0.88x (cheaper) when it rarely
does, crossing over around one rewrite per 32 requests.

**Context cliffs don't amortise.** Claude Haiku 5.5 and gpt-6-luna both list at
$0.10 / $0.50. Above 100k tokens every Haiku rate multiplies by 5, so on a
120k-token workload Haiku costs 6.8x more. No caching removes that.

**The ranking is stable — which I got wrong first time.** 28 model pairs swap
order across workloads. Against the 468 pairwise comparisons that actually
exist, that is 6%: the ranking holds 94% of the time. The exceptions cluster on
models with context cliffs — Claude Haiku 5.5 appears in 10 of the 28, more
than any other model.

**One with a date on it.** Gemini 4 Argon doubles on 2026-12-01, to $4.00 in /
$20.00 out. Gemini 3.8 Flash doubles on 2027-01-01. Both are on Google's page.

## Reproduce

```
python3 compute.py     prices.json costs.json
python3 sensitivity.py prices.json sensitivity.json
```

No dependencies beyond the standard library. `prices.json` carries the source
URL and retrieval date for every figure.

## Excluded

Batch, flex and priority tiers. Anthropic's longer cache TTL, which is priced
differently and would change the amortisation. Negotiated and enterprise rates.
Non-text modalities. All figures are standard-tier list prices.
