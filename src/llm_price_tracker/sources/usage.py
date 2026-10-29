"""First-party image modalities and per-use fees, with their published units."""

from __future__ import annotations

import json
import re
from decimal import Decimal

from selectolax.parser import HTMLParser

from ..models import UnitPrice, UsagePrice
from .base import Source, dollars
from .vendors import OpenAISource, _pipe_tables

_SERVICE_RATE = re.compile(
    r'\$\s*([\d,.]+)\s*(?:USD\s+)?(?:/|per)\s*(1,000|1000|1k)\s*'
    r'(?:search queries|grounded prompts|search requests|requests|searches|calls)',
    re.IGNORECASE,
)


def _token_rate(amount: float) -> UnitPrice:
    return UnitPrice(amount=amount, per=1_000_000, unit='tokens')


def _service_rate(text: str, unit: str) -> UnitPrice | None:
    match = _SERVICE_RATE.search(text.replace('*', ''))
    if match is None:
        return None
    return UnitPrice(amount=float(match[1].replace(',', '')), per=1000, unit=unit)


def _modality_rates(cell: str) -> dict[str, UnitPrice]:
    rates = {}
    for match in re.finditer(r'\$\s*([\d,.]+)\s*\(([^)]+)\)', cell):
        qualifier = match[2].lower()
        for modality in ('text', 'image'):
            if modality not in qualifier:
                continue
            rate = _token_rate(float(match[1].replace(',', '')))
            if modality in rates and rates[modality] != rate:
                raise ValueError(f'Ambiguous {modality} modality rates: {cell}')
            rates[modality] = rate
    return rates


def openai_usage_prices(body: str, url: str) -> dict[str, UsagePrice]:
    source = OpenAISource()
    image_models = source._parse_image_models(body.splitlines())
    image_section = body.partition(source._IMAGE_SECTION)[2]
    standard = re.split(
        r'(?m)^\s*(?:Batch|Flex|Fast|Priority)\s*$', image_section, maxsplit=1
    )[0]
    rates: dict[str, dict[str, UnitPrice]] = {}
    for _, rows in _pipe_tables(standard):
        header = [cell.lower() for cell in rows[0]]
        if not {'model', 'modality', 'input', 'output'}.issubset(header):
            continue
        for row in rows[1:]:
            if len(row) != len(header):
                continue
            model = row[header.index('model')]
            modality = row[header.index('modality')].lower()
            if model not in image_models or modality not in ('image', 'text'):
                continue
            for column, prefix in (
                ('input', 'input'),
                ('cached input', 'cached_input'),
                ('output', 'output'),
            ):
                if column not in header:
                    continue
                amount = dollars(row[header.index(column)])
                if amount is not None:
                    rates.setdefault(model, {})[f'{prefix}_{modality}'] = _token_rate(
                        amount
                    )
        break
    cards = {}
    for model, model_rates in rates.items():
        if not {'input_text', 'output_image'}.issubset(model_rates):
            continue
        key = f'openai.image.{model}'
        cards[key] = UsagePrice(
            id=key,
            vendor='openai',
            rates=model_rates,
            source_url=url,
            note='Standard image modalities; cached rates require an endpoint that supports caching.',
        )
    for _, rows in _pipe_tables(body):
        for row in rows[1:]:
            if len(row) >= 3 and row[1].lower() == 'web search (all models)':
                rate = _service_rate(row[2], 'queries')
                if rate is not None:
                    key = 'openai.web_search'
                    cards[key] = UsagePrice(
                        id=key,
                        vendor='openai',
                        rates={'searches': rate},
                        source_url=url,
                        note='Non-preview web search; content tokens billed separately.',
                    )
    return cards


def anthropic_usage_prices(body: str, url: str) -> dict[str, UsagePrice]:
    match = re.search(
        r'Web search is available on the Claude API[^\n]+', body, re.IGNORECASE
    )
    rate = _service_rate(match[0], 'queries') if match else None
    if rate is None:
        return {}
    key = 'anthropic.web_search'
    return {
        key: UsagePrice(
            id=key,
            vendor='anthropic',
            rates={'searches': rate},
            source_url=url,
            note='Successful searches; content tokens billed separately.',
        )
    }


