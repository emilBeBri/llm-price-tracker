# llm-price-tracker

Published LLM prices, read from each vendor's own pricing page, cross-checked
against each other, and committed as a reviewable artifact.

```bash
uv sync --extra fetch
uv run llm-price-tracker check                       # fetch everything, diff the book
uv run llm-price-tracker check --all                 # …and alert on every model, not just watched ones
uv run llm-price-tracker refresh --write             # fold live prices in
uv run llm-price-tracker show gpt-5                  # offline lookup
uv run llm-price-tracker relative deepseek-flash     # compare every model to a reference
uv run llm-price-tracker relative fzf                # interactively choose two models
```

`relative REFERENCE` lists every standard-tier model, sorted by input rate, with
input, output, cache-read and cache-write prices expressed as percentages of the
same rate on `REFERENCE`. Add a second model id for a two-model comparison.
`relative fzf` selects the reference and comparison model in two successive
pickers. Relative lookup is offline and never fetches vendor pages.

## Why it exists

Not because prices were unavailable — because **a single source is
confidently wrong more often than you would expect**, and there was no way to
notice. Observed inside one week:

| | claim | truth |
| --- | --- | --- |
| aggregator feed | `o3` at 10/40 | 2/8 — the pre-cut launch price, never updated |
| aggregator feed | DeepSeek cache-hit 0.028 | 0.0028 — off by 10x |
| a docs mirror crawled 3 days earlier | `gpt-5.6-luna` at 1.0/6.0 | 0.2/1.2 — missed a 5x cut |

Each source reads as authoritative on its own. Only the **disagreement between
them** carries information. So this tool collects every source it can and
reports conflicts rather than picking a winner:

```
Sources disagree — check the vendor page before trusting either
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ model            ┃           deepseek ┃     llm-prices.com ┃          openai ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ deepseek-v4-flash│ $0.14/$0.28/$0.0028│ $0.14/$0.28/$0.028 │               — │
│ deepseek-v4-pro  │ $0.435/$0.87/$0.00…│ $1.74/$3.48/$0.145 │               — │
│ o3               │                  — │ $10.00/$40.00/$0.5 │ $2.00/$8.00/$0.5│
└──────────────────┴────────────────────┴────────────────────┴─────────────────┘
```

## Design

**The book is data, not a service.** `check`/`refresh` touch the network;
`llm_price_tracker` itself does not. Cost estimation must be deterministic —
the same call priced twice must not depend on whether a cache had expired — so
the read path is pure and offline, and the scraping deps (`httpx`,
`selectolax`) sit behind the optional `fetch` extra.

```python
from llm_price_tracker import estimate_cost, get_price

get_price('gpt-5.6-luna')  # Price(input=0.2, output=1.2, ...)
estimate_cost('gpt-5.6-luna', 1_000_000, 50_000, cache_read_tokens=800_000)
```

**A price change does not erase what a call cost.** A refresh that changes a
rate pushes the outgoing one onto `ModelEntry.history` and stamps the incoming
one with `effective_from`, so a past call can be priced at the rate in force
then. Pass the moment and both axes resolve off it — the calendar rate first,
then that rate's peak/off-peak window:

```python
from datetime import UTC, datetime

estimate_cost('gpt-5.6-sol', 1_000_000, 1_000_000)                        # 24.00, today
estimate_cost('gpt-5.6-sol', 1_000_000, 1_000_000,
              at=datetime(2026, 8, 15, tzinfo=UTC))                       # 35.00, pre-cut
```

Without `at` you get today's rate — which is right for pricing a call as you
make it, and wrong for re-pricing one you made in July. A consumer that stores
token counts and derives cost on read (rather than snapshotting cost at call
time) must pass `at`, or every vendor price move silently rewrites its whole
billing history.

