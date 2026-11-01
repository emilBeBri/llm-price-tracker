"""Parser tests against small fixtures shaped like the real pages.

No network. Each fixture reproduces the exact structural trap that broke a
parser in practice, so the test fails if someone "simplifies" the fix away.
"""

from datetime import UTC, datetime

import pytest

from llm_price_tracker.models import Price
from llm_price_tracker.sources import (
    SOURCES,
    AnthropicSource,
    DeepSeekSource,
    GoogleSource,
    GroqSource,
    LlmPricesSource,
    MoonshotSource,
    OpenAISource,
    OpenRouterSource,
    RegoloSource,
    ZaiSource,
)
from llm_price_tracker.sources.base import Source, SourceResult, dollars
from llm_price_tracker.sources.vendors import EUR_USD, _weekdays


def test_dollars_takes_the_first_amount():
    assert dollars('$0.075 (text/image)') == 0.075
    assert dollars('$1,250.00') == 1250.0
    assert dollars('—') is None


# --------------------------------------------------------------------------- #
# OpenAI: the 'Cache writes' column that shifted 'Output' out from under a
# hardcoded index, plus the Standard/Batch/Flex/Fast sections that share a header.
# --------------------------------------------------------------------------- #
OPENAI_MD = """# Pricing

### Standard pricing data

| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context output |
| --- | --- | --- | --- | --- | --- | --- |
| gpt-5.6-luna | $0.20 | $0.02 | $0.25 | $1.20 | $0.40 | $1.80 |
| gpt-5.5 (<272K context length) | $5.00 | $0.50 | - | $30.00 | $10.00 | $45.00 |
| o3 | $2.00 | $0.50 | - | $8.00 | - | - |

### Batch pricing data

| Model | Short context input | Short context cached input | Short context cache writes | Short context output |
| --- | --- | --- | --- | --- |
| gpt-5.6-luna | $0.10 | $0.01 | $0.125 | $0.60 |

Cyber models

### Grouped Pricing Table data

| Model | Short context input | Short context cached input | Short context cache writes | Short context output |
| --- | --- | --- | --- | --- |
| gpt-5.6-cyber | $12.50 | $1.25 | $15.625 | $75.00 |

Image generation models

Prices per 1M tokens.

Standard

      For image generation cost estimates, use the calculator in the image generation guide.

### Grouped Pricing Table data

| Model | Modality | Input | Cached input | Output |
| --- | --- | --- | --- | --- |
| gpt-image-2.5-sunburst | Image | $8.00 | $2.00 | $30.00 |
| gpt-image-2.5-sunburst | Text | $5.00 | $1.25 | - |
| gpt-image-1-mini | Image | $2.50 | $0.25 | $8.00 |
| gpt-image-1-mini | Text | $2.00 | $0.20 | - |

Batch

### Grouped Pricing Table data

| Model | Modality | Input | Cached input | Output |
| --- | --- | --- | --- | --- |
| gpt-image-2.5-sunburst | Image | $4.00 | $1.00 | $15.00 |
| gpt-image-2.5-sunburst | Text | $2.50 | $0.625 | - |
"""


def test_openai_reads_output_not_the_cache_writes_column():
    prices = OpenAISource().parse(OPENAI_MD)
    luna = prices['gpt-5.6-luna']
    assert luna.output == 1.20, 'output must not be the $0.25 cache-writes cell'
    assert luna.cache_write == 0.25
    assert luna.cache_read == 0.02


def test_openai_takes_standard_not_batch():
    assert OpenAISource().parse(OPENAI_MD)['gpt-5.6-luna'].input == 0.20


def test_openai_strips_context_suffix_from_model_names():
    assert 'gpt-5.5' in OpenAISource().parse(OPENAI_MD)


def test_openai_covers_the_o_series():
    """The o-series is absent from the server-rendered HTML; markdown has it."""
    assert OpenAISource().parse(OPENAI_MD)['o3'].input == 2.0


def test_openai_without_the_standard_heading_parses_nothing():
    assert OpenAISource().parse('# Pricing\n\nno tables here') == {}


def test_openai_image_models_come_from_the_image_row_not_the_text_row():
    """Both rows share a header; only the Image one has an output price."""
    sunburst = OpenAISource().parse(OPENAI_MD)['gpt-image-2.5-sunburst']
    assert (sunburst.input, sunburst.cache_read, sunburst.output) == (8.0, 2.0, 30.0)


def test_openai_image_models_take_standard_not_batch():
    assert OpenAISource().parse(OPENAI_MD)['gpt-image-2.5-sunburst'].input != 4.0


def test_openai_image_section_does_not_grab_the_cyber_grouped_table():
    """'### Grouped Pricing Table data' is reused across the page; the cyber
    table sits above the image section and shares the heading."""
    prices = OpenAISource().parse(OPENAI_MD)
    assert 'gpt-image-1-mini' in prices
    assert prices['gpt-image-1-mini'].output == 8.0


def test_openai_image_section_missing_leaves_the_text_lineup_intact():
    text_only = OPENAI_MD.split('Image generation models')[0]
    prices = OpenAISource().parse(text_only)
    assert prices['gpt-5.6-luna'].input == 0.20
    assert not [m for m in prices if 'image' in m]


def test_openai_image_table_behind_another_tier_tab_is_refused():
    """A reordering that puts Batch first must yield nothing, not batch rates."""
    swapped = OPENAI_MD.replace('\nStandard\n', '\nFlex\n', 1)
    assert not [m for m in OpenAISource().parse(swapped) if 'image' in m]


def test_openai_image_renamed_column_yields_no_rows():
    renamed = OPENAI_MD.replace(
        '| Model | Modality | Input | Cached input | Output |',
        '| Model | Modality | In | Cached input | Output |',
    )
    assert not [m for m in OpenAISource().parse(renamed) if 'image' in m]


