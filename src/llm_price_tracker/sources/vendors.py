"""The first-party vendor pricing pages.

Ported from the proven parsers in bb-scripts/llm-prices.py, with the two bugs
that had silently killed the OpenAI one fixed here from the start.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from datetime import date

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
    expect_usage = ('anthropic.web_search',)

    # "Claude Sonnet 5 through August 31, 2026" -> ('sonnet 5', ' through ...')
    _NAME = re.compile(r'^claude\s+([a-z0-9.\s]+?)\s*(?:\[|\(|$)', re.IGNORECASE)
    # "Claude Sonnet 5 starting September 1, 2026" -> 'September 1, 2026'.
    _STARTING = re.compile(r'\s+starting\s+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})')
    # The prompt-size band is published as a SECOND ROW for the same model:
    # 'Claude Haiku 5.5 (for prompts up to 100,000 tokens)' and '... (for
    # prompts over 100,000 tokens)'. Both name the same model and both carry
    # the full five rate columns; the second is the surcharge, and its
    # parenthetical is the only thing distinguishing it from a rate change.
    _BAND = re.compile(
        r'\(\s*(?:for\s+)?prompts\s+(up to|over)\s+([\d,]+)\s+tokens?\s*\)',
        re.IGNORECASE,
    )

    def parse_usage_prices(self, body):
        from .usage import anthropic_usage_prices

        return anthropic_usage_prices(body, self.url)

    def parse(self, body: str) -> dict[str, Price]:
        out: dict[str, Price] = {}
        future: dict[str, list[Price]] = {}
        # slug -> (last short-band token count, the surcharge row above it)
        bands: dict[str, tuple[int, Price]] = {}
        for line in body.splitlines():
            s = line.strip()
            if not s.startswith('|') or 'claude' not in s.lower():
                continue
            cells = [c.strip() for c in s.strip('|').split('|')]
            if len(cells) < 5:
                continue
            # A model with a dated rate cut appears TWICE: the row in force now
            # ('[through August 31, 2026]') and the row that replaces it
            # ('starting September 1, 2026'). The first is the current rate; the
            # second is a vendor-published future fact, and it is kept as
            # `scheduled` instead of dropped. Dropping it is how the doubling
            # arrives as an unexplained surprise rather than a dated row — and
            # how a page that CANCELS an announced increase (Anthropic did
            # exactly that when it made Sonnet 5's intro rate permanent) would
            # leave a phantom price in the book forever.
            name_cell = cells[0]
            band = self._BAND.search(name_cell)
            if band is not None:
                name_cell = name_cell[: band.start()]
            start = self._STARTING.search(name_cell)
            if start is not None:
                name_cell = name_cell[: start.start()]
            m = self._NAME.match(name_cell)
            if not m:
                continue
            # Columns: name | base input | 5m cache write | 1h cache write |
            #          cache read | output
            rates = [dollars(c) for c in cells[1:6]]
            if len(rates) < 5 or rates[0] is None or rates[4] is None:
                continue
            slug = 'claude-' + re.sub(r'\s+', '-', m.group(1).strip().lower())
            price = Price(
                input=rates[0],
                output=rates[4],
                cache_read=rates[3],
                cache_write=rates[1],
            )
            if band is not None and start is None:
                # 'up to N' names the row the scalar fields already hold, so it
                # needs nothing. 'over N' is the surcharge; it is attached to
                # the base row after the loop, because the two rows can arrive
                # in either order. An ANNOUNCED band row (a start date on a
                # surcharge) is skipped rather than mis-filed as the current
                # band: no vendor publishes one today, and guessing would put a
                # future price in force now.
                if band.group(1).lower() == 'over':
                    bands.setdefault(
                        slug, (int(band.group(2).replace(',', '')), price)
                    )
                    continue
            if start is not None:
                when = _iso_date(start.group(1) or '')
                if when is not None:
                    future.setdefault(slug, []).append(
                        price.model_copy(update={'effective_from': when})
                    )
                continue
            out.setdefault(slug, price)
        for slug, (limit, surcharge) in bands.items():
            current = out.get(slug)
            if current is not None:
                out[slug] = current.model_copy(
                    update={
                        'long_context_above': limit,
                        'long_context': surcharge,
                    }
                )
        for slug, rows in future.items():
            current = out.get(slug)
            if current is not None:
                out[slug] = current.model_copy(
                    update={
                        'scheduled': sorted(rows, key=lambda p: p.effective_from or '')
                    }
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

    Cache writes are published per TTL — `30m write` and `12h write`, then both
    again inside the long-context block. The book records ONE write rate per
    row and it is the SHORTEST published TTL (30m here, 5m on Anthropic), so
    the 30m cell is the one read; 12h is a longer-lived cache at a different
    rate and folding it in would record a price nobody is charged for the
    default case. The single `cache writes` column this replaced carried that
    same rate, and the fallback stays because the renderer may serve either
    shape.

    The image models are NOT in that table. They live in their own section
    under a `### Grouped Pricing Table data` heading that the page reuses for a
    dozen unrelated tables, so they need a different anchor and a different
    row filter — see `_parse_image_models`. The two parses are kept separate
    rather than generalised: each stops on a different rule, and the shared
    helper that would unify them is exactly what would let one table's failure
    silently pick up another table's rows.
    """

    name = 'openai'
    url = 'https://developers.openai.com/api/docs/pricing'
    accept = 'text/markdown,text/plain,*/*'
    # gpt-image is here so the image section vanishing is reported as drift
    # rather than read as "OpenAI stopped selling image models".
    expect = ('gpt-5', 'gpt-image')
    expect_usage = ('openai.image.', 'openai.web_search')

    # 'Short context: ≤272K input tokens. Long context: >272K input tokens.'
    _CAPTION = re.compile(
        r'Short context:\s*≤\s*([\d.]+)\s*K input tokens\.\s*'
        r'Long context:\s*>\s*([\d.]+)\s*K input tokens'
    )
    _SECTION = '### Standard pricing data'
    # 'gpt-5.5 (<272K context length)' -> 'gpt-5.5'
    _SUFFIX = re.compile(r'\s*\(.*?\)\s*$')
    _IMAGE_SECTION = 'Image generation models'
    # The tier tabs rendered inside a section. 'Standard' is the one we record;
    # the rest exist to be refused.
    _TIER_LABELS = ('Standard', 'Batch', 'Flex', 'Fast', 'Priority')

    def parse_usage_prices(self, body):
        from .usage import openai_usage_prices

        return openai_usage_prices(body, self.url)

    @staticmethod
    def _cells(line: str) -> list[str] | None:
        """Split one markdown table line, or None if it is the |---| separator."""
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if all(set(c) <= {'-', ':'} for c in cells):
            return None
        return cells

    def parse(self, body: str) -> dict[str, Price]:
        lines = body.splitlines()
        out = self._parse_standard(lines, body)
        # Image ids ('gpt-image-*', 'chatgpt-image-latest') cannot collide with
        # the standard table's, so a plain merge is safe, and a drifted image
        # section costs those rows without touching the text lineup.
        out.update(self._parse_image_models(lines))
        return out

    @staticmethod
    def _col(
        header: list[str], *needles: str, without: tuple[str, ...] = ()
    ) -> int | None:
        for i, h in enumerate(header):
            if all(n in h for n in needles) and not any(w in h for w in without):
                return i
        return None

    def _threshold(self, body: str) -> int:
        """The long-context boundary, from the sentence the page publishes.

        'Short context: ≤272K input tokens. Long context: >272K input tokens.'
        — once, document-wide, above nothing in particular. Read, not assumed:
        a page that drops or rewrites the sentence raises rather than keeping
        yesterday's boundary, because a stale threshold silently prices a large
        prompt at the small-prompt rate.
        """
        found = self._CAPTION.findall(body)
        if len(found) != 1:
            raise ValueError(
                f'long-context caption: {len(found)} occurrences, expected exactly 1'
            )
        short, long_ = found[0]
        if float(short) != float(long_):
            raise ValueError(f'long-context caption disagrees with itself: {found[0]}')
        return int(float(short) * 1_000)

    def _long_block(
        self, row: list[str], cols: dict[str, int | None], model: str
    ) -> Price | None:
        """The row's long-context rates, or None when it publishes none.

        Absent only when EVERY present long cell is a dash: a numeric 12h write
        with no long input or output is a half-published band, not an absence,
        and so is a lone cached-input figure.
        """
        texts = {k: (row[i].strip() if i is not None else '-') for k, i in cols.items()}
        if all(v in ('-', '—', '') for v in texts.values()):
            return None
        inp, outp = dollars(texts['input']), dollars(texts['output'])
        if inp is None or outp is None:
            raise ValueError(
                f'{model}: long-context block is only partly published '
                f'({texts}) — refusing to read it as no surcharge'
            )
        return Price(
            input=inp,
            output=outp,
            cache_read=dollars(texts['cached']),
            cache_write=dollars(texts['write']),
        )

    def _parse_standard(self, lines: list[str], body: str) -> dict[str, Price]:
        try:
            start = next(i for i, ln in enumerate(lines) if ln.strip() == self._SECTION)
        except StopIteration:
            return {}

        # Contiguous pipe blocks, each read against its OWN header. The section
        # carries the modern eleven-column table and then, after a blank line, a
        # legacy four-column one for the older families; a blank line does not
        # end a table here, so both used to be parsed against the first header.
        blocks: list[list[list[str]]] = []
        current: list[list[str]] = []
        for ln in lines[start + 1 :]:
            s = ln.strip()
            if not s:
                if current:
                    blocks.append(current)
                    current = []
                continue
            if not s.startswith('|'):
                break  # the section's tables ended
            cells = self._cells(s)
            if cells is None:
                continue  # the |---|---| separator
            current.append(cells)
        if current:
            blocks.append(current)

        out: dict[str, Price] = {}
        for rows in blocks:
            for model, price in self._parse_block(rows, body).items():
                out.setdefault(model, price)
        return out

    def _parse_block(self, rows: list[list[str]], body: str) -> dict[str, Price]:
        if not rows:
            return {}
        header = [c.lower() for c in rows[0]]
        col = self._col

        if col(header, 'short context') is None:
            # Legacy four-column table: Model | Input | Output | Cached input.
            # No prompt-size block exists for these models.
            c_in = col(header, 'input', without=('cached',))
            c_out = col(header, 'output')
            if c_in is None or c_out is None:
                return {}
            c_cached = col(header, 'cached input')
            c_write = None
            long_cols = None
        else:
            c_in = col(header, 'short context', 'input', without=('cached', 'write'))
            c_out = col(header, 'short context', 'output')
            c_cached = col(header, 'short context', 'cached input')
            c_write = col(header, 'short context', '30m write')
            if c_write is None:
                c_write = col(header, 'short context', 'cache writes')
            long_cols = {
                'input': col(header, 'long context', 'input', without=('cached', 'write')),
                'output': col(header, 'long context', 'output'),
                'cached': col(header, 'long context', 'cached input'),
                'write': col(header, 'long context', '30m write'),
                '12h': col(header, 'long context', '12h write'),
            }
            if long_cols['write'] is None:
                long_cols['write'] = col(header, 'long context', 'cache writes')
            # Required by NAME on both sides. Symmetry is not completeness: a
            # page that renames both cached-input columns would otherwise match
            # itself and record two live rates as absent.
            required = [
                name
                for name, idx in (
                    ('short context input', c_in),
                    ('short context output', c_out),
                    ('short context cached input', c_cached),
                    ('short context write', c_write),
                    ('long context input', long_cols['input']),
                    ('long context output', long_cols['output']),
                    ('long context cached input', long_cols['cached']),
                    ('long context write', long_cols['write']),
                )
                if idx is None
            ]
            if required:
                raise ValueError(
                    f'standard table header missing {required} — the page was '
                    'reshaped, refusing to read absent rates'
                )

        out: dict[str, Price] = {}
        for r in rows[1:]:
            if not r or not r[0]:
                continue
            if len(r) != len(header):
                raise ValueError(
                    f'row {r[0][:40]!r} has {len(r)} cells, header has {len(header)}'
                )
            inp, outp = dollars(r[c_in]), dollars(r[c_out])
            if inp is None or outp is None:
                continue
            model = self._SUFFIX.sub('', r[0]).strip()
            price = Price(
                input=inp,
                output=outp,
                cache_read=dollars(r[c_cached]) if c_cached is not None else None,
                cache_write=dollars(r[c_write]) if c_write is not None else None,
            )
            if long_cols is not None:
                band = self._long_block(r, long_cols, model)
                if band is not None:
                    price = price.model_copy(
                        update={
                            'long_context_above': self._threshold(body),
                            'long_context': band,
                        }
                    )
            out.setdefault(model, price)
        return out

    def _parse_image_models(self, lines: list[str]) -> dict[str, Price]:
        """The 'Image generation models' section's Standard table.

        Three things separate this table from the standard one, and each is its
        own way to record a confident wrong number:

        1. Its heading, `### Grouped Pricing Table data`, is shared with about
           a dozen other tables on the page — cyber models, realtime/audio,
           video, fine-tuning. Anchoring on the heading picks whichever comes
           first, which is the cyber table. The anchor is the section title,
           then the tier tab, then the first table under it.
        2. Standard and Batch render IDENTICAL headers inside the section — the
           same trap `### Standard pricing data` was introduced to fix upstream
           for the text models, and it has no equivalent heading here. So we
           refuse outright if any other tier label appears between 'Standard'
           and the table, rather than reading on and billing batch rates as
           standard.
        3. Every model occupies TWO rows, one per modality, and the row we want
           is the Image one. Text is not a lesser copy of it: on five of the
           seven models the Text row's output cell is '-' (they emit no text at
           all), and on the two that do emit text it is a different, lower rate
           for a different product. Only the Image row is complete for every
           model, and image tokens are what an image call is actually billed
           for.

           We record that row AS PUBLISHED. Pairing the text INPUT rate ($5.00)
           with the image OUTPUT rate ($30.00) would describe a text-prompt
           generation call more accurately, and that is exactly why it does not
           belong here: which modality a caller sends is a consuming app's
           billing choice, and this book holds vendor facts. The per-model text
           rate lives in the book row's note instead.
        """
        try:
            start = next(
                i for i, ln in enumerate(lines) if ln.strip() == self._IMAGE_SECTION
            )
        except StopIteration:
            return {}

        tier = None
        for i in range(start + 1, len(lines)):
            s = lines[i].strip()
            if s == 'Standard':
                tier = i
                break
            # A table, or another tier's tab, before the Standard tab means the
            # section is not shaped the way this parser was written against.
            if s in self._TIER_LABELS or s.startswith('|'):
                return {}
        if tier is None:
            return {}

        rows: list[list[str]] = []
        for ln in lines[tier + 1 :]:
            s = ln.strip()
            if not s.startswith('|'):
                if rows:
                    break  # table ended
                if s in self._TIER_LABELS:
                    return {}  # reached the next tier without finding a table
                continue  # prose and the shared heading, before the table
            cells = self._cells(s)
            if cells is None:
                continue
            rows.append(cells)
        if len(rows) < 2:
            return {}

        header = [c.lower() for c in rows[0]]
        # Exact names, not substrings: 'cached input' contains 'input', and a
        # renamed column must yield nothing rather than the neighbouring price.
        try:
            c_in = header.index('input')
            c_cached = header.index('cached input')
            c_out = header.index('output')
            c_modality = header.index('modality')
        except ValueError:
            return {}

        out: dict[str, Price] = {}
        for r in rows[1:]:
            if len(r) <= max(c_in, c_out, c_cached, c_modality) or not r[0]:
                continue
            if r[c_modality].lower() != 'image':
                continue
            inp, outp = dollars(r[c_in]), dollars(r[c_out])
            if inp is None or outp is None:
                continue
            out.setdefault(
                r[0],
                Price(input=inp, output=outp, cache_read=dollars(r[c_cached])),
            )
        return out


