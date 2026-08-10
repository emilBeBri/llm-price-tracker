"""The first-party vendor pricing pages.

Ported from the proven parsers in bb-scripts/llm-prices.py, with the two bugs
that had silently killed the OpenAI one fixed here from the start.
"""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from ..models import Price
from .base import Source, dollars


class AnthropicSource(Source):
    """platform.claude.com pricing page.

    Reads the PRICING page, not the models-overview page. The overview lists
    only a model's standard rate; the pricing page is where a live introductory
    rate and its end date appear. Pointed at the overview, this source reports
    Sonnet 5 at 3/15 while Anthropic is actually charging 2/10 through
    2026-08-31 — and would "correct" a right answer into a 50% over-estimate.
    """

    name = 'anthropic'
    url = 'https://platform.claude.com/docs/en/about-claude/pricing.md'
    accept = 'text/markdown,text/plain,*/*'
    expect = ('opus', 'sonnet', 'haiku')

    # "Claude Sonnet 5 through August 31, 2026" -> ('sonnet 5', ' through ...')
    _NAME = re.compile(r'^claude\s+([a-z0-9.\s]+?)\s*(?:\[|\(|$)', re.IGNORECASE)

    def parse(self, body: str) -> dict[str, Price]:
        out: dict[str, Price] = {}
        for line in body.splitlines():
            s = line.strip()
            if not s.startswith('|') or 'claude' not in s.lower():
                continue
            cells = [c.strip() for c in s.strip('|').split('|')]
            if len(cells) < 5:
                continue
            m = self._NAME.match(cells[0])
            if not m:
                continue
            # Columns: name | base input | 5m cache write | 1h cache write |
            #          cache read | output
            rates = [dollars(c) for c in cells[1:6]]
            if len(rates) < 5 or rates[0] is None or rates[4] is None:
                continue
            slug = 'claude-' + re.sub(r'\s+', '-', m.group(1).strip().lower())
            # A model with a dated introductory rate appears TWICE: the current
            # row and the future one. Keep the first, which the page lists as
            # the rate in force now.
            out.setdefault(
                slug,
                Price(
                    input=rates[0],
                    output=rates[4],
                    cache_read=rates[3],
                    cache_write=rates[1],
                ),
            )
        return out


class OpenAISource(Source):
    """developers.openai.com pricing — the MARKDOWN rendering.

    Markdown on purpose, and it is worth knowing why, because the obvious
    reasoning points the other way. This page content-negotiates: ask for HTML
    and you get 587KB with 15 `<table>` elements; ask for markdown and you get
    19KB of pipe tables. The HTML looks richer and is worse — only the gpt-5.x
    family is server-rendered there, and the o-series and gpt-4.x tables are
    built client-side, so an HTML parser silently sees a fraction of the
    lineup. The markdown carries every family in one table.

    It also carries an explicit `### Standard pricing data` heading, which
    replaces the old "take the first qualifying table" guess with an anchor.
    That guess was load-bearing and wrong-adjacent: Standard, Batch, Flex and
    Fast tables all share a header, so a reordering upstream would have
    silently swapped in batch prices.

    The trap that killed the previous parser lives on in the column layout: a
    `cache writes` column sits between cached-input and output, so anything
    that pins output to a fixed index reads cache-writes as the output price.
    Columns are located by name here.
    """

    name = 'openai'
    url = 'https://developers.openai.com/api/docs/pricing'
    accept = 'text/markdown,text/plain,*/*'
    expect = ('gpt-5',)

    _SECTION = '### Standard pricing data'
    # 'gpt-5.5 (<272K context length)' -> 'gpt-5.5'
    _SUFFIX = re.compile(r'\s*\(.*?\)\s*$')

    def parse(self, body: str) -> dict[str, Price]:
        lines = body.splitlines()
        try:
            start = next(i for i, ln in enumerate(lines) if ln.strip() == self._SECTION)
        except StopIteration:
            return {}

        rows: list[list[str]] = []
        for ln in lines[start + 1 :]:
            s = ln.strip()
            if not s:
                continue
            if not s.startswith('|'):
                break  # table ended
            cells = [c.strip() for c in s.strip('|').split('|')]
            if all(set(c) <= {'-', ':'} for c in cells):
                continue  # the |---|---| separator
            rows.append(cells)
        if not rows:
            return {}

        header = [c.lower() for c in rows[0]]

        def col(*needles: str) -> int | None:
            for i, h in enumerate(header):
                if all(n in h for n in needles):
                    return i
            return None

        # Short context is the tier we record; the same row also carries the
        # long-context block, which is a different (higher) price.
        c_in = col('short context', 'input') or col('input')
        c_out = col('short context', 'output') or col('output')
        c_cached = col('short context', 'cached')
        c_write = col('short context', 'cache writes')
        if c_in is None or c_out is None:
            return {}

        out: dict[str, Price] = {}
        for r in rows[1:]:
            if len(r) <= max(c_in, c_out) or not r[0]:
                continue
            inp, outp = dollars(r[c_in]), dollars(r[c_out])
            if inp is None or outp is None:
                continue
            model = self._SUFFIX.sub('', r[0]).strip()
            out.setdefault(
                model,
                Price(
                    input=inp,
                    output=outp,
                    cache_read=dollars(r[c_cached]) if c_cached is not None else None,
                    cache_write=dollars(r[c_write]) if c_write is not None else None,
                ),
            )
        return out


