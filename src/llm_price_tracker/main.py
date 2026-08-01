"""CLI.

Exit codes are the product here, not decoration. The bug that motivated this
tool was not a missing scraper — it was a scraper that had been exiting non-zero
for days with nobody consuming the signal, while a 5x price cut went unnoticed.
So: 0 clean, 1 the book is out of date, 2 a source broke. Wire it to a timer
that surfaces anything non-zero (see README).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .book import DATA_PATH, load_book, save_book
from .compare import (
    Agreement,
    Drift,
    apply_deltas,
    diff_book,
    is_corroborated,
    reconcile,
)
from .models import STANDARD, PriceBook

app = typer.Typer(
    add_completion=False,
    help='Track published LLM prices across vendor pages and cross-check them.',
)
console = Console()

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_SOURCE_BROKEN = 2


def _money(v: float | None) -> str:
    if v is None:
        return '—'
    s = f'{v:.6f}'.rstrip('0').rstrip('.')
    return '$' + (s if '.' in s else s + '.00')


def _fetch_or_exit(timeout: float):
    try:
        from .sources import fetch_all
    except ImportError as e:  # pragma: no cover - depends on install extras
        console.print(
            f'[red]Fetching needs the optional extra:[/] uv sync --extra fetch  ({e})'
        )
        raise typer.Exit(EXIT_SOURCE_BROKEN) from e
    return fetch_all(timeout=timeout)


def _report_sources(results) -> bool:
    table = Table(title='Sources', title_justify='left', pad_edge=False)
    table.add_column('source', style='cyan')
    table.add_column('status')
    table.add_column('models', justify='right')
    table.add_column('note', style='dim', overflow='fold')
    for r in results:
        table.add_row(
            r.source,
            '[green]ok[/]' if r.ok else '[red]BROKEN[/]',
            str(len(r.prices)),
            r.note or '',
        )
    console.print(table)
    return all(r.ok for r in results)


@app.command()
def check(
    uncorroborated: Annotated[
        bool,
        typer.Option(help='Also count aggregator-only models as book drift.'),
    ] = False,
    timeout: Annotated[float, typer.Option(help='Per-request timeout (s).')] = 30.0,
    book_path: Annotated[
        Path | None, typer.Option(help='Override the book path.')
    ] = None,
) -> None:
    """Fetch every source, cross-check them, and diff against the book.

    Writes nothing. This is the command a timer runs.
    """
    results = _fetch_or_exit(timeout)
    sources_ok = _report_sources(results)

    verdicts = reconcile(results)
    conflicts = [v for v in verdicts.values() if v.agreement is Agreement.CONFLICT]

    if conflicts:
        table = Table(
            title='Sources disagree — check the vendor page before trusting either',
            title_style='bold yellow',
            title_justify='left',
            pad_edge=False,
        )
        table.add_column('model', style='cyan')
        for name in sorted({n for v in conflicts for n in v.values}):
            table.add_column(name, justify='right')
        names = sorted({n for v in conflicts for n in v.values})
        for v in sorted(conflicts, key=lambda x: x.model_id):
            row = [v.model_id]
            for n in names:
                p = v.values.get(n)
                # in/out/cache, all three. Showing only in/out made a cache-only
                # conflict render as two identical cells flagged as a conflict,
                # which reads as a false positive and teaches you to ignore the
                # table. DeepSeek's cache rate is the live example: the vendor
                # says $0.0028 and the aggregator says $0.028, 10x apart, while
                # input and output agree exactly.
                row.append(
                    f'{_money(p.input)}/{_money(p.output)}/{_money(p.cache_read)}'
                    if p
                    else '—'
                )
            table.add_row(*row)
        table.caption = 'input / output / cache-read, USD per 1M tokens'
        table.caption_justify = 'left'
        table.caption_style = 'dim'
        console.print(table)

    book = load_book(book_path)
    deltas = diff_book(book, verdicts)
    changed = [d for d in deltas if d.drift in (Drift.NEW, Drift.CHANGED)]

    # Must apply the SAME corroboration filter `refresh` uses. Without it this
    # command reports ~85 permanent "out of date" rows that refresh then
    # refuses to write — a red light that can never go green. A signal nobody
    # can act on is the exact failure this tool was built to stop.
    uncorroborated_count = sum(1 for d in changed if not is_corroborated(d))
    if not uncorroborated:
        changed = [d for d in changed if is_corroborated(d)]

    if changed:
        table = Table(
            title='Book is out of date',
            title_style='bold red',
            title_justify='left',
            pad_edge=False,
        )
        table.add_column('model', style='cyan')
        table.add_column('drift')
        table.add_column('book', justify='right', style='yellow')
        table.add_column('live', justify='right', style='green')
        table.add_column('corroborated by', style='dim')
        for d in changed:
            old = f'{_money(d.old.input)}/{_money(d.old.output)}' if d.old else '—'
            new = f'{_money(d.new.input)}/{_money(d.new.output)}' if d.new else '—'
            table.add_row(
                d.model_id,
                d.drift.value,
                old,
                new,
                ', '.join(d.verdict.corroborated_by) if d.verdict else '',
            )
        console.print(table)

    agree = sum(1 for v in verdicts.values() if v.agreement is Agreement.AGREE)
    single = sum(1 for v in verdicts.values() if v.agreement is Agreement.SINGLE)
    absent = sum(1 for d in deltas if d.drift is Drift.ABSENT)
    console.print(
        f'\n[bold]{len(verdicts)}[/] models seen · '
        f'[green]{agree}[/] corroborated by 2+ sources · '
        f'{single} single-source · [yellow]{len(conflicts)}[/] conflicting\n'
        f'[bold]{len(changed)}[/] book row(s) out of date · '
        f'{absent} book row(s) no source mentioned · '
        f'{uncorroborated_count} aggregator-only model(s) not tracked'
    )

    if not sources_ok:
        raise typer.Exit(EXIT_SOURCE_BROKEN)
    if changed:
        raise typer.Exit(EXIT_DRIFT)


@app.command()
def refresh(
    write: Annotated[bool, typer.Option(help='Actually write the book.')] = False,
    uncorroborated: Annotated[
        bool,
        typer.Option(
            help='Also record models only the aggregator feed knows (unverified).'
        ),
    ] = False,
    timeout: Annotated[float, typer.Option(help='Per-request timeout (s).')] = 30.0,
    book_path: Annotated[
        Path | None, typer.Option(help='Override the book path.')
    ] = None,
) -> None:
    """Fold live prices into the book. Requires --write to touch anything."""
    results = _fetch_or_exit(timeout)
    _report_sources(results)

    verdicts = reconcile(results)
    book = load_book(book_path)
    deltas = diff_book(book, verdicts)
    changed = [d for d in deltas if d.drift in (Drift.NEW, Drift.CHANGED)]

    if not uncorroborated:
        kept = [d for d in changed if is_corroborated(d)]
        skipped = len(changed) - len(kept)
        if skipped:
            console.print(
                f'[dim]Skipping {skipped} model(s) no first-party page corroborates '
                f'(--uncorroborated to include them).[/]'
            )
        changed, deltas = kept, [d for d in deltas if is_corroborated(d)]

    for d in changed:
        old = f'{_money(d.old.input)}/{_money(d.old.output)}' if d.old else '—'
        new = f'{_money(d.new.input)}/{_money(d.new.output)}' if d.new else '—'
        console.print(f'  {d.drift.value:8} {d.model_id:34} {old:>18} -> {new}')

    if not changed:
        console.print('[green]Book already matches every source.[/]')
        return

    if not write:
        console.print(
            f'\n[yellow]{len(changed)} change(s) proposed. Nothing written.[/] '
            'Re-run with --write once the diff looks right.'
        )
        raise typer.Exit(EXIT_DRIFT)

    updated = apply_deltas(book, deltas, updated_at=datetime.now(UTC).date().isoformat())
    save_book(updated, book_path)
    console.print(
        f'\n[green]Wrote {len(changed)} change(s)[/] to {book_path or DATA_PATH}'
    )


@app.command()
def show(
    filter_: Annotated[str, typer.Argument(metavar='[FILTER]')] = '',
    book_path: Annotated[
        Path | None, typer.Option(help='Override the book path.')
    ] = None,
) -> None:
    """Print the committed book. Offline — no network, no sources."""
    book = load_book(book_path)
    rows = {
        k: v
        for k, v in sorted(book.models.items())
        if not filter_ or filter_.lower() in k.lower()
    }
    table = Table(
        title=f'Price book (updated {book.updated_at})',
        title_justify='left',
        pad_edge=False,
        caption=str(book_path or DATA_PATH),
        caption_justify='left',
        caption_style='dim',
    )
    table.add_column('model', style='cyan')
    table.add_column('vendor', style='dim')
    table.add_column('input', justify='right')
    table.add_column('output', justify='right')
    table.add_column('cache read', justify='right', style='dim')
    table.add_column('sources', style='dim')
    for k, entry in rows.items():
        p = entry.tiers.get(STANDARD)
        table.add_row(
            k,
            entry.vendor,
            _money(p.input) if p else '—',
            _money(p.output) if p else '—',
            _money(p.cache_read) if p else '—',
            ', '.join(entry.sources),
        )
    console.print(table)
    console.print(f'{len(rows)} of {len(book.models)} model(s)')


@app.command()
def init(
    book_path: Annotated[
        Path | None, typer.Option(help='Override the book path.')
    ] = None,
) -> None:
    """Create an empty book. Only needed once, to bootstrap a fresh checkout."""
    target = book_path or DATA_PATH
    if target.exists():
        console.print(f'[yellow]{target} already exists — refusing to clobber it.[/]')
        raise typer.Exit(EXIT_DRIFT)
    save_book(PriceBook(updated_at=datetime.now(UTC).date().isoformat()), target)
    console.print(
        f'[green]Created[/] {target}. Now run: llm-price-tracker refresh --write'
    )


if __name__ == '__main__':
    app()