def _md_sections(body: str) -> Iterator[tuple[str, str]]:
    """(wire id, section text) for every `## <name> {% id="…" %}` section.

    This page is MDX, not HTML: each model is an `##` heading carrying its own
    wire id in a directive, and the prices live in a `{% table %}` inside a
    `{% tab title="Standard" %}`. There are no `<table>` elements and no pipe
    tables at all, which is why the HTML parser and `_pipe_tables` both return
    nothing here.
    """
    # The fetched page wraps the markdown in an HTML shell and escapes it on
    # the way through: `{% id=&quot;gemini-4-argon&quot; %}`, `prompts &lt;= 200k`.
    # Unescape once, here, so every downstream reader works on the document the
    # vendor wrote rather than on its HTML-escaped copy.
    for chunk in re.split(r'(?m)^## ', html.unescape(body))[1:]:
        match = re.search(r'\{%\s*id="([^"]+)"\s*%\}', chunk)
        if match is not None:
            yield match[1], chunk


def _md_cells(row: str) -> list[str]:
    """One MDX table row's cells: `*` bullets, continuation lines folded in.

    A cell can span several lines (a per-image equivalence note under the token
    rate), so a bullet starts a cell and every following non-bullet line belongs
    to it. `*` must be followed by whitespace — `*[text](link)*` is emphasis.
    """
    cells: list[str] = []
    current: list[str] = []
    for line in row.splitlines():
        stripped = line.strip()
        if stripped.startswith('*') and stripped[1:2].isspace():
            if current:
                cells.append(' '.join(current).strip())
            current = [stripped[1:].strip()]
        elif stripped and current:
            current.append(stripped)
    if current:
        cells.append(' '.join(current).strip())
    return cells


