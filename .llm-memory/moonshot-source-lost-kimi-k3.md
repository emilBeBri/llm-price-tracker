# Moonshot source lost kimi-k3 to a page rewrite

tags = #bug #sources #parsing #moonshot #moonshot-source

`check` on 2026-10-16 reported moonshot BROKEN: "parsed 3 rows but none match
['kimi-k3'] — page structure changed". The parser still reads three rows, so
the selector is not empty — the table it qualifies no longer carries the id
the source's own expectation list requires. That mismatch is the designed
failure mode: a parser returning nothing gets noticed, one returning the
WRONG rows does not. The shape-qualification fix has a precedent in
[[parse-the-table-not-the-heading]].

Consequences while it stays broken:

- `kimi-k3` keeps its last book row and joins check's unmentioned-rows counter
  (the re-mirror queue [[retired-ids-that-still-bill]] describes).
- No new Moonshot model can enter the book: nothing else covers the vendor
  first-party, and an aggregator-only row is refused by design.

Next step: pull the raw snapshot from `$XDG_CACHE_HOME/llm-price-tracker/raw/`
and re-qualify the table by shape, then delete this note.

# sources

llm-price-tracker check/refresh run, 2026-10-16 — bebri-chat session b69ba2f6d6ee
