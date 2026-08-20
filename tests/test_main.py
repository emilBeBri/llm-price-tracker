from __future__ import annotations

import subprocess

import pytest
from rich.console import Console
from typer.testing import CliRunner

from llm_price_tracker import ModelEntry, Price, PriceBook, main, save_book
from llm_price_tracker.sources.base import SourceResult

runner = CliRunner()


@pytest.fixture(autouse=True)
def wide_console(monkeypatch):
    monkeypatch.setattr(main, 'console', Console(width=180, color_system=None))


@pytest.fixture()
def book_path(tmp_path):
    path = tmp_path / 'prices.json'
    save_book(
        PriceBook(
            updated_at='2026-08-09',
            models={
                'reference-model': ModelEntry(
                    id='reference-model',
                    vendor='acme',
                    tiers={
                        'standard': Price(
                            input=2.0,
                            output=4.0,
                            cache_read=1.0,
                        )
                    },
                ),
                'cheaper-model': ModelEntry(
                    id='cheaper-model',
                    vendor='budget',
                    tiers={
                        'standard': Price(
                            input=1.0,
                            output=8.0,
                            cache_write=0.5,
                        )
                    },
                ),
                'expensive-model': ModelEntry(
                    id='expensive-model',
                    vendor='luxury',
                    tiers={
                        'standard': Price(
                            input=8.0,
                            output=2.0,
                            cache_read=2.0,
                        )
                    },
                ),
            },
        ),
        path,
    )
    return path


def test_relative_compares_each_rate_independently(book_path):
    result = runner.invoke(
        main.app,
        [
            'relative',
            'reference-model',
            'cheaper-model',
            '--book-path',
            str(book_path),
        ],
    )

    assert result.exit_code == 0
    assert 'reference-model' in result.stdout
    assert 'cheaper-model' in result.stdout
    assert '$1.00 · 50%' in result.stdout
    assert '$8.00 · 200%' in result.stdout
    assert '$0.5 · —' in result.stdout
    assert '2 model(s) · reference = 100%' in result.stdout


def test_relative_without_comparison_lists_all_models_by_input_cost(book_path):
    result = runner.invoke(
        main.app,
        ['relative', 'reference-model', '--book-path', str(book_path)],
    )

    assert result.exit_code == 0
    assert result.stdout.index('cheaper-model') < result.stdout.rindex(
        'reference-model'
    )
    assert result.stdout.rindex('reference-model') < result.stdout.index(
        'expensive-model'
    )
    assert '3 model(s) · reference = 100%' in result.stdout


def test_relative_fzf_selects_reference_then_one_comparison(book_path, monkeypatch):
    calls = []
    selections = iter(('reference-model', 'expensive-model'))

    def pick_model(book, prompt, *, exclude=None):
        calls.append((prompt, exclude))
        return next(selections)

    monkeypatch.setattr(main, '_pick_model', pick_model)

    result = runner.invoke(
        main.app,
        ['relative', 'fzf', '--book-path', str(book_path)],
    )

    assert result.exit_code == 0
    assert calls == [
        ('Reference model', None),
        ('Comparison model', 'reference-model'),
    ]
    assert 'reference-model' in result.stdout
    assert 'expensive-model' in result.stdout
    assert 'cheaper-model' not in result.stdout
    assert '2 model(s) · reference = 100%' in result.stdout


def test_relative_rejects_an_unknown_reference(book_path):
    result = runner.invoke(
        main.app,
        ['relative', 'imaginary-model', '--book-path', str(book_path)],
    )

    assert result.exit_code == 1
    assert 'Unknown or ambiguous model: imaginary-model' in result.stdout


def test_pick_model_uses_hidden_exact_id_and_leaves_terminal_for_fzf(monkeypatch):
    book = PriceBook(
        updated_at='2026-08-09',
        models={
            'reference-model': ModelEntry(
                id='reference-model',
                vendor='acme',
                tiers={'standard': Price(input=2.0, output=4.0)},
            )
        },
    )
    seen = {}

    def run(args, **kwargs):
        seen['args'] = args
        seen['kwargs'] = kwargs
        return subprocess.CompletedProcess(
            args,
            returncode=0,
            # Inherited --expect output must not be mistaken for the row id.
            stdout='enter\nreference-model\treference-model\tacme\n',
        )

    monkeypatch.setattr(main.subprocess, 'run', run)

    selected = main._pick_model(book, 'Reference model')

    assert selected == 'reference-model'
    assert '--with-nth=2..' in seen['args']
    assert seen['kwargs']['stdout'] is subprocess.PIPE
    assert 'stderr' not in seen['kwargs']
    assert seen['kwargs']['input'].startswith(
        'reference-model\treference-model\tacme\t'
    )


def test_relative_cell_keeps_absolute_rate_when_reference_rate_is_unavailable():
    assert main._relative_cell(0.5, None) == '$0.5 · —'
    assert main._relative_cell(0.5, 0.0) == '$0.5 · —'