def _md_paid_column(section: str) -> dict[str, str]:
    """{row label lowercased: the PAID tier cell} for the Standard tab.

    Selection is by name at every step, because a positional read records a
    confident wrong price the first time the page reorders a column: the tab by
    its title, the table by its directive, the paid column by its header cell
    ('Paid Tier, per 1M tokens in USD'), and each row by its label. A row whose
    width disagrees with the header is not a row we understand — it raises
    rather than shifting a neighbouring price into the answer.
    """
    tab = re.search(
        r'\{%\s*tab title="Standard"\s*%\}(.*?)\{%\s*/tab\s*%\}', section, re.S
    )
    if tab is None:
        return {}
    table = re.search(r'\{%\s*table\s*%\}(.*?)\{%\s*/table\s*%\}', tab[1], re.S)
    if table is None:
        return {}
    rows = [
        _md_cells(part)
        for part in re.split(r'(?m)^\s*---\s*$', table[1])
        if part.strip()
    ]
    rows = [row for row in rows if row]
    if not rows:
        return {}
    header = rows[0]
    paid = next(
        (i for i, cell in enumerate(header) if 'paid tier' in cell.lower()), None
    )
    if paid is None:
        return {}
    out: dict[str, str] = {}
    for row in rows[1:]:
        label = row[0].lower()
        if len(row) != len(header):
            # A merged row ('Not Supported' spanning Free and Paid) is legal and
            # carries nothing we read. A row we DO read must not be shifted, so
            # the width check is enforced exactly where a wrong index would
            # record a confident wrong rate, and skipped where it would only
            # brick a model over a cell we never touch.
            if label.startswith(('input price', 'output price', 'context caching')):
                raise ValueError(
                    f'row {row[0][:60]!r} has {len(row)} cells, header has '
                    f'{len(header)} — refusing to guess the paid column'
                )
            continue
        if paid >= len(row):
            continue
        out.setdefault(label, row[paid])
    return out


