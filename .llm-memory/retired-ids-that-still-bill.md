# A retired model id that still bills is mirrored, not deleted

tags = #architecture-decision-record #pricing-data #deepseek #drift

DeepSeek retired V4-Flash and V4-Flash-Vision-Exp on 2026-09-10, and kept
both **ids** alive: `deepseek-v4-flash` and `deepseek-v4-flash-vision-exp`
still resolve, are served by DeepSeek-V4.1-Flash, and are billed at the Flash
rate. The vendor pricing table dropped both columns the same day, so the
source stopped mentioning them while consumers kept calling them.

Three options, and why the middle one won:

1. **Delete the rows.** `get_price('deepseek-v4-flash')` returns `None`, the
   consumer prices a real call at nothing. A silent `$0` is exactly what
   `get_price` refuses to substitute — deleting the row does it anyway.
2. **Mirror them** (chosen). The current `tiers.standard` becomes a copy of
   `deepseek-flash`'s row (`effective_from: 2026-09-10`), the outgoing V4-Flash
   rate is pushed onto `history`, and `note` says the id is retired. Today's
   calls price at what they actually cost; `--at 2026-09-01` still prices an
   August call at the real $0.22/$0.66, because history is untouched.
3. **An `alias_of` field** resolved in `get_entry`. Rejected: an alias has to
   be dated to keep re-pricing honest (before 2026-09-10 the id was its own
   model with its own rates), so it is `alias_of` + `alias_from` + a resolver
   in the one function whose ambiguity rule is "return None rather than
   guess" — schema, refresh path, and every consumer, for three rows. Revisit
   if a vendor ever does this at scale.

**The hazard the mirror carries:** the copy is a snapshot. When V4.1-Flash's
price next moves, `deepseek-flash` updates from the vendor page and the two
mirrored rows silently do not — the book would then be confidently wrong,
which is the exact failure this repo exists to prevent. What catches it is
already built: `check` counts *book rows no source mentioned*, and these rows
are permanently in that count now. Treat that counter as a re-mirror queue,
not as noise.

`compare.py` preserves `entry.note` across a refresh, so the marker survives.
`show` renders noted rows with a starred id and prints the notes under the
table — a prose column collapses the numeric ones at any sane terminal width.

Scheduled reroutes get a note and nothing else: `deepseek-v4-pro` starts
routing to V4.1-Flash at Flash rates at 04:00 UTC 2026-09-14, but `tiers`
holds *the rate in force now* and there is no slot for a future-dated row —
writing one would make today's lookups wrong. The vendor page may well keep
publishing the V4-Pro card afterwards, so this one will NOT self-correct on a
refresh. Re-check it by hand on 2026-09-14.

See [[rate-history-effective-from]] for the history mechanism this leans on,
and [[alert-tiers-are-not-data-tiers]] for why an id change can also cost you
the alert.

# sources

src/llm_price_tracker/data/prices.json
src/llm_price_tracker/main.py
