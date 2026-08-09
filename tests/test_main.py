from __future__ import annotations

import subprocess

import pytest
from rich.console import Console
from typer.testing import CliRunner

from llm_price_tracker import ModelEntry, Price, PriceBook, main, save_book

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
