# A renamed vendor column deletes a published rate
tags = #gotcha #parsing #schema

OpenAI renamed `cache writes` to a per-TTL pair (`30m write` /
`12h write`) some time before 2026-10-31. The parser's needle stopped matching,
`cache_write` came back `None`, and `None` in this schema means "the vendor
publishes no such rate" — so the book recorded a false statement about eight
models for nine days, and the history push archived the rows that had the right
answer. Nothing alerted: the diff *did* say `cache-write 0.125→—`, and it was
read as the vendor withdrawing the column.

Both consumers noticed and worked around it by hand (bebri-chat added a
1.25x-input multiplier fallback and a test pinning the loss), which is the
failure this tool exists to prevent, reconstructed downstream.

Recovered 2026-11-09: the 30m column is the same rate, so the repair restored
every value and the multiplier path and the published path now agree.

What would have caught it: a source-level assertion that a table still carries
a column for each field it used to publish — the equivalent of `expect` for
columns rather than for model ids. Not built; recorded as the trigger for when
the next rename happens.

Second, unrelated lesson from the same refresh: consumer tests that pin values
read out of this book are the cheapest alarm that a book change reached
downstream. Four of bebri-chat's went red on the cache-write repair and one on
an unrelated regolo price move, and every one of them was telling the truth.

# sources

conversation 2026-11-09 — OpenAI TTL column repair and the Anthropic band — uid: 4f46da7e8930
