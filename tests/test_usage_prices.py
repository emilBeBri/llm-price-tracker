import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from llm_price_tracker import get_usage_price, load_book, save_book
from llm_price_tracker.compare import (
    apply_deltas,
    apply_usage_prices,
    usage_price_updates,
)
from llm_price_tracker.models import ModelEntry, Price, PriceBook
from llm_price_tracker.sources import (
    AnthropicSource,
    AzureImageSource,
    GoogleSource,
    OpenAISource,
)
from llm_price_tracker.sources.base import SourceResult

OPENAI = """Image generation models

Standard

### Grouped Pricing Table data
| Model | Modality | Input | Cached input | Output |
| --- | --- | --- | --- | --- |
| gpt-image-2.5-sunburst | Image | $8.00 | $2.00 | $30.00 |
| gpt-image-2.5-sunburst | Text | $5.00 | $1.25 | - |

Batch

| Model | Modality | Input | Cached input | Output |
| --- | --- | --- | --- | --- |
| gpt-image-2.5-sunburst | Image | $4 | $1 | $15 |

Tools

| Tool | Version | Price |
| --- | --- | --- |
| Web search | Web search (all models) | $10.00 / 1k calls + content tokens |
| Web search | Web search preview (non-reasoning models) | $25.00 / 1k calls |
"""


def test_openai_preserves_modalities_and_search_units_from_the_page():
    cards = OpenAISource().parse_usage_prices(OPENAI)
    image = cards['openai.image.gpt-image-2.5-sunburst']
    assert image.rates['input_text'].amount == 5
    assert image.rates['output_image'].amount == 30
    assert image.rates['output_image'].per == 1_000_000
    fee = cards['openai.web_search'].rates['searches']
    assert (fee.amount, fee.per, fee.unit) == (10, 1000, 'queries')
    changed = OpenAISource().parse_usage_prices(OPENAI.replace('$30.00', '$24.00'))
    assert changed[image.id].rates['output_image'].amount == 24


@pytest.mark.parametrize('currency', ['', 'USD '])
def test_anthropic_search_price_comes_from_the_published_sentence(currency):
    cards = AnthropicSource().parse_usage_prices(
        f'Web search is available on the Claude API for **$12 {currency}per 1,000 searches**, plus tokens.'
    )
    assert cards['anthropic.web_search'].rates['searches'].amount == 12
    assert AnthropicSource().parse_usage_prices('Web search is supported.') == {}


def test_google_reads_image_token_rate_not_per_image_equivalent():
    body = """<h2 id="gemini-nano-banana-2.1">Images</h2>
    <table class="pricing-table">
    <tr><td>Input price</td><td>$1.50 (text/image/video)</td></tr>
    <tr><td>Output price</td><td>$7.50 (text and thinking) $30.00 (images)
    Equivalent to $0.0336 per 1K image</td></tr>
    <tr><td>Grounding with Google Search</td><td>5,000 free, then $14 per 1,000 requests</td></tr>
    <tr><td>Grounding with Google Maps</td><td>5,000 free, then $14 / 1,000 search queries</td></tr>
    </table>"""
    cards = GoogleSource().parse_usage_prices(body)
    image = cards['google.image.gemini-nano-banana-2.1']
    assert image.rates['output_text'].amount == 7.5
    assert image.rates['output_image'].amount == 30
    assert cards['google.maps_grounding.gemini3'].rates['searches'].amount == 14
    assert cards['google.web_search.gemini3'].rates['searches'].unit == 'queries'
    separate = body.replace(
        '$1.50 (text/image/video)', '$0.50 (text) $1.50 (image)'
    ).replace(
        '$7.50 (text and thinking) $30.00 (images)',
        '$30.00 (images) $7.50 (text and thinking)',
    )
    separate_card = GoogleSource().parse_usage_prices(separate)[image.id]
    assert separate_card.rates['input_text'].amount == 0.5
    assert separate_card.rates['input_image'].amount == 1.5
    assert separate_card.rates['output_text'].amount == 7.5
    legacy = body.replace('gemini-nano-banana-2.1', 'gemini-2.5-flash')
    assert (
        GoogleSource()
        .parse_usage_prices(legacy)['google.web_search.gemini2']
        .rates['grounded_prompts']
        .unit
        == 'grounded_prompts'
    )