# --------------------------------------------------------------------------- #
# Anthropic: a model with a dated introductory rate appears TWICE.
# --------------------------------------------------------------------------- #
ANTHROPIC_MD = """
| Model | Base Input | 5m Cache Writes | 1h Cache Writes | Cache Hits | Output |
| --- | --- | --- | --- | --- | --- |
| Claude Opus 5 | $5 / MTok | $6.25 / MTok | $10 / MTok | $0.50 / MTok | $25 / MTok |
| Claude Sonnet 5 [through August 31, 2026](/x) | $2 / MTok | $2.50 / MTok | $4 / MTok | $0.20 / MTok | $10 / MTok |
| Claude Sonnet 5 starting September 1, 2026 | $3 / MTok | $3.75 / MTok | $6 / MTok | $0.30 / MTok | $15 / MTok |
"""


def test_anthropic_takes_the_rate_in_force_not_the_future_one():
    prices = AnthropicSource().parse(ANTHROPIC_MD)
    assert prices['claude-sonnet-5'].input == 2.0
    assert prices['claude-sonnet-5'].output == 10.0
    assert prices['claude-sonnet-5'].cache_read == 0.20


def test_anthropic_records_the_scheduled_row_it_used_to_drop():
    """The page lists a dated rate cut twice: the row in force now and the row
    that replaces it. The second is a vendor fact, and dropping it is why the
    increase arrived as a surprise instead of a dated row."""
    sonnet = AnthropicSource().parse(ANTHROPIC_MD)['claude-sonnet-5']
    assert (sonnet.input, sonnet.output) == (2.0, 10.0)
    assert [
        (p.input, p.output, p.cache_read, p.effective_from) for p in sonnet.scheduled
    ] == [(3.0, 15.0, 0.30, '2026-09-01')]


def test_anthropic_maps_the_cache_columns():
    opus = AnthropicSource().parse(ANTHROPIC_MD)['claude-opus-5']
    assert (opus.input, opus.output) == (5.0, 25.0)
    assert opus.cache_read == 0.50 and opus.cache_write == 6.25


# --------------------------------------------------------------------------- #
# DeepSeek: a transposed table, models as columns, prices as trailing cells.
# Since the peak-pricing activation each metric splits into OFF-PEAK/PEAK
# sub-rows; the FEATURES 'Json Output' row must not read as the output row,
# and the peak-hour windows come from the footnote, never hardcoded.
# --------------------------------------------------------------------------- #
DEEPSEEK_HTML = """
<table>
<tr><td>MODEL</td><td>deepseek-v4-flash</td><td>deepseek-v4-pro</td></tr>
<tr><td>FEATURES</td><td>Json Output</td><td>✓</td><td>✓</td></tr>
<tr><td>PRICING(1)</td><td>1M INPUT TOKENS (CACHE HIT)</td><td>OFF-PEAK</td><td>$0.007</td><td>$0.022</td></tr>
<tr><td>PEAK</td><td>$0.014</td><td>$0.044</td></tr>
<tr><td>1M INPUT TOKENS (CACHE MISS)</td><td>OFF-PEAK</td><td>$0.22</td><td>$0.66</td></tr>
<tr><td>PEAK</td><td>$0.44</td><td>$1.32</td></tr>
<tr><td>1M OUTPUT TOKENS</td><td>OFF-PEAK</td><td>$0.66</td><td>$1.98</td></tr>
<tr><td>PEAK</td><td>$1.32</td><td>$3.96</td></tr>
</table>
<p>(1) Off-peak rates are half of the peak rates. Peak hours are 01:00 - 04:00 and 06:00 - 10:00 UTC (all other hours are off-peak).</p>
"""

# The CURRENT footnote. The vendor added ', Monday through Friday' after the
# zone at some point before 2026-09-03 and the parser read straight past it,
# so the book claimed peak rates on weekends. Kept as a separate fixture
# because DEEPSEEK_HTML above is a real earlier state of the same page and
# still pins the unqualified path.
DEEPSEEK_HTML_WEEKDAYS = DEEPSEEK_HTML.replace(
    '10:00 UTC (all other hours',
    '10:00 UTC, Monday through Friday (all other hours',
)

# The pre-activation table: one row per metric, no period marker. Must keep
# parsing — a source that dies when the vendor REVERTS a policy is as bad as
# one that dies when the policy changes.
DEEPSEEK_HTML_NO_PEAK = """
<table>
<tr><td>MODEL</td><td>deepseek-v4-flash</td><td>deepseek-v4-pro</td></tr>
<tr><td>PRICING</td><td>1M INPUT TOKENS (CACHE HIT)</td><td>$0.0028</td><td>$0.003625</td></tr>
<tr><td>1M INPUT TOKENS (CACHE MISS)</td><td>$0.14</td><td>$0.435</td></tr>
<tr><td>1M OUTPUT TOKENS</td><td>$0.28</td><td>$0.87</td></tr>
</table>
"""


def test_deepseek_transposed_table_and_steep_cache_discount():
    prices = DeepSeekSource().parse(DEEPSEEK_HTML)
    pro = prices['deepseek-v4-pro']
    assert (pro.input, pro.output) == (0.66, 1.98)
    assert pro.cache_read == 0.022
    assert prices['deepseek-v4-flash'].cache_read == 0.007


def test_deepseek_off_peak_is_the_scalar_and_peak_is_the_variant():
    flash = DeepSeekSource().parse(DEEPSEEK_HTML)['deepseek-v4-flash']
    assert (flash.input, flash.output) == (0.22, 0.66)  # off-peak headline rate
    assert (flash.peak.input, flash.peak.output, flash.peak.cache_read) == (
        0.44,
        1.32,
        0.014,
    )


def test_deepseek_peak_windows_come_from_the_footnote():
    flash = DeepSeekSource().parse(DEEPSEEK_HTML)['deepseek-v4-flash']
    assert [(w.start, w.end) for w in flash.peak_windows] == [
        ('01:00', '04:00'),
        ('06:00', '10:00'),
    ]