class GoogleSource(Source):
    """ai.google.dev gemini pricing: `<h2 id="gemini-...">` + a pricing table.

    The column header promises "Paid Tier, per 1M tokens in USD" and an
    individual cell is free to contradict it. Every image model's output cell
    carries both units — `$3 (text and thinking)$60.00 (images)Equivalent to
    $0.045 per 0.5K image` — and taking the first amount picks the token rate,
    which is what the book records. `gemini-2.5-flash-image` is the one row
    that publishes NO token rate: its output cell reads `$0.039 per image`
    alone, and reading that as a per-1M-token price recorded a 2.5/M model at
    0.039/M for a month. That error is 64x, not 1e6, so `save_book`'s
    per-token/per-1M ceiling never sees it. A unit qualifier attached to the
    amount therefore disqualifies the cell — no output price, no model, and
    the row is reported absent rather than silently wrong.
    """

    name = 'google'
    url = 'https://ai.google.dev/gemini-api/docs/pricing'
    expect = ('gemini-3',)
    expect_usage = ('google.image.', 'google.web_search.gemini3')

    def parse_usage_prices(self, body):
        from .usage import google_usage_prices

        return google_usage_prices(body, self.url)

    _AMOUNT = re.compile(r'\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)')
    # '$2.00 through November 30, 2026. $4.00 starting December 1, 2026.'
    # Every Gemini row is currently priced that way: the whole lineup is on a
    # discount with a published end date, and the second amount is what the
    # rate becomes. A modal qualifier may sit between the amount and its date
    # ('$0.50 (text) through ...'), and the figures that follow a /-unit
    # ('$0.50 / 1,000,000 tokens per hour') are cache storage, not rates — the
    # pattern is anchored on 'through'/'starting' for that reason.
    _PROMO = re.compile(
        r'\$\s*(?P<now>[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:\([^)]*\)\s*)?'
        r'through\s+[A-Z][a-z]+\s+\d{1,2},?\s+\d{4}\.?\s*'
        r'\$\s*(?P<next>[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:\([^)]*\)\s*)?'
        r'starting\s+(?P<start>[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})'
    )
    # What follows the amount, not what appears anywhere in the cell: the
    # cache cell's '$0.15 $1.00 / 1,000,000 tokens per hour' is a token rate
    # whose SECOND figure is hourly storage, and it must keep parsing.
    _OTHER_UNIT = re.compile(
        r'^\s*\**\s*(?:/|per)\s+(?:[\d.,]+\s*k?\s*)?'
        r'(image|video|minute|second|hour|request|use|search)',
        re.IGNORECASE,
    )
    # 'per hour' / 'per month' anywhere in the text that FOLLOWS an amount is
    # storage, not a token rate — see `_token_rate`.
    _STORAGE = re.compile(r'\b(?:per|/)\s*(?:hour|day|month)\b', re.IGNORECASE)
    # '$2.00, prompts <= 200k tokens' / '$4.00, prompts > 200k tokens': the
    # prompt-size band, published inside the same cell as the headline rate.
    # The trailing 'tokens' is NOT required: the page writes 'prompts > 200k'
    # on the output cell and 'prompts > 200k tokens' on the input cell of the
    # same model, so demanding it silently records a band on one rate and not
    # the other.
    _BAND_AMOUNT = re.compile(
        r'\$\s*([\d,.]+)[^$]*?prompts\s*(<=|>)\s*([\d.]+)\s*k',
        re.IGNORECASE | re.DOTALL,
    )
    _START_DATE = re.compile(
        r'\b(?:starting|through)\s+[A-Z][a-z]+\s+\d{1,2},?\s+20\d{2}'
    )
    # Cells that state, in words or a dash, that there is no rate at all.
    _NO_RATE = ('-', '—', 'free', 'free of charge', 'not available', 'n/a')
    _METRICS = (
        ('input price', 'input'),
        ('output price', 'output'),
        ('context caching', 'cache_read'),
    )

    @classmethod
    def _metric(cls, label: str) -> str | None:
        for prefix, metric in cls._METRICS:
            if label.startswith(prefix):
                return metric
        return None

    def parse(self, body: str) -> dict[str, Price]:
        out: dict[str, Price] = {}
        for model_id, section in _md_sections(body):
            # A band in one metric's cell and an announced date in another is a
            # model whose FUTURE price cannot be stated without guessing:
            # `price_at` selects a scheduled row as a COMPLETE replacement, so
            # publishing one that omits the surcharge would bill the announced
            # price with no band at all. Raise — a red source is loud, a
            # silently shortened schedule is not.
            if self._BAND_AMOUNT.search(section) and self._START_DATE.search(section):
                raise ValueError(
                    f'{model_id}: a prompt-size band and an announced date in one '
                    'model — the announced rate cannot be published with its band'
                )
            cells = _md_paid_column(section)
            rates: dict[str, float | None] = {}
            above: int | None = None
            band_rates: dict[str, float | None] = {}
            announced: dict[str, dict[str, float]] = {}
            for label, cell in cells.items():
                metric = self._metric(label)
                if metric is None:
                    continue
                if cell.strip().lower().rstrip('.') in self._NO_RATE:
                    # Explicit: the vendor states there is no such rate here.
                    rates[metric] = None
                    continue
                bands = self._BAND_AMOUNT.findall(cell)
                if bands:
                    for amount, op, size in bands:
                        limit = int(float(size.replace(',', '')) * 1_000)
                        if op == '<=':
                            rates[metric] = float(amount.replace(',', ''))
                            above = limit
                        else:
                            band_rates[metric] = float(amount.replace(',', ''))
                            above = limit
                    if metric not in rates:
                        raise ValueError(f'{model_id}: {label} states only the band rate')
                else:
                    rate = self._token_rate(cell)
                    if rate is None and self._STORAGE.search(cell):
                        # Storage priced, token rate not published — a real
                        # state on this page ('$0.50 / 1,000,000 tokens per
                        # hour (storage price)'). Nothing to record here, and
                        # nothing missing either.
                        rates[metric] = None
                        continue
                    if rate is None and metric == 'cache_read':
                        # Present but unparseable, and this field is a HOLE in
                        # a row we would otherwise emit: `None` there means
                        # "the vendor publishes no cache rate", so recording it
                        # would overwrite a published one on the next refresh.
                        # Raise instead — the source goes red and the book keeps
                        # its last good row.
                        raise ValueError(f'{model_id}: unparseable {label}: {cell[:80]!r}')
                    # An unparseable input/output means the model has no token
                    # price at all (`gemini-2.5-flash-image` publishes
                    # '$0.039 per image' and no per-token output), which is an
                    # absence of the model, not of a field: it is skipped
                    # below, exactly as it was before this parser existed.
                    rates[metric] = rate
                for start, future in self._announced(cell):
                    announced.setdefault(start, {})[metric] = future
            inp, outp = rates.get('input'), rates.get('output')
            if inp is None or outp is None:
                continue
            price = Price(
                input=inp,
                output=outp,
                cache_read=rates.get('cache_read'),
                scheduled=[
                    Price(
                        input=row['input'],
                        output=row['output'],
                        cache_read=row.get('cache_read'),
                        effective_from=start,
                    )
                    for start, row in sorted(announced.items())
                    # Without both token rates there is nothing to charge
                    # against: half an announced row is not a price.
                    if 'input' in row and 'output' in row
                ]
                or None,
            )
            if above is not None:
                if 'input' not in band_rates or 'output' not in band_rates:
                    raise ValueError(
                        f'{model_id}: band covers only {sorted(band_rates)} — a band '
                        'without both token rates is not a price'
                    )
                price = price.model_copy(
                    update={
                        'long_context_above': above,
                        'long_context': Price(
                            input=band_rates['input'],
                            output=band_rates['output'],
                            cache_read=band_rates.get('cache_read'),
                        ),
                    }
                )
            out[model_id] = price
        return out


    def _token_rate(self, cell: str) -> float | None:
        """The cell's first amount, unless it is priced in something else.

        Two disqualifiers, and both are scoped to the amount they qualify
        rather than to the cell: a cell can hold several amounts, each with its
        own unit.

        1. A unit word at the amount's own slash or `per` — `$0.039 per image`,
           `Equivalent to $0.0336 per 1K image`. Reading one of those as a
           per-1M-token rate is a 64x-class error, not a 1e6 one, so the write
           guard never sees it.
        2. Hourly storage, looked for only up to the NEXT amount.
           `$0.15 $1.00 / 1,000,000 tokens per hour` is a cache-read rate
           followed by a storage rate and must yield 0.15, while
           `$0.50 / 1,000,000 tokens per hour` — or the same cell repeated for
           a later date — is storage alone and must yield nothing. A cell-wide
           search cannot tell those apart; the next-amount bound can.
        """
        m = self._AMOUNT.search(cell)
        if m is None:
            return None
        rest = cell[m.end() :]
        if self._OTHER_UNIT.match(rest):
            return None
        following = rest.find('$')
        if self._STORAGE.search(rest if following < 0 else rest[:following]):
            return None
        return float(m.group(1).replace(',', ''))

    def _announced(self, cell: str) -> list[tuple[str, float]]:
        """(start date, rate) for each published 'through X … starting Y'."""
        found: list[tuple[str, float]] = []
        for m in self._PROMO.finditer(cell):
            start = _iso_date(m.group('start'))
            if start is not None:
                found.append((start, float(m.group('next').replace(',', ''))))
        return found


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

    # `\s*` before each paren, not a literal space: the page renders the
    # qualifier as a nested element, so `text(strip=True)` concatenates it as
    # '1M INPUT TOKENS(CACHE HIT)'. Requiring the space made every metric row
    # miss, which parsed 0 models and froze the DeepSeek rows in the book at
    # whatever they last were — a source that reports BROKEN, so not silent,
    # but the fixture had the space and nothing else caught it.
    _METRIC = re.compile(
        r'1M INPUT TOKENS\s*\(CACHE HIT\)'
        r'|1M INPUT TOKENS\s*\(CACHE MISS\)'
        r'|1M OUTPUT TOKENS'
    )
    # Group 1 is the time ranges, group 2 the day qualifier that follows the
    # zone — 'Monday through Friday' rode there unread until 2026-09-03, which
    # billed every weekend morning at the peak rate. Group 2 stops at the '('
    # of the parenthetical or the sentence's full stop.
    _WINDOW_TEXT = re.compile(
        r'peak hours are\s+(.*?)\s*utc\b\s*,?\s*([^(.]*)', re.IGNORECASE
    )
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
                # Canonicalise to the spacing `_HIT`/`_MISS`/`_OUT` use, so the
                # two renderings of the same metric ('… TOKENS (CACHE HIT)' and
                # '… TOKENS(CACHE HIT)') land in one bucket rather than two,
                # one of which nothing ever reads.
                cur = re.sub(r'\s*\(', ' (', m.group(0))
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
        """The footnote's 'Peak hours are … UTC[, <days>]' sentence, as
        TimeWindows.

        A day qualifier this parser cannot read drops the windows entirely,
        and with them the peak variant. That looks drastic next to defaulting
        to every day, but the two failures are not symmetric: recording an
        unrestricted window when the vendor published a restricted one is a
        false fact that silently doubles a consumer's weekend bill, while
        recording no window is an absence the book already models and every
        consumer already handles. It is also loud — the peak variant
        disappearing surfaces as a CHANGED delta for a human to read before
        `refresh --write` lands it.
        """
        m = self._WINDOW_TEXT.search(text)
        if m is None:
            return None
        pairs = self._TIME_RANGE.findall(m.group(1))
        if not pairs:
            return None
        try:
            days = _weekdays(m.group(2))
        except ValueError:
            return None
        return [TimeWindow(start=s, end=e, days=days) for s, e in pairs]