`llm-price-tracker show <model> --at 2026-08-15` reads the same history from
the CLI. Dates are **first-observed**: the commit that introduced the rate, a
lower bound on when the vendor started charging it, never an announcement
date. A query older than every recorded row returns the oldest known rate
rather than `None` — refusing to price a call the app definitely made is
worse, and `effective_from` on the returned row always says whether you got a
record or an extrapolation. History before 2026-09-02 was backfilled from
`git log` by `scripts/backfill_price_history.py`.

**Vendor facts only.** The book records what a vendor publishes, keyed by the
vendor's own model id. It holds no consuming app's billing decisions — no
namespaced keys, no deployment aliases, no projected future prices. Apps map
their own keys onto these ids and keep their overrides locally.

**Absence is loud, wrong is silent.** An unknown model returns `None`, never
`0.0`. For the same reason `refresh` refuses by default to record a model that
no first-party page corroborates: the aggregator alone would have written that
stale `o3` into the book as though it were fact. Pass `--uncorroborated` to
override.

**A drifted parser never deletes data.** A model no source mentioned this run
is reported, not dropped. Far more often the selector broke than the model was
withdrawn, and silently deleting rows turns one bad selector into mass loss of
billing data.

**Exact id matching only.** Fuzzy matching across sources at a 0.72 similarity
threshold matched `glm-4.5-flash` to *Gemini 1.5 Flash*, `grok-4.3` to *Grok 3*
and `claude-haiku-4-5` to *Claude 3 Haiku*. Exact matching cut ~89 noisy
proposals to ~10 real ones. A model only one source names is reported as
single-source, never guessed at.

## Sources

| source | reads | format |
| --- | --- | --- |
| `anthropic` | platform.claude.com pricing | markdown |
| `openai` | developers.openai.com pricing | markdown |
| `google` | ai.google.dev pricing | HTML |
| `deepseek` | api-docs.deepseek.com pricing | HTML |
| `moonshot` | platform.kimi.ai chat-k3 pricing | markdown (MDX) |
| `zai` | docs.z.ai pricing, every per-1M-token table | markdown |
| `llm-prices.com` | Simon Willison's aggregated feed | JSON |
| `openrouter` | openrouter.ai `/api/v1/models` | JSON |

The `Accept` header is per-source and load-bearing. These sites
content-negotiate, and the same URL yields genuinely different documents:
OpenAI's markdown is 19KB of clean pipe-tables covering every model family,
while its HTML is 587KB that renders half the tables client-side. Getting this
wrong fed markdown to an HTML parser and produced zero models for days.

The two aggregators are kept deliberately, as cross-checks rather than
replacements, and neither can ever authorize a write. Simon's feed is a curated
human artifact — hand-edited per-vendor JSON, built and published on push with
**no schedule** — so it is exactly as fresh as its author's last commit.
OpenRouter is the one clean machine-readable feed in the ecosystem (~700 models,
per-token prices), but it prices *its own routing*, which legitimately diverges
from what a vendor bills directly, and its `author/model` slugs only sometimes
coincide with vendor wire ids. Both are independent, and therefore useful; the
payoff showed on the first run with both: OpenRouter agreed with OpenAI's page
on `o3` at 2/8, isolating the feed's stale 10/40 as the outlier. Conflicts in
which no vendor page participates are summarized in one line rather than
tabled — routing-vs-list disagreements are permanent, and a table that shows
the same benign rows every day trains you to ignore the real one.

Every fetch's raw body is snapshotted to
`$XDG_CACHE_HOME/llm-price-tracker/raw/<date>/<source>.<ext>` (14 days kept),
so "what did the page actually say when the parser broke?" stays answerable
after the live page has moved on. It paid for itself on its first run: the
snapshot is how OpenRouter's `-1` price sentinel on its meta-routers was
diagnosed.

Two write-time guards keep garbage out of the book: a negative rate is rejected
at the model level (no vendor pays you), and a rate above $1,000/M refuses to
save — the classic per-token vs per-1M unit bug is a factor of exactly 1e6, so
it lands absurdly far outside that ceiling.

## Wire the exit code to something