def test_deepseek_metric_label_without_a_space_before_the_paren():
    """The live page renders the qualifier as a nested element, so
    `text(strip=True)` yields '1M INPUT TOKENS(CACHE HIT)'. Requiring the
    space made every metric row miss and the source parsed 0 models — for
    long enough that the book's DeepSeek rows froze where they were."""
    html = DEEPSEEK_HTML_WEEKDAYS.replace('TOKENS (CACHE', 'TOKENS(CACHE')
    flash = DeepSeekSource().parse(html)['deepseek-v4-flash']
    assert (flash.input, flash.output, flash.cache_read) == (0.22, 0.66, 0.007)
    assert flash.peak.cache_read == 0.014  # both spellings land in one bucket


def test_deepseek_weekday_qualifier_is_read_off_the_footnote():
    """The regression this field exists for: 'Monday through Friday' rode
    unread after the zone, and every weekend morning billed at 2x."""
    flash = DeepSeekSource().parse(DEEPSEEK_HTML_WEEKDAYS)['deepseek-v4-flash']
    assert [w.days for w in flash.peak_windows] == [[1, 2, 3, 4, 5]] * 2


def test_deepseek_weekend_is_off_peak_under_the_current_footnote():
    """End to end through `for_time`, which is what a consumer actually calls."""
    flash = DeepSeekSource().parse(DEEPSEEK_HTML_WEEKDAYS)['deepseek-v4-flash']
    friday = datetime(2026, 9, 4, 7, 0, tzinfo=UTC)
    saturday = datetime(2026, 9, 5, 7, 0, tzinfo=UTC)
    assert flash.for_time(friday).input == 0.44
    assert flash.for_time(saturday).input == 0.22


def test_deepseek_footnote_without_a_day_qualifier_stays_unrestricted():
    """`None`, not a synthesised 1-7 list: the earlier page said nothing about
    days and the book must not claim it did."""
    flash = DeepSeekSource().parse(DEEPSEEK_HTML)['deepseek-v4-flash']
    assert [w.days for w in flash.peak_windows] == [None, None]


def test_deepseek_an_unreadable_day_qualifier_drops_the_peak_variant():
    """Better a known absence than a false 7-day window — see `_peak_windows`."""
    html = DEEPSEEK_HTML.replace(
        '10:00 UTC (all other hours',
        '10:00 UTC, on the third Blursday of each month (all other hours',
    )
    flash = DeepSeekSource().parse(html)['deepseek-v4-flash']
    assert flash.peak is None and flash.peak_windows is None
    assert (flash.input, flash.output) == (0.22, 0.66)  # scalar rate survives


@pytest.mark.parametrize(
    ('phrase', 'expected'),
    [
        ('', None),
        ('every day', None),
        ('Monday through Friday', [1, 2, 3, 4, 5]),
        ('Monday to Friday', [1, 2, 3, 4, 5]),
        ('Mon-Fri', [1, 2, 3, 4, 5]),
        ('weekdays', [1, 2, 3, 4, 5]),
        ('weekends', [6, 7]),
        ('Saturday and Sunday', [6, 7]),
        ('Mon, Wed, Fri', [1, 3, 5]),
        ('Tuesday', [2]),
        # Spans wrap through Sunday rather than collapsing to nothing.
        ('Friday-Monday', [1, 5, 6, 7]),
    ],
)
def test_weekday_phrases(phrase, expected):
    assert _weekdays(phrase) == expected


@pytest.mark.parametrize('phrase', ['Blursday', 'the third Tuesday', 'Mon-Tue-Wed'])
def test_an_unreadable_weekday_phrase_raises_rather_than_guessing(phrase):
    with pytest.raises(ValueError, match='unreadable day qualifier'):
        _weekdays(phrase)


def test_deepseek_features_json_output_row_is_not_the_output_price():
    """'Json Output' in FEATURES must not overwrite the output-price row."""
    flash = DeepSeekSource().parse(DEEPSEEK_HTML)['deepseek-v4-flash']
    assert flash.output == 0.66


# The 2026-08-20 break: `/quick_start/pricing` WITHOUT a trailing slash began
# serving 'Your First API Call' instead of 'Models & Pricing'. 200 OK, no
# redirect, real HTML, one entirely valid table — of base_url and api_key rows.
DEEPSEEK_HTML_WRONG_PAGE = """
<table><thead><tr><th>PARAM</th><th>VALUE</th></tr></thead><tbody>
<tr><td>base_url (OpenAI)</td><td>https://api.deepseek.com</td></tr>
<tr><td>api_key</td><td>apply for an API key</td></tr>
<tr><td>model</td><td>deepseek-v4-flash deepseek-v4-pro</td></tr>
</tbody></table>
"""


def test_deepseek_table_without_period_rows_has_no_peak_variant():
    prices = DeepSeekSource().parse(DEEPSEEK_HTML_NO_PEAK)
    pro = prices['deepseek-v4-pro']
    assert (pro.input, pro.output) == (0.435, 0.87)
    assert pro.cache_read == 0.003625
    assert pro.peak is None and pro.peak_windows is None


def test_deepseek_a_200_serving_the_wrong_page_parses_to_nothing():
    """The whole guardrail, in one assertion.

    Note what the wrong page still contains: both model ids, in its `model`
    row. So `expect` would have passed happily — the only thing standing
    between a swapped document and a silently empty book row is `parse`
    refusing to invent prices from a table it does not recognise.
    """
    assert DeepSeekSource().parse(DEEPSEEK_HTML_WRONG_PAGE) == {}


def test_deepseek_url_keeps_its_trailing_slash():
    """Not a style preference — see the comment on DeepSeekSource.url. Dropping
    it swaps the fetched document for an unrelated one that still returns 200."""
    assert DeepSeekSource.url.endswith('/')