class GoogleSource(Source):
    """ai.google.dev gemini pricing: `<h2 id="gemini-...">` + a pricing table."""

    name = 'google'
    url = 'https://ai.google.dev/gemini-api/docs/pricing'
    expect = ('gemini-3',)

    def parse(self, html: str) -> dict[str, Price]:
        out: dict[str, Price] = {}
        for chunk in re.split(r'<h2\b', html)[1:]:
            m = re.search(r'id="(gemini-[^"]+)"', chunk)
            if not m:
                continue
            table = HTMLParser(chunk).css_first('table.pricing-table')
            if table is None:
                continue
            inp = outp = cached = None
            for tr in table.css('tr'):
                c = [n.text(strip=True) for n in tr.css('th, td')]
                if not c:
                    continue
                label = c[0].lower()
                if label.startswith('input price'):
                    inp = dollars(c[-1])
                elif label.startswith('output price'):
                    outp = dollars(c[-1])
                elif label.startswith('context caching'):
                    # Cell reads "$0.15 $1.00 / 1,000,000 tokens per hour"; the
                    # first figure is the per-token read, the second is storage.
                    cached = dollars(c[-1])
            if inp is not None and outp is not None:
                out[m.group(1)] = Price(input=inp, output=outp, cache_read=cached)
        return out


class DeepSeekSource(Source):
    """api-docs.deepseek.com pricing: one transposed table, models as columns."""

    name = 'deepseek'
    url = 'https://api-docs.deepseek.com/quick_start/pricing'
    expect = ('deepseek',)

    def parse(self, html: str) -> dict[str, Price]:
        table = HTMLParser(html).css_first('table')
        if table is None:
            return {}
        rows = [
            [n.text(strip=True) for n in tr.css('th, td')] for tr in table.css('tr')
        ]

        models: list[str] = []
        for r in rows:
            if r and r[0].strip().upper() == 'MODEL':
                models = [re.sub(r'\(\d+\)', '', c).strip() for c in r[1:] if c.strip()]
                break
        if not models:
            return {}

        n = len(models)
        miss = outp = hit = None
        for r in rows:
            label = ' '.join(r).upper()
            # Price rows carry one cell per model as their TRAILING cells; the
            # first row also carries a spanning 'PRICING' label cell.
            cells = r[-n:] if len(r) >= n else r
            if 'CACHE MISS' in label:
                miss = [dollars(c) for c in cells]
            elif 'OUTPUT TOKENS' in label:
                outp = [dollars(c) for c in cells]
            elif 'CACHE HIT' in label:
                hit = [dollars(c) for c in cells]

        def at(arr: list | None, i: int) -> float | None:
            return arr[i] if arr and i < len(arr) else None

        out: dict[str, Price] = {}
        for i, mid in enumerate(models):
            inp, o = at(miss, i), at(outp, i)
            if inp is None or o is None:
                continue
            out[mid] = Price(input=inp, output=o, cache_read=at(hit, i))
        return out


class MoonshotSource(Source):
    """Kimi's first-party USD pricing announcement.

    Moonshot's Chinese platform lists CNY rates, while the international Kimi
    forum publishes the USD API rate used by OpenRouter-facing consumers. The
    K3 announcement has one label/value table rather than a model matrix.
    """

    name = 'moonshot'
    url = 'https://forum.kimi.com/t/kimi-k3-is-live/744'
    expect = ('kimi-k3',)

    def parse(self, html: str) -> dict[str, Price]:
        inp = outp = cached = None
        for table in HTMLParser(html).css('table'):
            for tr in table.css('tr'):
                cells = [n.text(strip=True) for n in tr.css('th, td')]
                if len(cells) < 2:
                    continue
                label = cells[0].lower()
                if label == 'input price':
                    inp = dollars(cells[-1])
                elif label == 'output price':
                    outp = dollars(cells[-1])
                elif label == 'cache hit price':
                    cached = dollars(cells[-1])

        if inp is None or outp is None:
            return {}
        return {'kimi-k3': Price(input=inp, output=outp, cache_read=cached)}


class ZaiSource(Source):
    """Z.ai API introduction: a normal model-by-row USD pricing table."""

    name = 'zai'
    url = 'https://docs.z.ai/api-reference/introduction'
    expect = ('glm-5.2',)

    def parse(self, html: str) -> dict[str, Price]:
        out: dict[str, Price] = {}
        for table in HTMLParser(html).css('table'):
            rows = [
                [n.text(strip=True) for n in tr.css('th, td')] for tr in table.css('tr')
            ]
            if not rows:
                continue
            header = [cell.lower() for cell in rows[0]]

            def column(columns: list[str], *needles: str) -> int | None:
                for i, cell in enumerate(columns):
                    if all(needle in cell for needle in needles):
                        return i
                return None

            c_model = column(header, 'model')
            c_in = column(header, 'input', 'cache miss')
            c_cached = column(header, 'input', 'cache hit')
            c_out = column(header, 'output')
            if c_model is None or c_in is None or c_out is None:
                continue

            for row in rows[1:]:
                if len(row) <= max(c_model, c_in, c_out):
                    continue
                model_id = row[c_model].strip().lower()
                if not model_id.startswith('glm-'):
                    continue
                inp, outp = dollars(row[c_in]), dollars(row[c_out])
                if inp is None or outp is None:
                    continue
                out[model_id] = Price(
                    input=inp,
                    output=outp,
                    cache_read=(
                        dollars(row[c_cached])
                        if c_cached is not None and c_cached < len(row)
                        else None
                    ),
                )
        return out
