from llm_price_tracker.sources import SOURCES
from llm_price_tracker.sources.vendors import MistralSource


MISTRAL_MD = '''# API pricing

## Generalist models

| Model | API name | Price |
| --- | --- | --- |
| [Z.ai GLM 5.3](/models/zai-glm-5-3) | `zai-glm-5-3` | Input $1.4 /M Tokens<br>Cached input $0.14 /M Tokens<br>Output $4.4 /M Tokens |
| [Mistral Large 4](/models/mistral-large-4-0) | `mistral-large-4` | Input $0.7 /M Tokens<br>Cached input $0.07 /M Tokens<br>Output $2.1 /M Tokens |
| [Mistral Small 4](/models/mistral-small-4-0-26-03) | `mistral-small-2603` | Input $0.15 /M Tokens<br>Output $0.6 /M Tokens |

## OCR models

| Model | API name | Price |
| --- | --- | --- |
| [OCR 4.1](/models/ocr-4-1) | `mistral-ocr-4-1` | Input $4 /1000 Pages |

## Audio models

| Model | API name | Price |
| --- | --- | --- |
| [Voxtral Small](/models/voxtral-small-25-07) | `voxtral-small-2507` | Input $0.004 /Min<br>Input $0.1 /M Tokens<br>Output $0.4 /M Tokens |
'''


def test_mistral_source_reads_owned_token_rates_and_skips_other_hosts_and_units():
    prices = MistralSource().parse(MISTRAL_MD)
    assert set(prices) == {'mistral-large-4', 'mistral-small-2603', 'voxtral-small-2507'}
    large = prices['mistral-large-4']
    assert (large.input, large.output, large.cache_read, large.cache_write) == (
        0.7, 2.1, 0.07, None
    )
    small = prices['mistral-small-2603']
    assert (small.input, small.output, small.cache_read) == (0.15, 0.6, None)
    voxtral = prices['voxtral-small-2507']
    assert (voxtral.input, voxtral.output) == (0.1, 0.4)


def test_mistral_source_is_registered_before_aggregators():
    names = [source.name for source in SOURCES]
    assert names.index('mistral') < names.index('llm-prices.com')
    assert names.index('mistral') < names.index('openrouter')