# ISO weekday numbers, keyed by the three letters every abbreviation of a day
# name shares ('tue'/'tues'/'tuesday'). 'through' is normalised to a dash
# before this map is consulted, so it never collides with 'thu'.
_DAY_PREFIX = {
    'mon': 1,
    'tue': 2,
    'wed': 3,
    'thu': 4,
    'fri': 5,
    'sat': 6,
    'sun': 7,
}
_EVERY_DAY = ('every day', 'all days', 'all week', 'daily', '7 days')
_RANGE_WORDS = re.compile(r'\bthrough\b|\bthru\b|\bto\b|[\u2013\u2014]')


def _day_number(word: str) -> int | None:
    letters = re.sub(r'[^a-z]', '', word.lower())
    return _DAY_PREFIX.get(letters[:3]) if len(letters) >= 3 else None


def _day_span(first: int, last: int) -> list[int]:
    """Inclusive ISO-weekday span, wrapping through Sunday: Fri-Mon is
    5,6,7,1. A vendor writing a span that crosses the week boundary means the
    days between them the long way round, not an empty set."""
    out, day = [first], first
    while day != last:
        day = 1 if day == 7 else day + 1
        out.append(day)
    return out


def _weekdays(text: str) -> list[int] | None:
    """The day restriction in a vendor's window sentence, as ISO weekdays.

    `None` means unrestricted — an empty qualifier, or an explicit 'every
    day'. Raises ValueError when a qualifier is present but unreadable, which
    the caller turns into a dropped window rather than a silent 7-day one.
    """
    t = text.strip().lower()
    if not t:
        return None
    if any(phrase in t for phrase in _EVERY_DAY):
        return None
    t = _RANGE_WORDS.sub('-', t).replace(' and ', ',')
    days: set[int] = set()
    for part in re.split(r'[,/]', t):
        part = part.strip(' -')
        if not part:
            continue
        if 'weekday' in part:
            days.update((1, 2, 3, 4, 5))
            continue
        if 'weekend' in part:
            days.update((6, 7))
            continue
        ends = [_day_number(x) for x in part.split('-') if x.strip()]
        if not ends or len(ends) > 2 or any(d is None for d in ends):
            raise ValueError(f'unreadable day qualifier: {text!r}')
        days.update(ends if len(ends) == 1 else _day_span(*ends))
    if not days:
        raise ValueError(f'unreadable day qualifier: {text!r}')
    return sorted(days)