# --------------------------------------------------------------------------- #
# Moonshot: platform.kimi.ai is a Mintlify site. Its markdown rendering (`.md`)
# used to carry the price table as a server-side <DocTable> MDX block; since the
# K3.1 rewrite that rendering ships an EMPTY <tbody /> and the rows survive only
# in the page HTML's Next.js RSC payload, as `_jsx(DocTable,{columns:[…],
# rows:[[…]]})` with backticked cells. Both fixtures below are excerpts of what
# the wire actually carries. The Chinese platform's CNY table must not be mixed
# into this USD-only book.
# --------------------------------------------------------------------------- #
MOONSHOT_RSC = r"""
<script>self.__next_f.push([1,"…function _createMdxContent(props){const _components={a:`a`,code:`code`,p:`p`,strong:`strong`,…props.components},{Heading,Note,Tooltip,_MdxComponentBoundary}=_components;
_jsx(Heading,{level:`3`,id:`k3-series-models`,children:`K3 Series Models`}),
_jsx(_MdxComponentBoundary,{name:`DocTable`,children:_jsx(DocTable,{className:`doc-table-bordered`,corner:{top:`Model`,bottom:`Name`},groups:[{title:`Base tokens`,span:2},{title:`Prompt caching`,span:3}],columns:[{title:`Model`,width:`25%`},{title:`Input`,width:`15%`},{title:`Output`,width:`15%`},{title:_jsx(Tooltip,{tip:`Cached input is billed at this rate; each hit refreshes the TTL.`,children:`Cached Input`}),width:`15%`},{title:_jsx(Tooltip,{tip:`Writing a prompt prefix with a 5-minute TTL; the default tier.`,children:`5m writes`}),width:`15%`},{title:_jsx(Tooltip,{tip:`Writing a prompt prefix with a 1-hour TTL.`,children:`1h writes`}),width:`15%`}],rows:[[_jsx(_components.a,{href:`/guide/kimi-k3-1-pro-quickstart`,children:`kimi-k3.1-pro`}),`$2.00 / MTok`,`$10.00 / MTok`,`$0.20 / MTok`,`$2.00 / MTok`,`$4.00 / MTok`],[_jsx(_components.a,{href:`/guide/kimi-k3-1-pro-quickstart`,children:`kimi-k3.1-pro-highspeed`}),`$4.00 / MTok`,`$20.00 / MTok`,`$0.20 / MTok`,`$4.00 / MTok`,`$8.00 / MTok`],[_jsx(_components.a,{href:`/guide/kimi-k3-quickstart`,children:`kimi-k3`}),`$3.00 / MTok`,`$15.00 / MTok`,`$0.30 / MTok`,`$3.00 / MTok`,`$6.00 / MTok`]]})}),
_jsx(_MdxComponentBoundary,{name:`DocTable`,children:_jsx(DocTable,{className:`doc-table-bordered`,corner:{top:`Model`,bottom:`Name`},groups:[{title:`Base tokens`,span:2},{title:`Prompt caching`,span:1}],columns:[{title:`Model`,width:`34%`},{title:`Input`,width:`22%`},{title:`Output`,width:`22%`},{title:_jsx(Tooltip,{tip:`Cached input is billed at this rate.`,children:`Cached Input`}),width:`22%`}],rows:[[_jsx(_components.a,{href:`/guide/kimi-k2-7-code-quickstart`,children:`kimi-k2.7-code`}),`$0.95 / MTok`,`$4.00 / MTok`,`$0.19 / MTok`],[_jsx(_components.a,{href:`/guide/kimi-k2-6-quickstart`,children:`kimi-k2.6`}),`$0.95 / MTok`,`$4.00 / MTok`,`$0.16 / MTok`]]})})
"])
</script>
"""


def test_moonshot_reads_the_flagship_and_its_highspeed_twin():
    prices = MoonshotSource().parse(MOONSHOT_RSC)
    pro = prices['kimi-k3.1-pro']
    assert (pro.input, pro.output, pro.cache_read) == (2.0, 10.0, 0.2)
    # The 5-minute write tier, which the page says applies by default. The 1h
    # column ($4.00) is a different TTL the book has no field for.
    assert pro.cache_write == 2.0
    fast = prices['kimi-k3.1-pro-highspeed']
    assert (fast.input, fast.output, fast.cache_write) == (4.0, 20.0, 4.0)


def test_moonshot_input_is_not_the_adjacent_cached_input_column():
    """'Input' and 'Cached Input' sit next to each other in both tables, so a
    substring match on 'input' would bill every fresh token at the cache-hit
    rate — a silent 10x under-billing on K3 and 5x on K3.1 Pro."""
    k3 = MoonshotSource().parse(MOONSHOT_RSC)['kimi-k3']
    assert (k3.input, k3.cache_read) == (3.0, 0.3)


def test_moonshot_reads_both_generations_from_the_same_page():
    prices = MoonshotSource().parse(MOONSHOT_RSC)
    assert set(prices) == {
        'kimi-k3.1-pro',
        'kimi-k3.1-pro-highspeed',
        'kimi-k3',
        'kimi-k2.7-code',
        'kimi-k2.6',
    }
    assert prices['kimi-k2.6'].input == 0.95
    # The K2 table has no write column at all.
    assert prices['kimi-k2.6'].cache_write is None


def test_moonshot_requires_the_table_payload_not_merely_dollar_amounts():
    assert MoonshotSource().parse('<p>Kimi K3 is 50% off from $99.</p>') == {}


def test_moonshot_reads_nothing_from_the_empty_markdown_rendering():
    """The drift this parser exists for: the `.md` rendering renders every price
    table as `<tbody />`, so it parses to ZERO models while looking successful.
    Pinned so nobody "simplifies" the source back onto it."""
    empty = (
        '# Model Inference Pricing\n\n### K3 Series Models\n\n'
        '<table>\n  <thead>\n    <tr />\n  </thead>\n\n  <tbody />\n</table>\n'
    )
    assert MoonshotSource().parse(empty) == {}


