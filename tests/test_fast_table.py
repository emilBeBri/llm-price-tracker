"""The Fast table: three ways a long-context block can be absent, not one.

The parser's own `_long_block` decides between a real band, an unpublished band
(a `max_context` restriction) and a malformed block. Re-deriving that from the
raw cells would quietly turn a valid band into a short-only row whenever an
optional cache cell is dashed.
"""

from __future__ import annotations

from llm_price_tracker.sources.vendors import OpenAISource

CAPTION = (
    'Short context: \u2264272K input tokens. Long context: >272K input tokens.\n'
)

MODERN = """### Fast pricing data

| Model | Short context input | Short context output | Short context cached input | Short context 30m write | Long context input | Long context output | Long context cached input | Long context 30m write |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-banded | $4.00 | $20.00 | $0.20 | $5.00 | $8.00 | $30.00 | $0.40 | $10.00 |
| gpt-restricted | $12.50 | $75.00 | $1.25 | $15.00 | - | - | - | - |
"""

LEGACY_ONLY = """### Fast pricing data

| Model | Input | Output | Cached input |
| --- | --- | --- | --- |
| gpt-legacy | $4.25 | $17.00 | $0.425 |
"""


def _parse(body: str):
    return OpenAISource().parse_tiers(CAPTION + body).get('fast', {})


def test_a_published_long_block_is_a_real_band() -> None:
    rows = _parse(MODERN)
    banded = rows['gpt-banded']
    assert banded.max_context is None
    assert banded.long_context is not None
    assert banded.long_context.input == 8.0


def test_an_all_dash_long_block_is_a_restriction() -> None:
    rows = _parse(MODERN)
    restricted = rows['gpt-restricted']
    assert restricted.long_context is None
    assert restricted.max_context == 272_000


def test_a_four_column_table_is_not_a_restriction() -> None:
    """No long columns at all means the vendor draws no band distinction —
    that is NOT the same as publishing only short-context rates."""
    rows = _parse(LEGACY_ONLY)
    assert rows['gpt-legacy'].max_context is None
    assert rows['gpt-legacy'].long_context is None


def test_a_partly_published_long_block_still_raises() -> None:
    import pytest

    broken = MODERN.replace(
        '| gpt-restricted | $12.50 | $75.00 | $1.25 | $15.00 | - | - | - | - |',
        '| gpt-restricted | $12.50 | $75.00 | $1.25 | $15.00 | - | - | $0.60 | - |',
    )
    with pytest.raises(ValueError):
        _parse(broken)