_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            'january',
            'february',
            'march',
            'april',
            'may',
            'june',
            'july',
            'august',
            'september',
            'october',
            'november',
            'december',
        ),
        1,
    )
}


def _iso_date(text: str) -> str | None:
    """'November 30, 2026' -> '2026-11-30'; None when it is not a real date.

    A month-name table rather than `strptime('%B')`, which is locale-dependent:
    a parser must not start returning None because `LANG` moved.
    """
    m = re.fullmatch(r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})', text.strip())
    if m is None:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if month is None:
        return None
    try:
        return date(int(m.group(3)), month, int(m.group(2))).isoformat()
    except ValueError:
        return None


def _column_index(header: list[str], *needles: str) -> int | None:
    """First column whose header contains every needle (in order)."""
    for i, h in enumerate(header):
        if all(n in h for n in needles):
            return i
    return None


def _pipe_tables(body: str) -> Iterator[tuple[str, list[list[str]]]]:
    """Every markdown pipe table in `body`, with the prose line above it.

    The caption is the last non-empty non-table line before the table, which
    on these docs pages is where the unit lives ('Prices per 1M tokens.',
    'Prices per image.'). Separator rows are dropped; the first row yielded is
    the header.
    """
    caption = ''
    rows: list[list[str]] = []
    for line in [*body.splitlines(), '']:  # sentinel flushes a trailing table
        s = line.strip()
        if s.startswith('|'):
            cells = [c.strip() for c in s.strip('|').split('|')]
            if not all(set(c) <= {'-', ':'} for c in cells):
                rows.append(cells)
            continue
        if rows:
            yield caption, rows
            rows = []
        if s:
            caption = s


def _jsx_groups(text: str) -> list[str]:
    """Inner text of every TOP-LEVEL `[...]` group in `text`.

    A table's rows arrive as ONE array of arrays (`rows:[[...],[...]]`), so this
    yields each row text for `_split_mdx_cells`. Depth- and quote-aware for the
    same reason that splitter is: a row holds `_jsx(...)` calls with brace
    arguments and template strings, and a naive regex stops at the first `]`
    inside one of them.
    """
    groups: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != '[':
            i += 1
            continue
        depth, quote, j = 0, None, i
        while j < n:
            c = text[j]
            if quote:
                if c == quote:
                    quote = None
            elif c in '`"\'':
                quote = c
            elif c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
                if depth == 0:
                    groups.append(text[i + 1 : j])
                    break
            j += 1
        i = j + 1
    return groups


def _split_mdx_cells(row_text: str) -> list[str]:
    """Split one table row into cells on TOP-LEVEL commas.

    A cell is a quoted/template string, a `<>…</>` fragment or an `_jsx(…)`
    call, so a naive `.split(',')` breaks on the comma inside "1,048,576 tokens"
    and on the one between an `_jsx` call's arguments. Brackets, braces and
    quotes all nest, so the split tracks depth and quote state. String cells
    come back unquoted.
    """
    cells: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None

    def push() -> None:
        cell = ''.join(buf).strip()
        if len(cell) >= 2 and cell[0] in '"\'`' and cell[-1] == cell[0]:
            cell = cell[1:-1]
        cells.append(cell)

    for c in row_text:
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            continue
        if c in '"\'`':
            quote = c
            buf.append(c)
            continue
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif c == ',' and depth == 0:
            push()
            buf = []
            continue
        buf.append(c)
    if buf:
        push()
    return cells