# --------------------------------------------------------------------------- #
# Groq: the marketing pricing page is a client-rendered shell with no payload;
# the console's Supported Models page carries the same rates server-side, mixed
# with rows priced per hour and per character.
# --------------------------------------------------------------------------- #
GROQ_HTML = """
<table><tbody>
  <tr>
    <td><div id="llama-3.3-70b-versatile" class="flex flex-col">
      <span class="font-mono">llama-3.3-70b-versatile</span></div></td>
    <td><div class="flex flex-col text-sm text-muted-foreground">
      <span>Contact</span><span>Sales</span></div></td>
  </tr>
  <tr>
    <td><div id="openai/gpt-oss-120b" class="flex flex-col">
      <span class="font-mono">openai/gpt-oss-120b</span></div></td>
    <td><div class="flex flex-col text-sm">
      <span>$0.15<!-- --> <span class="text-muted-foreground text-xs">input</span></span>
      <span>$0.60<!-- --> <span class="text-muted-foreground text-xs">output</span></span>
    </div></td>
  </tr>
  <tr>
    <td><div id="openai/gpt-oss-20b" class="flex flex-col">
      <span class="font-mono">openai/gpt-oss-20b</span></div></td>
    <td><div class="flex flex-col text-sm">
      <span>$0.075<!-- --> <span class="text-muted-foreground text-xs">input</span></span>
      <span>$0.30<!-- --> <span class="text-muted-foreground text-xs">output</span></span>
    </div></td>
  </tr>
  <tr>
    <td><div id="whisper-large-v3" class="flex flex-col">
      <span class="font-mono">whisper-large-v3</span></div></td>
    <td><div class="flex flex-col text-sm">
      <span>$0.111<!-- --> <span class="text-muted-foreground text-xs">per hour</span></span>
    </div></td>
  </tr>
  <tr>
    <td><div id="canopylabs/orpheus-v1-english" class="flex flex-col">
      <span class="font-mono">canopylabs/orpheus-v1-english</span></div></td>
    <td><div class="flex flex-col text-sm">
      <span>$22.00<!-- --> <span class="text-muted-foreground text-xs">per 1M characters</span></span>
    </div></td>
  </tr>
</tbody></table>
"""


def test_groq_reads_the_token_priced_rows_and_nothing_else():
    prices = GroqSource().parse(GROQ_HTML)
    assert set(prices) == {'openai/gpt-oss-120b', 'openai/gpt-oss-20b'}
    assert (prices['openai/gpt-oss-120b'].input, prices['openai/gpt-oss-120b'].output) == (
        0.15,
        0.60,
    )
    # An hourly voice/audio rate booked as a token rate would make Whisper look
    # like the cheapest text model on the menu; a `Contact Sales` row booked at
    # all would look free.
    assert 'whisper-large-v3' not in prices
    assert 'canopylabs/orpheus-v1-english' not in prices
    assert 'llama-3.3-70b-versatile' not in prices


def test_groq_requires_a_row_with_both_token_rates():
    assert GroqSource().parse('<p>Grab a plan from $9.</p>') == {}
    # An input-only row (a partial render) is not a price.
    assert (
        GroqSource().parse(
            '<table><tr><td><div id="x/y"></div></td>'
            '<td><span>$1<!-- --> <span>input</span></span></td></tr></table>'
        )
        == {}
    )


# --------------------------------------------------------------------------- #
# Regolo: a CSS-grid pseudo-table in EUR, whose fourth column reads `Included`
# and means plan availability, not a free cache read.
# --------------------------------------------------------------------------- #
REGOLO_HTML = """
<div class="grid" style="grid-template-columns: repeat(4, minmax(0,1fr));">
  <div class="py-2">Model</div>
  <div class="py-2">Input cost per 1M tokens</div>
  <div class="py-2">Output cost per 1M tokens</div>
  <div class="py-2">Core · Boost · Pro · Scale</div>
</div>
<div class="grid hover:bg-white/5" style="grid-template-columns: repeat(4, minmax(0,1fr));">
  <div class="px-3 py-2 font-medium text-white">glm5.2</div>
  <div class="px-3 py-2 flex items-center">€2.00</div>
  <div class="px-3 py-2 flex items-center">€5.20</div>
  <div class="px-3 py-2 flex items-center text-green-400">Included</div>
</div>
<div class="grid hover:bg-white/5" style="grid-template-columns: repeat(4, minmax(0,1fr));">
  <div class="px-3 py-2 font-medium text-white">gpt-oss-20b</div>
  <div class="px-3 py-2 flex items-center">€0.10</div>
  <div class="px-3 py-2 flex items-center">€0.42</div>
  <div class="px-3 py-2 flex items-center text-green-400">Included</div>
</div>
"""


def test_regolo_converts_euros_at_the_documented_rate():
    prices = RegoloSource().parse(REGOLO_HTML)
    assert set(prices) == {'glm5.2', 'gpt-oss-20b'}
    glm = prices['glm5.2']
    assert glm.input == pytest.approx(2.00 * EUR_USD)
    assert glm.output == pytest.approx(5.20 * EUR_USD)
    # The exact figures bebri-chat's hand-written rows carried: 1.10 is the
    # multiplier those rows used, and the source must not drift off it silently.
    assert (glm.input, glm.output) == (2.2, 5.72)


def test_regolo_does_not_read_the_plan_column_as_a_cache_price():
    """`Included` is which subscription plans carry the model. Booking it as a
    cache rate would record 0.00 cache reads — free input for any cached
    session — for every model on the page."""
    glm = RegoloSource().parse(REGOLO_HTML)['glm5.2']
    assert glm.cache_read is None
    assert glm.cache_write is None


def test_regolo_rejects_the_header_row_and_anything_odd():
    prices = RegoloSource().parse(REGOLO_HTML)
    assert 'Model' not in prices
    assert RegoloSource().parse('<div>Nothing priced here</div>') == {}


