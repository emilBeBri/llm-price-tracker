"""Every known price source, and the fan-out that runs them.

Importing this module pulls in httpx and selectolax, which live in the optional
`fetch` extra. The pure read path (`llm_price_tracker.book`) never imports it,
so a consumer that only wants prices does not pay for a scraping stack.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from .aggregator import LlmPricesSource, OpenRouterSource
from .base import Source, SourceResult, dollars
from .vendors import (
    AnthropicSource,
    DeepSeekSource,
    GoogleSource,
    MoonshotSource,
    OpenAISource,
    ZaiSource,
)

__all__ = [
    'SOURCES',
    'AnthropicSource',
    'DeepSeekSource',
    'GoogleSource',
    'LlmPricesSource',
    'MoonshotSource',
    'OpenAISource',
    'OpenRouterSource',
    'Source',
    'SourceResult',
    'ZaiSource',
    'dollars',
    'fetch_all',
    'save_snapshots',
]

USER_AGENT = 'llm-price-tracker/0.1 (+https://github.com/emilbebri)'

SOURCES: tuple[Source, ...] = (
    AnthropicSource(),
    OpenAISource(),
    GoogleSource(),
    DeepSeekSource(),
    MoonshotSource(),
    ZaiSource(),
    LlmPricesSource(),
    OpenRouterSource(),
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


def default_snapshot_dir() -> Path:
    """XDG cache, not the repo: snapshots are a machine-local debugging aid.

    The CLI may run installed as a uv tool from any cwd, so a cwd-relative
    `raw/` would scatter copies wherever the command happened to run.
    """
    cache_home = os.environ.get('XDG_CACHE_HOME') or '~/.cache'
    return Path(cache_home).expanduser() / 'llm-price-tracker' / 'raw'


def save_snapshots(
    results: list[SourceResult], root: Path | None = None, keep_days: int = 14
) -> Path:
    """Write each result's raw body to <root>/<YYYY-MM-DD>/<source>.<ext>.

    Exists so a bad parse can be post-mortemed after the live page has moved
    on — "what did the page actually say when the parser returned 0 models?"
    is unanswerable without the body. Re-runs the same day overwrite; date
    directories older than `keep_days` are pruned so the cache stays bounded
    (the OpenRouter feed alone is multiple MB per day).
    """
    root = root or default_snapshot_dir()
    today = datetime.now(UTC).date().isoformat()
    day_dir = root / today
    day_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        if r.body is None:
            continue
        head = r.body.lstrip()[:1]
        ext = 'json' if head in ('{', '[') else 'html' if head == '<' else 'md'
        (day_dir / f'{r.source}.{ext}').write_text(r.body, encoding='utf-8')

    for old in sorted(d for d in root.iterdir() if d.is_dir())[:-keep_days]:
        for f in old.iterdir():
            f.unlink()
        old.rmdir()
    return day_dir
