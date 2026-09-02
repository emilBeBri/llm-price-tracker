#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Reconstruct `ModelEntry.history` for the price book from git.

Every rate this book has ever held is already recorded — as commits to
`src/llm_price_tracker/data/prices.json`. Before rate history existed as a
schema feature, that log WAS the history, and a consumer wanting the rate in
force on some past date had to read diffs by hand. This script promotes that
log into the book itself, once, so `get_price(..., at=<then>)` can answer.

Deliberately a script and not a CLI subcommand: the package is pure and
offline by design (see `llm_price_tracker/__init__`), and shelling out to git
belongs nowhere near the read path. Run it from the repo:

    ./scripts/backfill_price_history.py            # dry run, prints the plan
    ./scripts/backfill_price_history.py --write    # rewrite the book

Idempotent — it recomputes from git every time rather than appending, so a
second run over the same history is a no-op. Rows the current book no longer
carries are not resurrected: a model dropped on purpose stays dropped.

DATE SEMANTICS: a stamped `effective_from` is the date this book FIRST
OBSERVED that rate, i.e. the commit that introduced it. It is a lower bound on
when the vendor started charging it, never an announcement date — every rate
present in the book's first commit is stamped with that commit's date, though
most were in force well before. `ModelEntry.price_at` documents how a query
older than every recorded row is answered.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOK_REL = 'src/llm_price_tracker/data/prices.json'
BOOK = ROOT / BOOK_REL
RATE_FIELDS = ('input', 'output', 'cache_read', 'cache_write', 'peak', 'peak_windows')


def git(*args: str) -> str:
    return subprocess.run(
        ['git', '-C', str(ROOT), *args],
        check=True, capture_output=True, text=True,
    ).stdout


def rate_key(price: dict) -> str:
    """Identity of a rate, ignoring effective_from — two rows with the same
    numbers are the same rate however they were stamped."""
    return json.dumps({f: price.get(f) for f in RATE_FIELDS}, sort_keys=True)


def commits_oldest_first() -> list[tuple[str, str]]:
    out = git('log', '--follow', '--reverse', '--format=%H %ad', '--date=short',
              '--', BOOK_REL).strip()
    if not out:
        return []
    return [(line.split()[0], line.split()[1]) for line in out.splitlines()]


def observations() -> dict[tuple[str, str], list[tuple[str, dict]]]:
    """{(model_id, tier): [(first_seen_date, price_dict), ...]} oldest first,
    one entry per DISTINCT consecutive rate."""
    seen: dict[tuple[str, str], list[tuple[str, dict]]] = {}
    for sha, date in commits_oldest_first():
        try:
            payload = json.loads(git('show', f'{sha}:{BOOK_REL}'))
        except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
            print(f'  skipping {sha[:8]} ({date}): unreadable book — {e}', file=sys.stderr)
            continue
        for model_id, row in payload.get('models', {}).items():
            for tier, price in (row.get('tiers') or {}).items():
                runs = seen.setdefault((model_id, tier), [])
                if runs and rate_key(runs[-1][1]) == rate_key(price):
                    continue  # unchanged since the last commit
                runs.append((date, price))
    return seen


def main(argv: list[str]) -> int:
    write = '--write' in argv[1:]
    book = json.loads(BOOK.read_text())
    today = git('log', '-1', '--format=%ad', '--date=short').strip()
    runs = observations()

    changed = 0
    for model_id, row in book.get('models', {}).items():
        history: dict[str, list[dict]] = {}
        for tier, current in (row.get('tiers') or {}).items():
            observed = runs.get((model_id, tier), [])
            # An uncommitted working-tree rate is its own newest run, stamped
            # with the tip commit's date — the refresh that produced it has not
            # landed yet, so git cannot date it any better.
            if not observed or rate_key(observed[-1][1]) != rate_key(current):
                observed = [*observed, (today, current)]
            *older, (current_date, _) = observed
            if current.get('effective_from') != current_date:
                current['effective_from'] = current_date
                changed += 1
            for date, price in older:
                price = {k: v for k, v in price.items() if k != 'effective_from'}
                history.setdefault(tier, []).append({**price, 'effective_from': date})
        if history:
            if row.get('history') != history:
                changed += 1
            row['history'] = history
            n = sum(len(v) for v in history.values())
            print(f'  {model_id}: {n} superseded rate(s) '
                  f'+ current from {row["tiers"][next(iter(row["tiers"]))].get("effective_from")}')
        else:
            row.pop('history', None)

    if not write:
        print(f'\n{changed} field(s) would change. Nothing written; re-run with --write.')
        return 0
    BOOK.write_text(json.dumps(book, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'\nWrote {changed} field change(s) to {BOOK}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