def google_usage_prices(html: str, url: str) -> dict[str, UsagePrice]:
    cards: dict[str, UsagePrice] = {}
    for chunk in re.split(r'<h2\b', html)[1:]:
        match = re.search(r'id="(gemini-[^"]+)"', chunk)
        if match is None:
            continue
        model = match[1]
        table = HTMLParser(chunk).css_first('table.pricing-table')
        if table is None:
            continue
        cells = {}
        for row in table.css('tr'):
            values = [
                node.text(separator=' ', strip=True) for node in row.css('th, td')
            ]
            if values:
                cells[values[0].lower()] = values[-1]
        if 'image' in model or 'nano-banana' in model:
            input_cell = next(
                (v for k, v in cells.items() if k.startswith('input price')), ''
            )
            output_cell = next(
                (v for k, v in cells.items() if k.startswith('output price')), ''
            )
            inputs, outputs = _modality_rates(input_cell), _modality_rates(output_cell)
            if 'text' in inputs and {'text', 'image'}.issubset(outputs):
                key = f'google.image.{model}'
                cards[key] = UsagePrice(
                    id=key,
                    vendor='google',
                    source_url=url,
                    rates={
                        **{
                            f'input_{modality}': rate
                            for modality, rate in inputs.items()
                        },
                        **{
                            f'output_{modality}': rate
                            for modality, rate in outputs.items()
                        },
                    },
                    note='Standard text/image input, text/thinking output, and image output.',
                )
        for label, cell in cells.items():
            if not label.startswith('grounding with google'):
                continue
            kind = 'maps_grounding' if 'maps' in label else 'web_search'
            legacy = model.startswith('gemini-2')
            unit = 'grounded_prompts' if legacy else 'queries'
            rate = _service_rate(cell, unit)
            if rate is None:
                continue
            key = f'google.{kind}.gemini{"2" if legacy else "3"}'
            rate_key = 'grounded_prompts' if legacy else 'searches'
            card = UsagePrice(
                id=key,
                vendor='google',
                rates={rate_key: rate},
                source_url=url,
                note='Paid rate before account-wide free allowances.',
            )
            if key in cards and cards[key].rates != card.rates:
                raise ValueError(f'{key}: conflicting per-model grounding rates')
            cards[key] = card
    return cards


class AzureImageSource(Source):
    """Microsoft's embedded USD regional data, not its '$-' display placeholders."""

    name = 'azure'
    url = 'https://azure.microsoft.com/en-us/pricing/details/azure-openai/'
    expect_usage = ('azure.image.gpt-image-2.5-sunburst.global',)

    def parse(self, body: str) -> dict:
        return {}

    def parse_usage_prices(self, body: str) -> dict[str, UsagePrice]:
        cards = {}
        labels = {
            'Input Text': 'input_text',
            'Cached Input Text': 'cached_input_text',
            'Input Image': 'input_image',
            'Cached Input Image': 'cached_input_image',
            'Output Image': 'output_image',
        }
        for row in HTMLParser(body).css('tr'):
            cells = row.css('td')
            if len(cells) < 2:
                continue
            match = re.fullmatch(
                r'(?:GPT-)?Image-([a-z0-9.-]+) Global',
                cells[0].text(strip=True),
                re.IGNORECASE,
            )
            if match is None:
                continue
            table = row.parent
            while table is not None and table.tag != 'table':
                table = table.parent
            if table is None or not re.search(
                r'1M\s+Tokens', table.text(), re.IGNORECASE
            ):
                continue
            rates = {}
            for span in cells[1].css('span.price-data[data-amount]'):
                label = (
                    span.prev.text(strip=True).rstrip(':').strip() if span.prev else ''
                )
                if label not in labels:
                    continue
                regional = json.loads(span.attributes['data-amount']).get(
                    'regional', {}
                )
                amounts = {
                    Decimal(str(value))
                    for value in regional.values()
                    if isinstance(value, (int, float)) and not isinstance(value, bool)
                }
                if len(amounts) != 1:
                    raise ValueError(
                        f'Azure Image-{match[1]} {label}: region-specific or missing rates; '
                        'refusing to choose an arbitrary region'
                    )
                rates[labels[label]] = _token_rate(float(amounts.pop()))
            if not {'input_text', 'input_image', 'output_image'}.issubset(rates):
                continue
            key = f'azure.image.gpt-image-{match[1].lower()}.global'
            cards[key] = UsagePrice(
                id=key,
                vendor='azure',
                rates=rates,
                source_url=self.url,
                note='Azure Global Standard; published USD rates agree across all available regions.',
            )
        return cards