# --------------------------------------------------------------------------- #
# Z.ai: pricing moved off the API-reference introduction page to a dedicated
# pricing page; the source reads its markdown rendering. Three real traps in
# this fixture. (1) On 2026-09-04 a `### Latest Models` table appeared ABOVE
# `### Text Models` and the flagships moved into it, so a parser anchored on
# the Text Models heading returned ten superseded models and no GLM-5.x —
# every per-1M-token table is read now, whatever its section is called.
# (2) A model on promotion renders its list price struck through before the
# effective one (`~~\$0.15~~ \$0.075`), and `dollars` takes the first amount
# it sees. (3) The per-image and per-use tables further down the page must
# not read as token prices. Free rows and '-' cells must not parse.
# --------------------------------------------------------------------------- #
ZAI_MD = r"""
### Latest Models

Prices per 1M tokens.

| Model         | Input              | Cached Input       | Cached Input Storage | Output            |
| :------------ | :----------------- | :----------------- | :------------------- | :---------------- |
| GLM-5.3-Flash | ~~\$0.15~~ \$0.075 | ~~\$0.03~~ \$0.015 | Limited-time Free    | ~~\$0.50~~ \$0.25 |
| GLM-5.3       | \$1.4              | \$0.26             | Limited-time Free    | \$4.4             |

### Text Models

Prices per 1M tokens.

| Model               | Input  | Cached Input | Cached Input Storage | Output |
| :------------------ | :----- | :----------- | :------------------- | :----- |
| GLM-5.3             | \$9.9  | \$9.9        | Limited-time Free    | \$9.9  |
| GLM-5.2             | \$1.4  | \$0.26       | Limited-time Free    | \$4.4  |
| GLM-4.7-FlashX      | \$0.07 | \$0.01       | Limited-time Free    | \$0.4  |
| GLM-4-32B-0414-128K | \$0.1  | -            | -                    | \$0.1  |
| GLM-4.7-Flash       | Free   | Free         | Free                 | Free   |

### Built-in Tools

| Tool       | Cost         |
| :--------- | :----------- |
| Web Search | \$0.01 / use |

### Image Generation Models

Prices per image.

| Model     | Price   |
| :-------- | :------ |
| GLM-Image | \$0.015 |
"""


def test_zai_maps_input_cache_and_output_columns_of_a_token_table():
    prices = ZaiSource().parse(ZAI_MD)
    standard = prices['glm-5.2']
    assert (standard.input, standard.output, standard.cache_read) == (1.4, 4.4, 0.26)


def test_zai_reads_every_token_table_not_one_named_section():
    prices = ZaiSource().parse(ZAI_MD)
    assert set(prices) == {
        'glm-5.3-flash',
        'glm-5.3',
        'glm-5.2',
        'glm-4.7-flashx',
        'glm-4-32b-0414-128k',
    }
    assert prices['glm-4.7-flashx'].output == 0.4


def test_zai_records_the_effective_rate_not_the_struck_through_list_price():
    flash = ZaiSource().parse(ZAI_MD)['glm-5.3-flash']
    assert (flash.input, flash.output, flash.cache_read) == (0.075, 0.25, 0.015)


def test_zai_keeps_the_first_listing_of_a_model_named_twice():
    # The page leads with the current table; a stale duplicate lower down must
    # not overwrite it.
    assert ZaiSource().parse(ZAI_MD)['glm-5.3'].input == 1.4


def test_zai_ignores_tables_that_are_not_priced_per_1m_tokens():
    assert 'glm-image' not in ZaiSource().parse(ZAI_MD)


def test_zai_drops_free_rows_and_missing_cache_cells():
    prices = ZaiSource().parse(ZAI_MD)
    assert 'glm-4.7-flash' not in prices
    assert prices['glm-4-32b-0414-128k'].cache_read is None


def test_first_party_sources_are_registered_before_aggregators():
    names = [source.name for source in SOURCES]
    assert names.index('moonshot') < names.index('llm-prices.com')
    assert names.index('zai') < names.index('llm-prices.com')


# --------------------------------------------------------------------------- #
# Google: one <h2 id="gemini-..."> per model, then per-tier tables. The FIRST
# table is Standard; a later one is Batch at half price.
# --------------------------------------------------------------------------- #
GOOGLE_HTML = """
<h2 id="gemini-3.6-flash">Gemini 3.6 Flash</h2>
<table class="pricing-table"><tbody>
<tr><td>Input price</td><td>Free of charge</td><td>$1.50</td></tr>
<tr><td>Output price (including thinking tokens)</td><td>Free of charge</td><td>$7.50</td></tr>
<tr><td>Context caching price</td><td>Free</td><td>$0.15 $1.00 / 1,000,000 tokens per hour</td></tr>
</tbody></table>
<table class="pricing-table"><tbody>
<tr><td>Input price</td><td>Free of charge</td><td>$0.75</td></tr>
<tr><td>Output price (including thinking tokens)</td><td>Free of charge</td><td>$3.75</td></tr>
</tbody></table>
<h2 id="gemini-2.5-flash-image">Gemini 2.5 Flash Image</h2>
<table class="pricing-table"><tbody>
<tr><th></th><th>Free Tier</th><th>Paid Tier, per 1M tokens in USD</th></tr>
<tr><td>Input price</td><td>Not available</td><td>$0.30 (text / image)</td></tr>
<tr><td>Output price</td><td>Not available</td><td>$0.039 per image*</td></tr>
</tbody></table>
<h2 id="not-a-model">Something else</h2>
<p>no table</p>
"""


def test_google_takes_standard_tier_and_the_paid_column():
    prices = GoogleSource().parse(GOOGLE_HTML)
    flash = prices['gemini-3.6-flash']
    assert (flash.input, flash.output) == (1.50, 7.50)
    # First figure is the per-token cache read; the second is hourly storage.
    assert flash.cache_read == 0.15


def test_google_skips_sections_without_a_pricing_table():
    assert set(GoogleSource().parse(GOOGLE_HTML)) == {'gemini-3.6-flash'}