class MoonshotSource(Source):
    """Kimi token pricing on platform.kimi.ai — read from the RENDERED page.

    The forum.kimi.com announcement this source once read is DNS-dead, and the
    pricing table lives on platform.kimi.ai, a Mintlify site. Its markdown
    rendering (`.md`) used to carry the table as a server-side `<DocTable>` MDX
    block. Since the K3.1 rewrite that rendering ships `<table><tbody /></table>`
    — the rows are gone from the markdown entirely, so `check` reported "parsed
    0 models" for a page that renders fine in a browser.

    What the browser actually gets is the page HTML, and its Next.js RSC payload
    still carries the table as DATA:

        _jsx(DocTable,{...columns:[{title:`Model`},...],rows:[[...],[...]]})

    with every cell a backticked string (`$2.00 / MTok`). So this source reads
    the HTML and walks those two arrays. The rendered `<table>` in the body is
    the same data after hydration — it is not parsed, and cannot be: the raw
    HTML has zero <table> elements.

    Two table shapes, one per generation, both on the page today:
      K3   Model | Input | Output | Cached Input | 5m writes | 1h writes
      K2   Model | Input | Output | Cached Input
    'Input' and 'Cached Input' are ADJACENT, so the cache column is excluded BY
    NAME when picking the input column: a plain substring match bills every
    fresh token at the cache-hit rate, a silent 10x under-billing. Of the two
    write tiers only the 5-minute one is recorded — the page says that is what
    applies when no TTL is requested, and the book's `cache_write` is one
    number.

    The Chinese platform lists CNY rates; the USD table here is the rate
    OpenRouter-facing consumers pay, and the book's unit is USD. Never convert
    the CNY page through a live exchange rate.
    """

    name = 'moonshot'
    url = 'https://platform.kimi.ai/docs/pricing/chat'
    accept = 'text/html,*/*'
    # `kimi-k3.1-pro` is the flagship since 2026-10-16. A page that parses rows
    # but lacks it has lost the K3 table again — the exact failure the previous
    # selector produced, and one no row count would have caught.
    expect = ('kimi-k3.1-pro', 'kimi-k3')

    # One `_jsx(DocTable,{...})` call ends at `]]})` — the rows array closing
    # immediately before the component call does. Nothing inside a row can
    # produce that sequence: cells end in a backtick or `})`, never `]})`.
    _TABLE = re.compile(r'_jsx\(DocTable,\{(.*?)\]\}\)', re.DOTALL)
    _COLUMNS = re.compile(r'columns:\[(.*?)\],rows:\[', re.DOTALL)
    # Every column object closes with ,width:`15%`} — the one brace-terminated
    # field after the label, and the split point that keeps a Tooltip's tip
    # text in the same chunk as the label it wraps.
    _WIDTH = re.compile(r',width:`[^`]*`\}')
    _LABEL = re.compile(r'(?:title|children):`([^`]+)`')
    _NAME = re.compile(r'children:`([^`]+)`')


    def parse(self, body: str) -> dict[str, Price]:
        out: dict[str, Price] = {}
        for block in self._TABLE.findall(body):
            m = self._COLUMNS.search(block)
            if m is None:
                continue
            header = [
                labels[-1].strip().lower()
                for labels in (
                    self._LABEL.findall(chunk)
                    for chunk in self._WIDTH.split(m.group(1))
                )
                if labels
            ]
            c_model = self._col(header, 'model')
            c_out = self._col(header, 'output')
            c_in = self._col(header, 'input', without=('cache',))
            if c_model is None or c_in is None or c_out is None:
                continue
            c_hit = self._col(header, 'cached', 'input')
            c_write = self._col(header, '5m', 'write')

            for row in _jsx_groups(block[m.end() :]):
                cells = _split_mdx_cells(row)
                if not cells:
                    continue
                names = self._NAME.findall(cells[0])
                if not names:
                    continue
                model_id = names[-1].strip().lower()
                rates = [self._money(cell) for cell in cells]
                inp, outp = rates[c_in], rates[c_out]
                if inp is None or outp is None:
                    continue
                out[model_id] = Price(
                    input=inp,
                    output=outp,
                    cache_read=(
                        rates[c_hit]
                        if c_hit is not None and c_hit < len(rates)
                        else None
                    ),
                    cache_write=(
                        rates[c_write]
                        if c_write is not None and c_write < len(rates)
                        else None
                    ),
                )
        return out

    @staticmethod
    def _col(
        header: list[str], *needles: str, without: tuple[str, ...] = ()
    ) -> int | None:
        """First column whose header contains every needle and no `without`.

        `_column_index` cannot express 'input, but not cached input', and in the
        K3 table the two are adjacent.
        """
        for i, h in enumerate(header):
            if all(n in h for n in needles) and not any(w in h for w in without):
                return i
        return None

    def _money(self, cell: str) -> float | None:
        return dollars(cell.strip())



class ZaiSource(Source):
    """Z.ai pricing — the MARKDOWN rendering of the dedicated pricing page.

    The API-reference introduction page that used to carry the USD table no
    longer does; pricing moved to its own page. The docs site serves source
    markdown at <url>.md, where the price tables are plain pipe tables. The
    HTML page is client-rendered, which is exactly what broke the old source
    (fetched, parsed 0 models).

    Every per-1M-token table is read, not one named section. Anchoring on
    `### Text Models` is what broke on 2026-09-04: z.ai added a `### Latest
    Models` table above it and moved GLM-5.2 and GLM-5.3 into it, so the
    parser returned ten rows of superseded models and not one flagship. A
    section title is a label the vendor is free to rename; the table's own
    header is the structure. So a table qualifies on shape — model, input and
    output columns under a 'per 1M tokens' caption — which picks up a new
    section on the day it appears and still rejects the per-image, per-video
    and per-use tables further down the page.
    """

    name = 'zai'
    url = 'https://docs.z.ai/guides/overview/pricing.md'
    accept = 'text/markdown,text/plain,*/*'
    expect = ('glm-5',)

    _PER_MTOK = re.compile(r'per\s+1M\s+tokens', re.IGNORECASE)
    # `~~\$0.15~~ \$0.075` — the struck-through list price of a model on
    # promotion. `dollars` takes the first amount in the cell, so leaving the
    # strikethrough in records $0.15 for a model z.ai is charging $0.075 for.
    # The book records the rate in force, the same call AnthropicSource makes
    # for a dated introductory rate.
    _STRUCK = re.compile(r'~~.*?~~')

    def parse(self, body: str) -> dict[str, Price]:
        out: dict[str, Price] = {}
        for caption, rows in _pipe_tables(body):
            if not self._PER_MTOK.search(caption):
                continue
            header = [c.lower() for c in rows[0]]
            # First-match order is load-bearing: 'input' hits the plain Input
            # column before Cached Input, and 'cached' hits Cached Input
            # before Cached Input Storage.
            c_model = _column_index(header, 'model')
            c_in = _column_index(header, 'input')
            c_cached = _column_index(header, 'cached')
            c_out = _column_index(header, 'output')
            if c_model is None or c_in is None or c_out is None:
                continue

            for r in rows[1:]:
                if len(r) <= max(c_model, c_in, c_out):
                    continue
                model_id = r[c_model].strip().lower()
                if not model_id.startswith('glm-'):
                    continue
                inp, outp = self._money(r[c_in]), self._money(r[c_out])
                if inp is None or outp is None:
                    continue
                # setdefault, not assignment: a model listed in both Latest
                # Models and Text Models keeps the earlier row, and the page
                # leads with the current one.
                out.setdefault(
                    model_id,
                    Price(
                        input=inp,
                        output=outp,
                        cache_read=(
                            self._money(r[c_cached])
                            if c_cached is not None and c_cached < len(r)
                            else None
                        ),
                    ),
                )
        return out

    def _money(self, cell: str) -> float | None:
        return dollars(self._STRUCK.sub('', cell))


