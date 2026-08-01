"""Parser tests against small fixtures shaped like the real pages.

No network. Each fixture reproduces the exact structural trap that broke a
parser in practice, so the test fails if someone "simplifies" the fix away.
"""

from llm_price_tracker.sources import (
    GoogleSource,
    AnthropicSource,
    DeepSeekSource,
    LlmPricesSource,
    OpenAISource,
)
from llm_price_tracker.sources.base import Source, SourceResult, dollars
from llm_price_tracker.models import Price


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
# --------------------------------------------------------------------------- #
DEEPSEEK_HTML = """
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
    assert (pro.input, pro.output) == (0.435, 0.87)
    # ~0.008x of input — nothing like a generic 0.1x fallback.
    assert pro.cache_read == 0.003625
    assert prices['deepseek-v4-flash'].cache_read == 0.0028


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