def test_google_refuses_a_cell_priced_in_something_other_than_tokens():
    # '$0.039 per image' under a 'per 1M tokens' header: read as a token rate
    # it records a $2.50/M model at $0.039/M, 64x low and far under the
    # >$1000/M write gate. No token price, no model.
    assert 'gemini-2.5-flash-image' not in GoogleSource().parse(GOOGLE_HTML)


def test_google_keeps_the_token_rate_when_a_cell_carries_both_units():
    both = (
        '<h2 id="gemini-9-image">x</h2>'
        '<table class="pricing-table"><tbody>'
        '<tr><td>Input price</td><td>$2.00 (text/image),equivalent to '
        '$0.0011 per image*</td></tr>'
        '<tr><td>Output price</td><td>$12.00 (text and thinking)$120.00 '
        '(images)Equivalent to $0.134 per 1K image**</td></tr>'
        '<tr><td>Context caching price</td><td>$0.15 $1.00 / 1,000,000 tokens '
        'per hour</td></tr>'
        '</tbody></table>'
    )
    price = GoogleSource().parse(both)['gemini-9-image']
    assert (price.input, price.output, price.cache_read) == (2.0, 12.0, 0.15)


# --------------------------------------------------------------------------- #
# Google, 2026-10-19: the whole lineup is a discount with a published end date.
# Every rate cell reads "$2.00 through November 30, 2026. $4.00 starting
# December 1, 2026." Taking the first amount recorded a six-week promo as a
# standing price and threw the doubling away — the book would have had nothing
# to say on the day eleven Gemini models doubled at once.
# --------------------------------------------------------------------------- #
GOOGLE_PROMO_HTML = """
<h2 id="gemini-4-argon">Gemini 4 Argon</h2>
<table class="pricing-table"><tbody>
<tr><th></th><th>Free Tier</th><th>Paid Tier, per 1M tokens in USD</th></tr>
<tr><td>Input price</td><td>Not available</td><td>$2.00 through November 30, 2026. $4.00 starting December 1, 2026.</td></tr>
<tr><td>Output price (including thinking tokens)</td><td>Not available</td><td>$10.00 through November 30, 2026. $20.00 starting December 1, 2026.</td></tr>
<tr><td>Context caching price</td><td>Not available</td><td>$0.10 through November 30, 2026. $0.20 starting December 1, 2026. Implicit caching only (explicit caching is not supported).</td></tr>
</tbody></table>
<h2 id="gemini-3.8-flash-tts">Gemini 3.8 Flash TTS</h2>
<table class="pricing-table"><tbody>
<tr><td>Input price</td><td>Not available</td><td>$0.50 (text) through December 31, 2026. $1.00 (text) starting January 1, 2027.</td></tr>
<tr><td>Output price</td><td>Not available</td><td>$9.00 (audio) through December 31, 2026. $18.00 (audio) starting January 1, 2027. Equivalent to $0.00225 per 10s audio * through December 31, 2026.</td></tr>
<tr><td>Context caching price</td><td>Not available</td><td>$0.50 / 1,000,000 tokens per hour (storage price) starting January 1, 2027.</td></tr>
</tbody></table>
"""


def test_google_records_the_announced_rate_next_to_the_one_in_force():
    argon = GoogleSource().parse(GOOGLE_PROMO_HTML)['gemini-4-argon']
    # The rate in force today is the first amount, unchanged.
    assert (argon.input, argon.output, argon.cache_read) == (2.0, 10.0, 0.10)
    # And the announced row keeps the date the vendor published with it.
    assert [(p.input, p.output, p.cache_read, p.effective_from) for p in argon.scheduled] == [
        (4.0, 20.0, 0.20, '2026-12-01')
    ]


def test_google_does_not_read_a_storage_figure_as_the_announced_rate():
    """'$0.50 / 1,000,000 tokens per hour' is cache storage, and the per-10s
    audio figure is not a token rate either. The announcement is anchored on
    'starting <date>', so neither can be mistaken for the next token price."""
    tts = GoogleSource().parse(GOOGLE_PROMO_HTML)['gemini-3.8-flash-tts']
    assert [(p.input, p.output, p.cache_read) for p in tts.scheduled] == [(1.0, 18.0, None)]


def test_google_announced_row_needs_both_token_rates():
    """Half an announced row is not a price: without input AND output there is
    nothing to charge a call against."""
    half = (
        '<h2 id="gemini-9-partial">x</h2>'
        '<table class="pricing-table"><tbody>'
        '<tr><td>Input price</td><td>$2.00 through November 30, 2026. $4.00 '
        'starting December 1, 2026.</td></tr>'
        '<tr><td>Output price</td><td>$10.00</td></tr>'
        '</tbody></table>'
    )
    price = GoogleSource().parse(half)['gemini-9-partial']
    assert price.input == 2.0
    assert price.scheduled is None


# --------------------------------------------------------------------------- #
# Aggregator feed.
# --------------------------------------------------------------------------- #
def test_aggregator_parses_and_leaves_cache_write_unset():
    body = (
        '{"updated_at":"2026-07-30","prices":['
        '{"id":"o3","vendor":"openai","input":10,"output":40,"input_cached":0.5},'
        '{"id":"broken","vendor":"x","input":null,"output":1}]}'
    )
    prices = LlmPricesSource().parse(body)
    assert prices['o3'].input == 10.0
    assert prices['o3'].cache_write is None, 'the feed has no cache-write field'
    assert 'broken' not in prices, 'rows without a price are skipped, not zeroed'


# --------------------------------------------------------------------------- #
# The drift guardrail.
# --------------------------------------------------------------------------- #
class _Stub(Source):
    name = 'stub'
    url = 'https://example.invalid/'
    expect = ('wanted',)

    def __init__(self, prices):
        self._prices = prices

    def parse(self, body: str):
        return self._prices


class _Client:
    def __init__(self, text='body', boom=False):
        self._text, self._boom = text, boom

    def get(self, url, headers=None):
        if self._boom:
            raise RuntimeError('connection reset')
        return self

    text = property(lambda self: self._text)

    def raise_for_status(self):
        return None


