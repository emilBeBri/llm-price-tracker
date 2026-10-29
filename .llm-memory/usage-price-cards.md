# Usage price cards
tags = #architecture-decision-record #sources #pricing

`PriceBook.usage_prices` records image-modality and hosted-service prices
separately from chat `models`. Each `UsagePrice` has a source URL, vendor,
explicit `UnitPrice` amounts/denominators/units, first-observed date and
superseded history. `get_usage_price` is an offline exact lookup. Consumers
map deployment aliases onto these keys; the tracker never stores app aliases.

This is the consumer trigger anticipated by
[[image-models-record-the-image-modality-row]]: the old chat-shaped image
rows remain unchanged, while dedicated cards preserve text and image rates
without blending modalities or reading a per-image equivalent as a token rate.
`sources/usage.py` extracts the OpenAI Standard modality table, Google's
multiple token rates per cell, and hosted search/Maps fees with their units.
Only first-party sources authorize these cards. Refresh/check include their
changes; missing source rows never delete existing data.

`AzureImageSource` reads Microsoft's `span.price-data[data-amount]` JSON.
The visible page says `$-`; that is a UI placeholder, not missing data.
Global image prices are accepted only when all published available regions
agree and the table declares 1M tokens. Divergent regional rates fail rather
than selecting a region by guess. Azure cards are distinct from public
OpenAI cards even when their amounts match.

`Source.expect_usage` prevents a healthy token parse from hiding a broken
fee parse. The first live run exposed `$10 USD per 1,000 searches` on
Anthropic, where the old wording lacked `USD`; the unit parser accepts both.

# sources

../src/llm_price_tracker/models.py
../src/llm_price_tracker/book.py
../src/llm_price_tracker/sources/usage.py
../src/llm_price_tracker/compare.py
https://azure.microsoft.com/en-us/pricing/details/azure-openai/
Tracker is the only source of image and hosted-service prices — uid: a98d7d2a-ad9f-4cff-bafe-ca53e8fd8346