# Regolo quotes EUR per 1M tokens and the book is USD, so the conversion has to
# happen at the parse edge — a rate recorded in the wrong currency is a wrong
# rate, not a convertible one. 1.10 is the multiplier bebri-chat's hand-written
# rows used on 2026-07-22, and keeping the two identical is the point: the day
# this constant and the vendor's euro price disagree with those rows is the day
# somebody looks. Revise it when EUR/USD drifts more than a couple of percent.
EUR_USD = 1.10

_EUR_AMOUNT = re.compile(r'€\s*([0-9][0-9,]*(?:\.[0-9]+)?)')


def _euros(cell: str) -> float | None:
    m = _EUR_AMOUNT.search(cell)
    return float(m.group(1).replace(',', '')) if m else None


def _children(node) -> list:
    """A node's element children, in order.

    selectolax offers `css('> div')` only through its lexbor engine and raises
    `Bad CSS Selectors` on modest, so the walk is explicit.
    """
    out = []
    child = node.child
    while child is not None:
        out.append(child)
        child = child.next
    return out


class GroqSource(Source):
    """Groq's model table on console.groq.com — NOT groq.com/pricing.

    The marketing pricing page is a client-rendered Next.js shell: 55KB of
    markup, no model ids, the table fetched after mount. It has no scrapable
    payload and never will, so this source reads the Supported Models page
    instead, which renders the same rates server-side, one `<tr>` per model and
    the rate in a `$0.15` span carrying a nested `input`/`output` chip.

    Two traps, both live today:

    * Rows priced in something other than tokens — Whisper `$0.111 per hour`,
      the Orpheus voices `$40 per 1M characters` — have no input/output chip
      and are dropped. Booking an hourly rate as a per-Mtok rate would make an
      audio model look like the cheapest text model on the menu.
    * A row whose price is negotiated (`Contact Sales`, which is what the
      Llama enterprise rows say) has no amount either, so it is dropped rather
      than recorded as free. Those ids keep whatever the book already had.

    Only the id and the two token rates are read. Context windows, speeds and
    rate limits are on the same row and are somebody else's data.
    """

    name = 'groq'
    url = 'https://console.groq.com/docs/models'
    accept = 'text/html,*/*'
    expect = ('openai/gpt-oss-120b', 'openai/gpt-oss-20b')

    def parse(self, body: str) -> dict[str, Price]:
        tree = HTMLParser(body)
        out: dict[str, Price] = {}
        for tr in tree.css('tr'):
            node = tr.css_first('div[id]')
            if node is None:
                continue
            model_id = (node.attributes.get('id') or '').strip()
            if not model_id:
                continue
            rates: dict[str, float] = {}
            # The chip (`input` / `output`) is the INNER span and the amount is
            # its parent's text. selectolax's `css_first` matches the node
            # itself before its descendants, so asking a price span for 'span'
            # hands back the price span — find the chips first, then walk up.
            for chip in tr.css('span'):
                unit = chip.text(strip=True).lower()
                if unit not in ('input', 'output'):
                    continue
                amount = dollars(
                    chip.parent.text(strip=True) if chip.parent else unit
                )
                if amount is not None:
                    rates[unit] = amount
            if 'input' in rates and 'output' in rates:
                out[model_id] = Price(
                    input=rates['input'], output=rates['output']
                )
        return out


class RegoloSource(Source):
    """Regolo's pay-as-you-go table — CSS grid rows in EUR, no cache column.

    The table is not a `<table>`. Each row is a `div` whose inline style declares
    `grid-template-columns: repeat(4, …)` and whose four children are: model id,
    euro input, euro output, and a plan-availability cell that reads
    `Included` / `Core · Boost · Pro · Scale`.

    That fourth cell is the trap: 'Included' means the model is in every
    subscription plan, NOT that cache reads are free. Reading it as a cache
    price would book 0.00 cache reads for every Regolo model — free input for
    any session that caches.

    Ids are the vendor's own (`glm5.2`, `qwen3.5-122b`), which is what bebri-chat
    resolves against; a row the vendor renames therefore drops out of the book
    instead of quietly keeping the old id's numbers.
    """

    name = 'regolo'
    url = 'https://regolo.ai/pricing'
    accept = 'text/html,*/*'
    expect = ('gpt-oss-120b', 'qwen3.5-122b')

    _ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]*')

    def parse(self, body: str) -> dict[str, Price]:
        tree = HTMLParser(body)
        out: dict[str, Price] = {}
        for row in tree.css('div'):
            style = row.attributes.get('style') or ''
            if 'grid-template-columns' not in style or 'repeat(4' not in style:
                continue
            # Direct children only: `row.css('div')` matches the row itself
            # first, which turns the first cell into the whole row's text.
            cells = [
                child.text(strip=True)
                for child in _children(row)
                if child.tag == 'div'
            ]
            if len(cells) != 4:
                continue
            model_id, in_cell, out_cell, _plans = cells
            if not self._ID.fullmatch(model_id):
                continue
            inp, outp = _euros(in_cell), _euros(out_cell)
            if inp is None or outp is None:
                continue
            out[model_id] = Price(
                input=round(inp * EUR_USD, 4), output=round(outp * EUR_USD, 4)
            )
        return out