def test_zero_models_is_reported_as_drift_not_success():
    r = _Stub({}).fetch(_Client())
    assert not r.ok and 'parsed 0 models' in r.note


def test_parsed_rows_but_no_expected_id_is_drift():
    r = _Stub({'something-else': Price(input=1, output=1)}).fetch(_Client())
    assert not r.ok and 'page structure changed' in r.note


def test_expected_id_present_is_ok():
    r = _Stub({'wanted-1': Price(input=1, output=1)}).fetch(_Client())
    assert r.ok and r.note is None


def test_network_failure_is_contained_in_the_result():
    r = _Stub({}).fetch(_Client(boom=True))
    assert not r.ok and 'RuntimeError' in r.note
    assert isinstance(r, SourceResult)


# --------------------------------------------------------------------------- #
# OpenRouter: per-TOKEN decimal strings under author/model slugs. The traps are
# the unit (x1e6), the id taxonomy, and routing products that are not vendor
# list prices.
# --------------------------------------------------------------------------- #
OPENROUTER_JSON = """
{"data": [
  {"id": "deepseek/deepseek-v4-flash-0731", "context_length": 1048576,
   "pricing": {"prompt": "0.00000014", "completion": "0.00000028",
               "input_cache_read": "0.0000000028"}},
  {"id": "openai/gpt-5.6-luna",
   "pricing": {"prompt": "0.0000002", "completion": "0.0000012",
               "input_cache_read": "0.00000002"}},
  {"id": "anthropic/claude-sonnet-5",
   "pricing": {"prompt": "0.000002", "completion": "0.00001",
               "input_cache_read": "0.0000002", "input_cache_write": "0.0000025"}},
  {"id": "some-org/claude-sonnet-5",
   "pricing": {"prompt": "0.000099", "completion": "0.000099"}},
  {"id": "google/gemini-3-flash-preview:thinking",
   "pricing": {"prompt": "0.0000005", "completion": "0.000003"}},
  {"id": "meta-llama/llama-3.3-70b-instruct:free",
   "pricing": {"prompt": "0", "completion": "0"}},
  {"id": "acme/promo-model",
   "pricing": {"prompt": "0", "completion": "0"}},
  {"id": "openrouter/auto",
   "pricing": {"prompt": "-1", "completion": "-1"}}
]}
"""


def test_openrouter_strips_author_prefix_and_scales_per_token():
    prices = OpenRouterSource().parse(OPENROUTER_JSON)
    luna = prices['gpt-5.6-luna']
    assert (luna.input, luna.output, luna.cache_read) == (0.2, 1.2, 0.02)
    assert 'openai/gpt-5.6-luna' not in prices


def test_openrouter_decimal_scaling_is_exact():
    """Bit-exact floats, so no dust ever reaches the book or a diff."""
    flash = OpenRouterSource().parse(OPENROUTER_JSON)['deepseek-v4-flash-0731']
    assert flash.input == 0.14
    assert flash.cache_read == 0.0028


def test_openrouter_maps_the_cache_write_field():
    sonnet = OpenRouterSource().parse(OPENROUTER_JSON)['claude-sonnet-5']
    assert sonnet.cache_write == 2.5


def test_openrouter_first_author_wins_a_suffix_collision():
    """Two authors, same model suffix: a silent overwrite could pair one
    author's input with another's output in the conflict table."""
    assert OpenRouterSource().parse(OPENROUTER_JSON)['claude-sonnet-5'].input == 2.0


def test_openrouter_skips_routing_variants_and_free_promos():
    prices = OpenRouterSource().parse(OPENROUTER_JSON)
    assert not any(':' in mid for mid in prices)
    assert 'promo-model' not in prices, '0/0 is a routing promo, not a list price'


def test_openrouter_treats_negative_as_no_price_not_a_rate():
    """OpenRouter's meta-routers publish "-1" as a your-price-varies sentinel.

    Found on the first live run: five `openrouter/*` rows carried -1/-1, which
    Price (ge=0) rightly refused — taking the whole source down with them.
    """
    assert 'auto' not in OpenRouterSource().parse(OPENROUTER_JSON)


# --------------------------------------------------------------------------- #
# Raw-body capture: a bad parse must be post-mortemable after the page moved on.
# --------------------------------------------------------------------------- #
def test_fetch_keeps_the_raw_body_on_success():
    r = _Stub({'wanted-1': Price(input=1, output=1)}).fetch(_Client(text='the page'))
    assert r.ok and r.body == 'the page'


def test_fetch_keeps_the_raw_body_when_the_parse_drifts():
    """The whole point: 'what did the page say when we parsed 0 models?'"""
    r = _Stub({}).fetch(_Client(text='the page'))
    assert not r.ok and r.body == 'the page'


def test_network_failure_leaves_body_none():
    assert _Stub({}).fetch(_Client(boom=True)).body is None


def test_save_snapshots_writes_by_content_type_and_prunes(tmp_path):
    from llm_price_tracker.sources import save_snapshots

    for stale in ('2020-01-01', '2020-01-02'):
        (tmp_path / stale).mkdir()
        (tmp_path / stale / 'x.md').write_text('old')

    results = [
        SourceResult('openrouter', 'u', True, body='{"data": []}'),
        SourceResult('deepseek', 'u', True, body='<table></table>'),
        SourceResult('openai', 'u', True, body='| Model |'),
        SourceResult('anthropic', 'u', False, body=None),  # request failed
    ]
    day_dir = save_snapshots(results, root=tmp_path, keep_days=2)

    assert (day_dir / 'openrouter.json').exists()
    assert (day_dir / 'deepseek.html').exists()
    assert (day_dir / 'openai.md').exists()
    assert not (day_dir / 'anthropic.md').exists(), 'no body, nothing to keep'
    kept = sorted(d.name for d in tmp_path.iterdir())
    assert '2020-01-01' not in kept, 'oldest day pruned'
    assert len(kept) == 2
