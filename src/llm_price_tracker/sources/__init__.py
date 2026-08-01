"""Every known price source, and the fan-out that runs them.

Importing this module pulls in httpx and selectolax, which live in the optional
`fetch` extra. The pure read path (`llm_price_tracker.book`) never imports it,
so a consumer that only wants prices does not pay for a scraping stack.
"""

from __future__ import annotations

from .aggregator import LlmPricesSource
from .base import Source, SourceResult, dollars
from .vendors import AnthropicSource, DeepSeekSource, GoogleSource, OpenAISource

__all__ = [
    'SOURCES',
    'AnthropicSource',
    'DeepSeekSource',
    'GoogleSource',
    'LlmPricesSource',
    'OpenAISource',
    'Source',
    'SourceResult',
    'dollars',
    'fetch_all',
]

USER_AGENT = 'llm-price-tracker/0.1 (+https://github.com/emilbebri)'

SOURCES: tuple[Source, ...] = (
    AnthropicSource(),
    OpenAISource(),
    GoogleSource(),
    DeepSeekSource(),
    LlmPricesSource(),
)


def fetch_all(
    sources: tuple[Source, ...] = SOURCES, timeout: float = 30.0
) -> list[SourceResult]:
    """Run every source. One failure never takes down the others."""
    import httpx

    with httpx.Client(
        headers={'User-Agent': USER_AGENT}, timeout=timeout, follow_redirects=True
    ) as client:
        return [s.fetch(client) for s in sources]
