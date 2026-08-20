# Alert tiers are not data tiers

tags = #architecture-decision-record #alerting #cli #exit-codes #configuration

Built 2026-08-20. `check` no longer exits 1 for every drifted row. It splits
the changes it finds into **watched** (red table, exit 1, reaches
`notify-send`) and **background** (printed, exit 0). At 94 book rows a daily
critical notification about `gemini-2.0-flash-lite` moving a cent is a
notification you learn to dismiss unread — and then you dismiss the 5x cut too.
Same reasoning as the aggregator-only conflict collapse in
[[openrouter-aggregator-status]]: a table that shows the same benign rows every
day trains you to ignore the day a real one appears.

## The split is a filter on ALERTS, never on data

`refresh` is untouched and still folds every corroborated change into the book.
An unwatched model's price must still be right, because something downstream is
still pricing calls against it. Only the exit code is tiered. Anyone tempted to
"skip fetching the boring ones" is deleting billing accuracy to save eight
HTTPS GETs.

Corollary: importance is an *operational preference*, not a vendor fact, so it
does not belong in `prices.json` as an `important: true` column. It lives in
`src/llm_price_tracker/data/watch.toml`, and `watch.py` is stdlib-only and
imports nothing from the fetch path.

## Version parsing: first dotted run after the family prefix

One rule shape has to cover four incompatible vendor naming conventions, so
`FamilyRule.matches` searches rather than anchors:

- OpenAI/Google/Z.ai lead with the version — `gpt-5.6-luna`, `glm-5.2`
- Anthropic trails it — `claude-opus-4.6`, and `claude-fable-5`
- DeepSeek and Moonshot prefix a letter — `deepseek-v4-pro`, `kimi-k3`
- **First**, not last, because `gpt-3.5-turbo-0125`, `gpt-4-0613` and
  `gemini-2.5-computer-use-preview-10-2025` carry a build date that would clear
  any threshold ever written

Tuple comparison gives the right ordering for free: `(5,) < (5, 5)`, so
`gpt-5` is not watched at min 5.5 while `glm-5` is watched at min 5.

The one shape this cannot survive is an id whose leading number is not a
version — `gpt-oss-120b` reads as gpt 120. That is why `exclude_ids` exists.
Nothing in the book hits it today; the list ships empty with the hazard
documented.

## Failure modes deliberately made loud

- **A policy matching nothing is fatal**, checked at load. It would silence
  every alert, which is the exact failure this repo exists to prevent. Unknown
  TOML keys are rejected for the same reason: a typo'd `id = [...]` would parse
  to an empty allowlist and go quiet.
- **The policy loads BEFORE the fetch**, so a config typo costs nothing and can
  never degrade into a silent run.
- **A broken source outranks the watch list.** Exit 2 wins unconditionally. A
  parser returning zero models is exactly when filtering by model *name* is
  least trustworthy — the watched model may be absent from the results
  *because* the page changed, and a name filter reading the survivors would
  report all clear.

## Output order is load-bearing

The background table prints BEFORE the watched table. `llm-price-check-notify.zsh`
builds the notification body from `tail -n 30` of the captured run output, so
whatever prints last is what a human actually reads at 09:00. A test asserts
the ordering, because nothing else in the code would fail if it flipped.

See [[deferred-structural-prices]] for the other preference-vs-fact boundary
call in this book.