The failure that motivated this tool was not a missing scraper. It was a
scraper that had been exiting non-zero for days, correctly, with nobody
consuming the signal — while a 5x price cut went unnoticed.

- `0` — everything agrees, or only background models moved
- `1` — a **watched** model's price changed
- `2` — a source broke (page structure drifted, or unreachable), or the watch
  policy is unusable

## Two tiers, because a daily alarm about `gemini-2.0-flash-lite` is not an alarm

Ninety-odd models shouting at the same volume defeats the purpose as thoroughly
as no alert at all. A notification that fires because a model you retired two
generations ago moved by a cent is one you learn to dismiss unread — and on the
day the model you actually bill against gets cut 5x, you dismiss that one too.

So `check` splits the drift it finds. Watched models get the red table and exit
`1`; everything else is printed and exits `0`. The split lives in
`src/llm_price_tracker/data/watch.toml`:

```toml
[[families]]
name = "gpt"
min_version = "5.5"
```

A model is watched when its family matches and its version is at or above the
threshold. The version is the **first** dotted number after the family name,
which is what makes one rule shape cover every vendor's naming: OpenAI leads
with it (`gpt-5.6-luna`), Anthropic trails it (`claude-opus-4.6`), DeepSeek and
Moonshot prefix a letter (`deepseek-v4-pro`, `kimi-k3`), and the trailing build
dates that would otherwise read as versions (`gpt-3.5-turbo-0125`,
`gemini-2.5-computer-use-preview-10-2025`) are ignored. `ids` and `exclude_ids`
take globs for the ids no threshold describes.

Three deliberate choices in there:

- **It is a filter on alerts, not on data.** `refresh` still folds every
  corroborated change into the book, because a cost estimate for an unwatched
  model must still be correct. Only the exit code is tiered.
- **A broken source still exits `2`, whatever the watch list says.** A parser
  returning zero models is precisely when filtering by model *name* is least
  trustworthy: the watched model may be missing from the results *because* the
  page changed, and a filter reading the surviving names would conclude all is
  well.
- **A policy that matches nothing is a fatal error, not an empty filter.** It
  would silence every alert — the exact failure this repository exists to
  prevent — so it is rejected before the first HTTPS request, and it exits `2`
  so the notification still fires.

Run `check --all` to ignore the policy entirely. That is the audit to run before
trusting a quiet week.

A systemd user timer ships in the dotfiles repo:

```
.control-center/dotfiles/systemd/shared/llm-price-check.service
.control-center/dotfiles/systemd/shared/llm-price-check.timer
```

Deploy and enable:

```bash
./dotfiles/systemd/build-systemd.zsh     # compiles shared/ + hosts/$(hostname) into in-use/
systemctl --user daemon-reload
systemctl --user enable --now llm-price-check.timer
systemctl --user list-timers llm-price-check.timer
systemctl --user start llm-price-check.service && \
  systemctl --user status llm-price-check.service   # force one run
```

Three choices in there are deliberate:

- **`ExecStopPost` sends the notification.** Without a consumer the exit code is
  decorative, which is exactly how the predecessor failed.
- **`ExecCondition=ping` makes offline a SKIP, not a failure.** A laptop shut
  for a week must not produce a queue of false alarms — those train you to
  ignore the real one.
- **Daily, not weekly.** Eight HTTPS GETs cost nothing and a clean run is
  silent; weekly is how you notice a 5x cut six days late.

## Adding a vendor

Subclass `Source`, implement `parse`, add it to `SOURCES`. The base class
handles fetching, failure containment and the drift guardrail — `expect` lists
model-id fragments that must appear in a successful parse, so a page redesign
reports `SITE CHANGED` instead of quietly returning nothing.

```python
class AcmeSource(Source):
    name = 'acme'
    url = 'https://acme.example/pricing'
    expect = ('acme-1',)

    def parse(self, body: str) -> dict[str, Price]: ...
```

Add a fixture test alongside it in `tests/test_sources.py` reproducing whatever
structural trap the page contains. Every fixture there is a real bug.
