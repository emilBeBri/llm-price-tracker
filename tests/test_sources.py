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
    LlmPricesSource,
    MoonshotSource,
    OpenAISource,
    OpenRouterSource,
    ZaiSource,
)
from llm_price_tracker.sources.base import Source, SourceResult, dollars
from llm_price_tracker.sources.vendors import _weekdays


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
# Moonshot: the HTML page is client-rendered (zero <table> elements), so the
# source reads the docs site's markdown rendering, where the price table
# survives as a server-side <DocTable> MDX block. Prices sit in JSX fragments
# (<>{"$"}3.00</>); the component definition above must NOT be mistaken for a
# table. The Chinese platform's CNY table must not be mixed into this
# USD-only book.
# --------------------------------------------------------------------------- #
MOONSHOT_MD = r"""
> ## Documentation Index
> Fetch the complete documentation index at: https://platform.kimi.ai/docs/llms.txt
# Flagship Model Kimi K3 Pricing
export const DocTable = ({columns = [], rows = []}) => {
  return <div className="doc-table-wrap">
      <table className="doc-table">
        {columns.length > 0 ? <colgroup>
            {columns.map((column, index) => <col key={index} style={column.width ? {
    width: column.width
  } : undefined} />)}
          </colgroup> : null}
        <thead>
          <tr>
            {columns.map((column, index) => <th key={index}>{column.title}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => <tr key={rowIndex}>
              {row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}
            </tr>)}
        </tbody>
      </table>
    </div>;
};
## Product Pricing
**Explanation: Prices exclude applicable taxes.**
<DocTable
  columns={[
{ title: "Model", width: "24%" },
{ title: "Unit", width: "12%" },
{ title: "Input Price (Cache Hit)", width: "16%" },
{ title: "Input Price (Cache Miss)", width: "16%" },
{ title: "Output Price", width: "14%" },
{ title: "Context Window", width: "18%" },
]}
  rows={[
["kimi-k3", "1M tokens", <>{"$"}0.30</>, <>{"$"}3.00</>, <>{"$"}15.00</>, "1,048,576 tokens"],
]}
/>
"""


def test_moonshot_reads_kimi_k3_usd_rates_from_the_doctable_block():
    price = MoonshotSource().parse(MOONSHOT_MD)['kimi-k3']
    assert (price.input, price.output, price.cache_read) == (3.0, 15.0, 0.3)


def test_moonshot_requires_the_doctable_not_merely_dollar_amounts():
    assert MoonshotSource().parse('<p>Kimi K3 is 50% off from $99.</p>') == {}


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
