"""The first-party vendor pricing pages.

Ported from the proven parsers in bb-scripts/llm-prices.py, with the two bugs
that had silently killed the OpenAI one fixed here from the start.
"""

from __future__ import annotations

import re

from selectolax.parser import HTMLParser

from ..models import Price, TimeWindow
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
    """api-docs.deepseek.com pricing: one transposed table, models as columns.

    Since 2026-08-18 each metric splits into OFF-PEAK/PEAK sub-rows (peak =
    2x off-peak). The period marker rides somewhere in the row, so the
    OFF/PEAK decision reads the whole row label, while the metric match uses
    the exact '1M … TOKENS' phrase — 'Json Output' in the FEATURES rows must
    not read as the output-price row. Rows without a period marker (the
    pre-activation table) fall to the standard rate. The window definition is
    a vendor fact too: parsed from the page's footnote, never hardcoded.
    """

    name = 'deepseek'
    # The trailing slash is load-bearing. Without it this path started serving
    # a DIFFERENT page on 2026-08-20 — 'Your First API Call', 45KB, one
    # perfectly valid <table> of base_url/api_key rows — while the slashed form
    # serves 'Models & Pricing'. Status 200, no redirect, correct content-type:
    # the worst shape a drift can take, because every layer above reads it as a
    # successful fetch. Only `parse` finding no MODEL row caught it.
    url = 'https://api-docs.deepseek.com/quick_start/pricing/'
    expect = ('deepseek',)

    _METRIC = re.compile(
        r'1M INPUT TOKENS \(CACHE HIT\)'
        r'|1M INPUT TOKENS \(CACHE MISS\)'
        r'|1M OUTPUT TOKENS'
    )
    _WINDOW_TEXT = re.compile(r'peak hours are\s+(.*?)\s*utc', re.IGNORECASE)
    _TIME_RANGE = re.compile(r'(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})')

    _HIT = '1M INPUT TOKENS (CACHE HIT)'
    _MISS = '1M INPUT TOKENS (CACHE MISS)'
    _OUT = '1M OUTPUT TOKENS'

    def parse(self, html: str) -> dict[str, Price]:
        tree = HTMLParser(html)
        table = tree.css_first('table')
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

        def at(
            store: dict[str, list[float | None]], metric: str, i: int
        ) -> float | None:
            vals = store.get(metric)
            return vals[i] if vals and i < len(vals) else None

        std: dict[str, list[float | None]] = {}
        peak: dict[str, list[float | None]] = {}
        cur: str | None = None
        for r in rows:
            label = ' '.join(r).upper()
            m = self._METRIC.search(label)
            if m:
                cur = m.group(0)
            if cur is None:
                continue
            # A row without a metric keyword AND without a period marker is
            # unrelated table furniture (Concurrency Limit, Base URLs, ...)
            # and must not overwrite the metric it happens to follow.
            if not (m or 'OFF' in label or 'PEAK' in label):
                continue
            # Price rows carry one cell per model as their TRAILING cells; the
            # first row of the block also carries a spanning 'PRICING(1)' cell.
            cells = r[-n:] if len(r) >= n else r
            if 'PEAK' in label and 'OFF' not in label:
                peak[cur] = [dollars(c) for c in cells]
            else:
                std[cur] = [dollars(c) for c in cells]

        windows = self._peak_windows(tree.text(strip=True))

        out: dict[str, Price] = {}
        for i, mid in enumerate(models):
            inp = at(std, self._MISS, i)
            outp = at(std, self._OUT, i)
            if inp is None or outp is None:
                continue
            peak_price = None
            if windows:
                p_in = at(peak, self._MISS, i)
                p_out = at(peak, self._OUT, i)
                if p_in is not None and p_out is not None:
                    peak_price = Price(
                        input=p_in,
                        output=p_out,
                        cache_read=at(peak, self._HIT, i),
                    )
            out[mid] = Price(
                input=inp,
                output=outp,
                cache_read=at(std, self._HIT, i),
                peak=peak_price,
                peak_windows=windows if peak_price else None,
            )
        return out

    def _peak_windows(self, text: str) -> list[TimeWindow] | None:
        """The footnote's 'Peak hours are … UTC' sentence, as TimeWindows."""
        m = self._WINDOW_TEXT.search(text)
        if m is None:
            return None
        pairs = self._TIME_RANGE.findall(m.group(1))
        if not pairs:
            return None
        return [TimeWindow(start=s, end=e) for s, e in pairs]


def _column_index(header: list[str], *needles: str) -> int | None:
    """First column whose header contains every needle (in order)."""
    for i, h in enumerate(header):
        if all(n in h for n in needles):
            return i
    return None