def _azure_body(*, second_region_input=5):
    fields = (
        ('Input Text', 5, second_region_input),
        ('Input Image', 8, 8),
        ('Output Image', 30, 30),
    )
    spans = ''.join(
        f"""{label}: <span class="price-data" data-amount='{
            json.dumps({'regional': {'sweden-central': first, 'us-east': second}})
        }'><span>$-</span></span><br>"""
        for label, first, second in fields
    )
    return f'<table><tr><th>Model</th><th>Pricing (1M Tokens)</th></tr><tr><td>Image-2.5-sunburst Global</td><td>{spans}</td></tr></table>'


def test_azure_reads_embedded_amounts_not_display_placeholders():
    source = AzureImageSource()
    body = _azure_body()
    client = SimpleNamespace(
        get=lambda *a, **k: SimpleNamespace(
            text=body,
            raise_for_status=lambda: None,
        )
    )
    result = source.fetch(client)
    assert result.ok
    assert result.prices == {}
    card = result.usage_prices['azure.image.gpt-image-2.5-sunburst.global']
    assert card.rates['input_text'].amount == 5
    assert card.rates['output_image'].amount == 30
    assert card.source_url == source.url
    assert card.vendor == 'azure'


def test_azure_refuses_to_guess_when_regions_differ():
    with pytest.raises(ValueError, match='region-specific'):
        AzureImageSource().parse_usage_prices(_azure_body(second_region_input=6))


def test_missing_fee_is_a_broken_source_even_with_valid_token_prices():
    body = OPENAI.replace('Web search (all models)', 'Renamed service')
    client = SimpleNamespace(
        get=lambda *a, **k: SimpleNamespace(
            text=body,
            raise_for_status=lambda: None,
        )
    )

    result = OpenAISource().fetch(client)

    assert result.prices
    assert not result.ok
    assert 'openai.web_search' in result.note


def test_azure_does_not_treat_a_changed_denominator_as_one_million():
    assert (
        AzureImageSource().parse_usage_prices(
            _azure_body().replace('1M Tokens', '1K Tokens')
        )
        == {}
    )


def test_usage_refresh_roundtrip_and_history_do_not_change_token_models(tmp_path):
    source = OpenAISource()
    before = PriceBook(
        updated_at='2026-10-01',
        models={
            'model': ModelEntry(
                id='model',
                vendor='openai',
                tiers={'standard': Price(input=1, output=2)},
            ),
        },
    )
    result = SourceResult(
        source.name,
        source.url,
        True,
        usage_prices=source.parse_usage_prices(OPENAI),
    )
    updates = usage_price_updates(before, [result])
    book = apply_usage_prices(before, updates, '2026-10-29')
    path = tmp_path / 'book.json'
    save_book(book, path)
    restored = load_book(path)
    assert restored.models == before.models
    assert usage_price_updates(restored, [result]) == {}
    assert (
        apply_deltas(restored, [], '2026-10-29').usage_prices == restored.usage_prices
    )
    assert get_usage_price('missing', restored) is None

    changed = SourceResult(
        source.name,
        source.url,
        True,
        usage_prices=source.parse_usage_prices(OPENAI.replace('$30.00', '$20.00')),
    )
    book = apply_usage_prices(
        restored, usage_price_updates(restored, [changed]), '2026-11-01'
    )
    key = 'openai.image.gpt-image-2.5-sunburst'
    assert get_usage_price(key, book).rates['output_image'].amount == 20
    assert (
        get_usage_price(key, book, datetime(2026, 10, 30, tzinfo=UTC))
        .rates['output_image']
        .amount
        == 30
    )


def test_aggregator_and_failed_source_cannot_authorize_usage_prices():
    source = OpenAISource()
    cards = source.parse_usage_prices(OPENAI)
    book = PriceBook(updated_at='2026-10-01')
    assert (
        usage_price_updates(
            book,
            [
                SourceResult(
                    'openrouter', 'https://example.org', True, usage_prices=cards
                ),
                SourceResult(source.name, source.url, False, usage_prices=cards),
            ],
        )
        == {}
    )


def test_refresh_writes_usage_only_changes(tmp_path, monkeypatch):
    from llm_price_tracker import main

    source = AzureImageSource()
    result = SourceResult(
        source.name,
        source.url,
        True,
        usage_prices=source.parse_usage_prices(_azure_body()),
    )
    path = tmp_path / 'book.json'
    save_book(PriceBook(updated_at='2026-10-01'), path)
    monkeypatch.setattr(main, '_fetch_or_exit', lambda timeout: [result])

    main.refresh(write=True, uncorroborated=False, timeout=30, book_path=path)

    assert (
        get_usage_price('azure.image.gpt-image-2.5-sunburst.global', load_book(path))
        is not None
    )
