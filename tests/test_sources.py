"""Parser tests against small fixtures shaped like the real pages.

No network. Each fixture reproduces the exact structural trap that broke a
parser in practice, so the test fails if someone "simplifies" the fix away.
"""

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
# Moonshot: the international Kimi forum publishes one USD label/value table.
# The Chinese platform's CNY table must not be mixed into this USD-only book.
# --------------------------------------------------------------------------- #
MOONSHOT_HTML = """
<table>
<thead><tr><th>Price Type</th><th>Price (per 1M tokens)</th></tr></thead>
<tbody>
<tr><td>Input Price</td><td>$3</td></tr>
<tr><td>Cache Hit Price</td><td>$0.3</td></tr>
<tr><td>Output Price</td><td>$15</td></tr>
</tbody>
</table>
<p>For a limited time, Kimi K3 API costs are 50% off.</p>
"""


def test_moonshot_reads_kimi_k3_usd_rates_not_promo_prose():
    price = MoonshotSource().parse(MOONSHOT_HTML)['kimi-k3']
    assert (price.input, price.output, price.cache_read) == (3.0, 15.0, 0.3)


def test_moonshot_requires_the_price_table_not_merely_dollar_amounts():
    assert MoonshotSource().parse('<p>Kimi K3 is 50% off from $99.</p>') == {}


# --------------------------------------------------------------------------- #
# Z.ai: current API prices are rows; the cached-input column sits between the
# ordinary input and output columns.
# --------------------------------------------------------------------------- #
ZAI_HTML = """
<table>
<thead><tr><th>Model</th><th>Input (Cache Miss)</th><th>Input (Cache Hit)</th><th>Output</th></tr></thead>
<tbody>
<tr><td>GLM-5.2</td><td>$1.4 / 1M tokens</td><td>$0.26 / 1M tokens</td><td>$4.4 / 1M tokens</td></tr>
<tr><td>GLM-5.2-Flash</td><td>$0.2 / 1M tokens</td><td>$0.03 / 1M tokens</td><td>$1.6 / 1M tokens</td></tr>
</tbody>
</table>
"""


def test_zai_maps_glm_5_2_input_cache_and_output_columns():
    prices = ZaiSource().parse(ZAI_HTML)
    standard = prices['glm-5.2']
    assert (standard.input, standard.output, standard.cache_read) == (1.4, 4.4, 0.26)


def test_zai_parses_each_glm_model_as_its_vendor_wire_id():
    prices = ZaiSource().parse(ZAI_HTML)
    assert set(prices) == {'glm-5.2', 'glm-5.2-flash'}
    assert prices['glm-5.2-flash'].output == 1.6


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