def _split_mdx_cells(row_text: str) -> list[str]:
    """Split one DocTable row into cells on top-level commas.

    Cells are "…"/`…` strings or <>…</> JSX fragments; a naive .split(',')
    breaks on the comma inside "1,048,576 tokens" and the {"$"} braces must
    not open a quote state while inside a fragment. String cells come back
    unquoted; fragments stay wrapped so the money regex can see them.
    """
    cells: list[str] = []
    buf: list[str] = []

    def push() -> None:
        cell = ''.join(buf).strip()
        if len(cell) >= 2 and cell[0] in '"\'`' and cell[-1] == cell[0]:
            cell = cell[1:-1]
        cells.append(cell)

    i, n = 0, len(row_text)
    quote: str | None = None
    in_frag = False
    while i < n:
        if in_frag:
            if row_text.startswith('</>', i):
                buf.append('</>')
                in_frag = False
                i += 3
            else:
                buf.append(row_text[i])
                i += 1
            continue
        if quote:
            buf.append(row_text[i])
            if row_text[i] == quote:
                quote = None
            i += 1
            continue
        if row_text.startswith('<>', i):
            buf.append('<>')
            in_frag = True
            i += 2
            continue
        c = row_text[i]
        if c in '"\'':
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == ',':
            push()
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    if buf:
        push()
    return cells


class MoonshotSource(Source):
    """Kimi K3 pricing on the platform docs — the MDX rendering.

    The forum.kimi.com announcement this source used to read is DNS-dead, and
    the pricing table now lives on platform.kimi.ai. The HTML page there is
    client-rendered (zero <table> elements in the raw body), but the docs site
    serves the source markdown at <url>.md, where the price table survives as
    a server-side <DocTable> MDX block. Prices sit in JSX fragments
    (<>{"$"}3.00</>), which `dollars` cannot see through — the fragment
    wrapper is unwrapped before extraction.

    The Chinese platform lists CNY rates; the USD table here is the rate
    OpenRouter-facing consumers pay, and the book's unit is USD. Never
    convert the CNY page through a live exchange rate.
    """

    name = 'moonshot'
    url = 'https://platform.kimi.ai/docs/pricing/chat-k3.md'
    accept = 'text/markdown,text/plain,*/*'
    expect = ('kimi-k3',)

    # Terminator is line-anchored: the closer sits on its own line (`]}` then
    # `/>`), while every price fragment `<>{"$"}3.00</>` ends mid-line with
    # `/>` — a bare `(.*?)/>` would stop at the first fragment.
    _DOCTABLE = re.compile(r'<DocTable\b(.*?)^\s*/>', re.DOTALL | re.MULTILINE)
    _TITLE = re.compile(r'title:\s*"([^"]+)"')
    # <>{"$"}3.00</> -> $3.00, so dollars() can see it.
    _FRAGMENT = re.compile(r'<>\{\s*"\$"\s*\}\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*</>')

    def parse(self, body: str) -> dict[str, Price]:
        out: dict[str, Price] = {}
        for block in self._DOCTABLE.findall(body):
            header = [t.lower() for t in self._TITLE.findall(block)]
            c_model = _column_index(header, 'model')
            c_hit = _column_index(header, 'cache hit')
            c_miss = _column_index(header, 'cache miss')
            c_out = _column_index(header, 'output')
            if c_model is None or c_miss is None or c_out is None:
                continue

            rows = re.search(r'rows=\{\[(.*?)\]\}', block, re.DOTALL)
            if rows is None:
                continue
            for row_text in re.findall(r'\[(.*?)\]', rows.group(1), re.DOTALL):
                cells = _split_mdx_cells(row_text)
                if len(cells) <= max(c_model, c_miss, c_out):
                    continue
                model_id = cells[c_model].strip().lower()
                inp = self._money(cells[c_miss])
                outp = self._money(cells[c_out])
                if inp is None or outp is None:
                    continue
                out[model_id] = Price(
                    input=inp,
                    output=outp,
                    cache_read=(
                        self._money(cells[c_hit])
                        if c_hit is not None and c_hit < len(cells)
                        else None
                    ),
                )
        return out

    def _money(self, cell: str) -> float | None:
        return dollars(self._FRAGMENT.sub(r'$\1', cell.strip()))


class ZaiSource(Source):
    """Z.ai pricing — the MARKDOWN rendering of the dedicated pricing page.

    The API-reference introduction page that used to carry the USD table no
    longer does; pricing moved to its own page. The docs site serves source
    markdown at <url>.md, where the Text Models table is a plain pipe table
    under a `### Text Models` anchor. The HTML page is client-rendered, which
    is exactly what broke the old source (fetched, parsed 0 models).
    """

    name = 'zai'
    url = 'https://docs.z.ai/guides/overview/pricing.md'
    accept = 'text/markdown,text/plain,*/*'
    expect = ('glm-5.2',)

    _SECTION = '### Text Models'

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
                if rows:
                    break  # table ended
                continue  # prose between the anchor and the table
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

        # First-match order is load-bearing: 'input' hits the plain Input
        # column before Cached Input, and 'cached' hits Cached Input before
        # Cached Input Storage.
        c_model = col('model')
        c_in = col('input')
        c_cached = col('cached')
        c_out = col('output')
        if c_model is None or c_in is None or c_out is None:
            return {}

        out: dict[str, Price] = {}
        for r in rows[1:]:
            if len(r) <= max(c_model, c_in, c_out):
                continue
            model_id = r[c_model].strip().lower()
            if not model_id.startswith('glm-'):
                continue
            inp, outp = dollars(r[c_in]), dollars(r[c_out])
            if inp is None or outp is None:
                continue
            out[model_id] = Price(
                input=inp,
                output=outp,
                cache_read=(
                    dollars(r[c_cached])
                    if c_cached is not None and c_cached < len(r)
                    else None
                ),
            )
        return out
