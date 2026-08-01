"""The data model. Pure — stdlib + pydantic only, no network, no scraping.

Everything here is a *vendor fact*: what a vendor charges, as published. It is
deliberately free of any consuming app's billing choices (which tier to assume,
whether to project a future price). Those belong to the app, not to this book.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# The tier every entry must carry. Vendors publish several live rates for the
# same model at once — OpenAI standard/batch/priority, xAI and Gemini <200k vs
# >=200k, Anthropic standard/batch — and a cost estimator that sees only a model
# name and token counts cannot know which applied. So the book stores every tier
# it can read and names this one as the default a caller gets when it does not
# ask. Choosing among them is the caller's job, not the book's.
STANDARD = 'standard'


class Price(BaseModel):
    """One tier's rates for one model, USD per 1,000,000 tokens.

    `None` means the vendor publishes no such rate (not "free", and not
    "unknown-so-guess"). A consumer that wants a fallback multiplier must apply
    it itself — inventing one here would launder a guess into a recorded fact.

    Negative rates are rejected at the model level: no vendor pays you to use
    their API, so a negative can only be a parser bug. The upper sanity bound
    lives in `save_book`, not here — a heuristic ceiling belongs at write time,
    where it blocks recording garbage without bricking reads of a book that a
    legitimately expensive future model would otherwise invalidate.
    """

    input: float = Field(ge=0.0)
    output: float = Field(ge=0.0)
    cache_read: float | None = Field(default=None, ge=0.0)
    cache_write: float | None = Field(default=None, ge=0.0)


class ModelEntry(BaseModel):
    """One model, as one vendor publishes it.

    `id` is the vendor's own wire id, NOT a consuming app's registry key. Apps
    namespace and alias freely (`groq:openai/gpt-oss-120b`, Azure deployment
    names, `-dev` suffixes); mapping their keys onto these ids is their job.
    """

    # Deliberately `id`, not `model_id`: pydantic reserves the `model_` prefix
    # and warns on fields that use it.
    id: str
    vendor: str
    tiers: dict[str, Price]
    context_window: int | None = None
    # Which sources corroborated this entry at the last refresh. Two independent
    # sources agreeing is the closest thing to confidence available here.
    sources: list[str] = Field(default_factory=list)
    note: str | None = None

    @property
    def standard(self) -> Price | None:
        return self.tiers.get(STANDARD)


class PriceBook(BaseModel):
    """The committed artifact: every model this tracker knows a price for."""

    updated_at: str
    models: dict[str, ModelEntry] = Field(default_factory=dict)

    def get(self, model_id: str) -> ModelEntry | None:
        return self.models.get(model_id)

    def by_vendor(self, vendor: str) -> dict[str, ModelEntry]:
        return {k: v for k, v in self.models.items() if v.vendor == vendor}
