# Peak windows carry a weekday restriction, and an unreadable one drops the window

tags = #architecture-decision-record #schema #parsing #pricing #deepseek

`TimeWindow` was start/end only. DeepSeek's footnote reads:

> Peak hours are 01:00 - 04:00 and 06:00 - 10:00 UTC, **Monday through
> Friday** (all other hours are off-peak).

`_WINDOW_TEXT` was `r'peak hours are\s+(.*?)\s*utc'` — it terminated at the
zone and the day qualifier rode past unread. The vendor added it some time
after the 2026-08-18 peak-pricing activation (the fixture captured from the
page then does not have it), so the book has been claiming peak rates on
Saturday and Sunday mornings ever since. Every consumer deriving cost from
stored token counts — bebri-chat's `usage` table is exactly that, see its
[[historical-cost-uses-rate-in-force]] — has been doubling weekend DeepSeek
figures in those hours.

Found 2026-09-03 from the other end: bebri-chat added a startup warning for
peak-priced models, and checking the vendor doc to confirm the hours surfaced
the qualifier the parser was dropping.

## The shape

- `TimeWindow.days: list[int] | None` — ISO weekdays, 1=Monday … 7=Sunday.
- `None`, not a synthesised `[1..7]`, is unrestricted. Every book written
  before the field reads back identically, and `save_book`'s `exclude_none`
  keeps the key out of the committed JSON until a vendor actually publishes a
  restriction.
- A validator rejects an empty list and anything outside 1-7: a window active
  on no day is a peak rate that silently never applies, which can only be a
  parser bug.
- `contains()` attributes a **midnight-wrapping** window to the day it
  STARTED on — the tail of `Fri 22:00 - 02:00` is Friday's window, not
  Saturday's. Vendors publish one span with one day qualifier; splitting it
  across two days would be a different policy from the one announced.

Consumers need no change if they only ask `contains(at)`, which is the whole
point of handing them the `TimeWindow` object rather than its hours. bebri-chat
picked this up with zero code edits.

## An unreadable qualifier drops the window, deliberately

`_weekdays()` raises rather than guessing, and `_peak_windows` turns that into
`None` — no windows, so no peak variant at all. Defaulting to every day looks
gentler and is worse: the two failures are not symmetric. Recording an
unrestricted window when the vendor published a restricted one is a **false
fact** that silently doubles a consumer's bill, while recording no window is
an absence the book already models (`peak=None`) and every consumer already
handles. It is also loud — the peak variant vanishing shows up as a CHANGED
delta for a human to read before `refresh --write` lands it.

Parsed forms: `Monday through Friday`, `Monday to Friday`, `Mon-Fri`,
`weekdays`, `weekends`, `Mon, Wed, Fri`, `Tuesday`, and spans that wrap the
week (`Friday-Monday` -> 5,6,7,1). `every day` / `daily` / absent -> `None`.
The day map is keyed on the first three letters every abbreviation shares;
`through` is normalised to a dash first so it can never collide with `thu`.

## The book diff had the same blind spot

`compare._same` compared `[(w.start, w.end) for w in ...]` — a hand-listed
tuple of fields, so a `days` change would never have surfaced as CHANGED and
the fixed parser's output would never have reached the book. It now compares
the `TimeWindow` models by value, which covers the next field added too.

See [[rate-history-effective-from]] for the other axis of a time-resolved
rate, and [[wrong-document-200]] for the other DeepSeek parser trap.

# sources

conversation 2026-09-03; vendor footnote cross-checked against
`~/source-docs/deepseek-docs/api_docs_deepseek_com_quick_start_pricing.md`.