# --- check: the two-tier alert split -----------------------------------------
#
# The tracker's exit code is its entire product, and this is the only place a
# price change is allowed to stop producing one. So each test below fixes one
# way the split could go quiet when it should shout.


@pytest.fixture()
def alert_book_path(tmp_path):
    path = tmp_path / 'alert-prices.json'
    save_book(
        PriceBook(
            updated_at='2026-08-18',
            models={
                'gpt-5.6-terra': ModelEntry(
                    id='gpt-5.6-terra',
                    vendor='openai',
                    tiers={'standard': Price(input=1.0, output=6.0)},
                ),
                'gemini-2.0-flash': ModelEntry(
                    id='gemini-2.0-flash',
                    vendor='google',
                    tiers={'standard': Price(input=0.1, output=0.4)},
                ),
            },
        ),
        path,
    )
    return path


def _fetched(prices_by_source, ok=True):
    return [
        SourceResult(name, f'https://{name}/', ok, prices)
        for name, prices in prices_by_source.items()
    ]


def _stub_fetch(monkeypatch, results):
    monkeypatch.setattr(main, '_fetch_or_exit', lambda timeout: results)


BOOK_TERRA = {'gpt-5.6-terra': Price(input=1.0, output=6.0)}
BOOK_GEMINI = {'gemini-2.0-flash': Price(input=0.1, output=0.4)}
CUT_TERRA = {'gpt-5.6-terra': Price(input=0.2, output=1.2)}
CUT_GEMINI = {'gemini-2.0-flash': Price(input=0.05, output=0.2)}


def test_a_background_model_moving_does_not_raise_the_alarm(
    alert_book_path, monkeypatch
):
    _stub_fetch(monkeypatch, _fetched({'openai': BOOK_TERRA, 'google': CUT_GEMINI}))

    result = runner.invoke(main.app, ['check', '--book-path', str(alert_book_path)])

    assert result.exit_code == 0
    assert 'Background models changed' in result.stdout
    assert 'Watched models are out of date' not in result.stdout
    assert '1 background row(s) changed' in result.stdout


def test_a_watched_model_moving_exits_one(alert_book_path, monkeypatch):
    """The live incident: GPT-5.6 cut 5x, noticed three days late."""
    _stub_fetch(monkeypatch, _fetched({'openai': CUT_TERRA, 'google': BOOK_GEMINI}))

    result = runner.invoke(main.app, ['check', '--book-path', str(alert_book_path)])

    assert result.exit_code == 1
    assert 'Watched models are out of date' in result.stdout
    assert 'gpt-5.6-terra' in result.stdout


def test_all_promotes_every_model_for_an_audit(alert_book_path, monkeypatch):
    _stub_fetch(monkeypatch, _fetched({'openai': BOOK_TERRA, 'google': CUT_GEMINI}))

    result = runner.invoke(
        main.app, ['check', '--all', '--book-path', str(alert_book_path)]
    )

    assert result.exit_code == 1
    assert 'Watched models are out of date' in result.stdout
    assert 'Background models changed' not in result.stdout


def test_the_watched_table_is_printed_last(alert_book_path, monkeypatch):
    """ExecStopPost tails the captured output into the notification body, so
    the rows that earned the alert have to be the ones nearest the bottom."""
    _stub_fetch(monkeypatch, _fetched({'openai': CUT_TERRA, 'google': CUT_GEMINI}))

    result = runner.invoke(main.app, ['check', '--book-path', str(alert_book_path)])

    assert result.exit_code == 1
    assert result.stdout.index('Background models changed') < result.stdout.index(
        'Watched models are out of date'
    )


def test_a_broken_source_outranks_a_quiet_watch_list(alert_book_path, monkeypatch):
    """A parser returning nothing is exactly when filtering by model name is
    least trustworthy: the watched model may be missing BECAUSE it broke."""
    _stub_fetch(
        monkeypatch,
        _fetched({'google': CUT_GEMINI}) + _fetched({'openai': {}}, ok=False),
    )

    result = runner.invoke(main.app, ['check', '--book-path', str(alert_book_path)])

    assert result.exit_code == 2


def test_an_unusable_policy_fails_before_the_network(
    alert_book_path, tmp_path, monkeypatch
):
    def never(timeout):
        raise AssertionError('fetched despite an unusable watch policy')

    monkeypatch.setattr(main, '_fetch_or_exit', never)
    empty = tmp_path / 'watch.toml'
    empty.write_text('ids = []\n', encoding='utf-8')

    result = runner.invoke(
        main.app,
        [
            'check',
            '--watch-config',
            str(empty),
            '--book-path',
            str(alert_book_path),
        ],
    )

    assert result.exit_code == 2
    assert 'Watch policy unusable' in result.stdout
