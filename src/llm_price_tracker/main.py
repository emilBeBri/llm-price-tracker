"""CLI.

Exit codes are the product here, not decoration. The bug that motivated this
tool was not a missing scraper — it was a scraper that had been exiting non-zero
for days with nobody consuming the signal, while a 5x price cut went unnoticed.
So: 0 clean, 1 a *watched* model's price moved, 2 a source broke. Wire it to a
timer that surfaces anything non-zero (see README).

Only watched models set exit 1. Everything else still gets fetched, diffed and
printed — it just does not fire the notification. `watch.toml` holds the split
and the reasoning; `--all` ignores it for a full audit.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .book import DATA_PATH, get_entry, load_book, save_book
from .compare import (
    Agreement,
    Drift,
    apply_deltas,
    diff_book,
    is_corroborated,
    reconcile,
)
from .models import STANDARD, Price, PriceBook
from .watch import WatchPolicy, WatchPolicyError, load_policy

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


def _price_cell(p: Price | None) -> str:
    """'in/out', with a ·peak marker when a time-of-day variant exists.

    The scalar columns show the off-peak rate; without the marker a viewer
    would read it as THE price and never learn the 2x peak window exists.
    """
    if p is None:
        return '—'
    s = f'{_money(p.input)}/{_money(p.output)}'
    return f'{s}·peak' if p.peak is not None else s


def _fetch_or_exit(timeout: float):
    try:
        from .sources import fetch_all, save_snapshots
    except ImportError as e:  # pragma: no cover - depends on install extras
        console.print(
            f'[red]Fetching needs the optional extra:[/] uv sync --extra fetch  ({e})'
        )
        raise typer.Exit(EXIT_SOURCE_BROKEN) from e
    results = fetch_all(timeout=timeout)
    try:
        save_snapshots(results)
    except OSError as e:  # snapshots are a debugging aid, never worth failing a run
        console.print(f'[dim]raw snapshot skipped: {e}[/]')
    return results


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


def _drift_table(deltas, title: str, title_style: str) -> Table:
    table = Table(
        title=title,
        title_style=title_style,
        title_justify='left',
        pad_edge=False,
    )
    table.add_column('model', style='cyan')
    table.add_column('drift')
    table.add_column('book', justify='right', style='yellow')
    table.add_column('live', justify='right', style='green')
    table.add_column('corroborated by', style='dim')
    for d in deltas:
        table.add_row(
            d.model_id,
            d.drift.value,
            _price_cell(d.old),
            _price_cell(d.new),
            ', '.join(d.verdict.corroborated_by) if d.verdict else '',
        )
    return table


@app.command()
def check(
    all_: Annotated[
        bool,
        typer.Option(
            '--all',
            help='Ignore the watch policy: every model alerts. The full audit.',
        ),
    ] = False,
    uncorroborated: Annotated[
        bool,
        typer.Option(help='Also count aggregator-only models as book drift.'),
    ] = False,
    timeout: Annotated[float, typer.Option(help='Per-request timeout (s).')] = 30.0,
    watch_config: Annotated[
        Path | None, typer.Option(help='Override the watch policy path.')
    ] = None,
    book_path: Annotated[
        Path | None, typer.Option(help='Override the book path.')
    ] = None,
) -> None:
    """Fetch every source, cross-check them, and diff against the book.

    Writes nothing. This is the command a timer runs.

    Exit 1 means a *watched* model moved (see `watch.toml`). A change to
    anything else is printed and exits 0 — reported, but not worth an alarm.
    """
    # Before the network, not after: a policy typo must not cost eight HTTPS
    # requests to discover, and it must never degrade into silence.
    try:
        policy = WatchPolicy.everything() if all_ else load_policy(watch_config)
    except WatchPolicyError as e:
        console.print(f'[red]Watch policy unusable:[/] {e}')
        raise typer.Exit(EXIT_SOURCE_BROKEN) from e

    results = _fetch_or_exit(timeout)
    sources_ok = _report_sources(results)

    verdicts = reconcile(results)
    all_conflicts = [v for v in verdicts.values() if v.agreement is Agreement.CONFLICT]
    # Only disagreements a primary document participates in get the full
    # table. Aggregator-vs-aggregator conflicts are permanent by nature —
    # OpenRouter's routing prices legitimately diverge from list prices — and
    # a table that shows the same benign rows every day teaches you to ignore
    # the day a real one appears.
    conflicts = [v for v in all_conflicts if v.vendor_value is not None]
    agg_only = [v for v in all_conflicts if v.vendor_value is None]

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
                    (
                        f'{_money(p.input)}/{_money(p.output)}/{_money(p.cache_read)}'
                        + ('·peak' if p.peak is not None else '')
                    )
                    if p
                    else '—'
                )
            table.add_row(*row)
        table.caption = 'input / output / cache-read, USD per 1M tokens'
        table.caption_justify = 'left'
        table.caption_style = 'dim'
        console.print(table)

    if agg_only:
        names = ', '.join(sorted(v.model_id for v in agg_only))
        console.print(
            f'[dim]{len(agg_only)} aggregator-only disagreement(s), no vendor '
            f'page involved (routing vs list prices): {names}[/]'
        )

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

    watched = [d for d in changed if policy.is_watched(d.model_id)]
    background = [d for d in changed if not policy.is_watched(d.model_id)]

    # Background table first, watched table last. The systemd unit's
    # ExecStopPost tails this output into the notification body, so the rows
    # that earned the alert have to be the ones nearest the bottom.
    if background:
        console.print(
            _drift_table(
                background,
                'Background models changed — reported, not alerted',
                'dim',
            )
        )
    if watched:
        console.print(
            _drift_table(watched, 'Watched models are out of date', 'bold red')
        )

    agree = sum(1 for v in verdicts.values() if v.agreement is Agreement.AGREE)
    single = sum(1 for v in verdicts.values() if v.agreement is Agreement.SINGLE)
    absent = sum(1 for d in deltas if d.drift is Drift.ABSENT)
    console.print(
        f'\n[bold]{len(verdicts)}[/] models seen · '
        f'[green]{agree}[/] corroborated by 2+ sources · '
        f'{single} single-source · [yellow]{len(all_conflicts)}[/] conflicting\n'
        f'[bold]{len(watched)}[/] watched row(s) out of date · '
        f'{len(background)} background row(s) changed (no alert) · '
        f'{absent} book row(s) no source mentioned · '
        f'{uncorroborated_count} aggregator-only model(s) not tracked'
    )

    # A broken source outranks the watch policy on purpose. A parser returning
    # nothing is exactly when filtering by model NAME is least trustworthy —
    # the watched model may be missing from the results because the page
    # changed, and a filter that reads the surviving names would conclude all
    # is well.
    if not sources_ok:
        raise typer.Exit(EXIT_SOURCE_BROKEN)
    if watched:
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
        old = _price_cell(d.old)
        new = _price_cell(d.new)
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

    updated = apply_deltas(
        book, deltas, updated_at=datetime.now(UTC).date().isoformat()
    )
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
    at: Annotated[
        str,
        typer.Option(
            help='Show the rates in force on this date (YYYY-MM-DD) instead of today.'
        ),
    ] = '',
) -> None:
    """Print the committed book. Offline — no network, no sources.

    `--at YYYY-MM-DD` reads the recorded rate history rather than the current
    rates: what a call on that date actually cost. A model whose rate has never
    moved (or moved before this book started recording) shows the same numbers
    either way; the `from` column says which observation you are looking at.
    """
    when: datetime | None = None
    if at:
        try:
            when = datetime.fromisoformat(at).replace(tzinfo=UTC)
        except ValueError:
            console.print(f'[red]--at must be YYYY-MM-DD, got {at!r}[/red]')
            raise typer.Exit(2) from None
    book = load_book(book_path)
    rows = {
        k: v
        for k, v in sorted(book.models.items())
        if not filter_ or filter_.lower() in k.lower()
    }
    table = Table(
        title=(
            f'Price book (rates in force {at})'
            if when
            else f'Price book (updated {book.updated_at})'
        ),
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
    table.add_column('from', style='dim')
    table.add_column('sources', style='dim')
    for k, entry in rows.items():
        p = entry.price_at(when) if when else entry.tiers.get(STANDARD)
        table.add_row(
            k,
            entry.vendor,
            _money(p.input) if p else '—',
            _money(p.output) if p else '—',
            _money(p.cache_read) if p else '—',
            (p.effective_from or 'pre-history') if p else '—',
            ', '.join(entry.sources),
        )
    console.print(table)
    console.print(f'{len(rows)} of {len(book.models)} model(s)')


def _percentage(value: float, reference: float | None) -> float | None:
    if reference is None or reference == 0:
        return None
    return value / reference * 100


def _percent(value: float | None) -> str:
    if value is None:
        return '—'
    return f'{value:,.3f}'.rstrip('0').rstrip('.') + '%'


def _relative_cell(value: float | None, reference: float | None) -> str:
    if value is None:
        return '—'
    return f'{_money(value)} · {_percent(_percentage(value, reference))}'


def _pick_model(
    book: PriceBook, prompt: str, *, exclude: str | None = None
) -> str | None:
    rows = []
    for model_id, entry in sorted(book.models.items()):
        price = entry.standard
        if price is None or model_id == exclude:
            continue
        # The first field is a hidden stable identity. Repeating the model id in
        # the display fields keeps it searchable while parsing remains exact.
        rows.append(
            '\t'.join(
                (
                    model_id,
                    model_id,
                    entry.vendor,
                    _money(price.input),
                    _money(price.output),
                    _money(price.cache_read),
                    _money(price.cache_write),
                )
            )
        )

    try:
        result = subprocess.run(
            [
                'fzf',
                '--reverse',
                '--cycle',
                '--no-multi',
                '--delimiter=\\t',
                '--with-nth=2..',
                '--prompt',
                f'{prompt}: ',
                '--header',
                'model\tvendor\tinput\toutput\tcache read\tcache write',
            ],
            input='\n'.join(rows) + '\n',
            text=True,
            stdout=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as e:
        console.print('[red]fzf is required for interactive model selection.[/]')
        raise typer.Exit(2) from e

    if result.returncode in (1, 130):
        return None
    if result.returncode != 0:
        console.print(f'[red]fzf failed with exit status {result.returncode}.[/]')
        raise typer.Exit(2)

    # Inherited fzf options may add metadata lines (`--expect`,
    # `--print-query`). The accepted row is always the final output line.
    output_lines = result.stdout.rstrip('\n').splitlines()
    return output_lines[-1].split('\t', 1)[0] if output_lines else None


def _resolve_model_id(model_id: str, book: PriceBook) -> str:
    entry = get_entry(model_id, book)
    if entry is None:
        console.print(f'[red]Unknown or ambiguous model:[/] {model_id}')
        raise typer.Exit(EXIT_DRIFT)
    if entry.standard is None:
        console.print(f'[red]Model has no standard price tier:[/] {entry.id}')
        raise typer.Exit(EXIT_DRIFT)
    return entry.id


@app.command()
def relative(
    reference: Annotated[
        str,
        typer.Argument(help="Reference model id, or 'fzf' to select interactively."),
    ],
    comparison: Annotated[
        str | None,
        typer.Argument(
            help="Optional model to compare, or 'fzf' to select interactively."
        ),
    ] = None,
    book_path: Annotated[
        Path | None, typer.Option(help='Override the book path.')
    ] = None,
) -> None:
    """Compare standard token rates as percentages of a reference model."""
    book = load_book(book_path)
    interactive_reference = reference.casefold() == 'fzf'

    if interactive_reference:
        reference_id = _pick_model(book, 'Reference model')
        if reference_id is None:
            raise typer.Exit(130)
    else:
        reference_id = _resolve_model_id(reference, book)

    interactive_comparison = (comparison is None and interactive_reference) or (
        comparison is not None and comparison.casefold() == 'fzf'
    )
    if interactive_comparison:
        comparison_id = _pick_model(
            book,
            'Comparison model',
            exclude=reference_id,
        )
        if comparison_id is None:
            raise typer.Exit(130)
    elif comparison is not None:
        comparison_id = _resolve_model_id(comparison, book)
    else:
        comparison_id = None

    reference_entry = book.models[reference_id]
    reference_price = reference_entry.standard
    assert reference_price is not None  # guaranteed by picker/resolution filters

    if comparison_id is not None:
        model_ids = [reference_id]
        if comparison_id != reference_id:
            model_ids.append(comparison_id)
    else:
        model_ids = [
            model_id
            for model_id, entry in book.models.items()
            if entry.standard is not None
        ]
        model_ids.sort(
            key=lambda model_id: (
                book.models[model_id].standard.input / reference_price.input
                if reference_price.input
                else float('inf'),
                model_id,
            )
        )

    table = Table(
        title=f'Standard prices relative to {reference_id}',
        title_justify='left',
        pad_edge=False,
        caption='USD per 1M tokens · each percentage is relative to the same column',
        caption_justify='left',
        caption_style='dim',
    )
    table.add_column('model', style='cyan')
    table.add_column('vendor', style='dim')
    table.add_column('input', justify='right')
    table.add_column('output', justify='right')
    table.add_column('cache read', justify='right')
    table.add_column('cache write', justify='right')

    for model_id in model_ids:
        entry = book.models[model_id]
        price = entry.standard
        assert price is not None  # model_ids contains standard-tier entries only
        style = 'bold green' if model_id == reference_id else None
        table.add_row(
            model_id,
            entry.vendor,
            _relative_cell(price.input, reference_price.input),
            _relative_cell(price.output, reference_price.output),
            _relative_cell(price.cache_read, reference_price.cache_read),
            _relative_cell(price.cache_write, reference_price.cache_write),
            style=style,
        )

    console.print(table)
    console.print(f'{len(model_ids)} model(s) · reference = 100%')


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
