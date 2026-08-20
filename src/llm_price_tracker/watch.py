"""Which price changes are worth waking someone up for.

Pure — stdlib only, no network, no pydantic. This is the one module here that
holds an *operational preference* rather than a vendor fact, and that is
exactly why it is a separate file with a separate config: whether
`gemini-2.0-flash` still matters is a property of the person running the
tracker, not of Google's pricing page. Stamping an `important: true` flag onto
94 data rows would smuggle that preference into the committed artifact and
force a book rewrite every time the rotation changes.

The alerting tier is deliberately NOT a data filter. `refresh` still folds
every corroborated change into the book, because a cost estimate for an
unwatched model must still be right. This module only decides which changes
break the exit code and therefore reach a desktop notification.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / 'data' / 'watch.toml'

# A model's version is the FIRST dotted-numeric run after its family prefix.
# First, not last: `gpt-3.5-turbo-0125`, `gpt-4-0613` and
# `gemini-2.5-computer-use-preview-10-2025` all carry a trailing build date
# that would otherwise read as a version far above any threshold. Searching
# rather than anchoring is what lets one rule cover both vendor id shapes —
# OpenAI/Google/Z.ai put the version first (`gpt-5.6-luna`), Anthropic puts it
# last (`claude-opus-4.6`), and DeepSeek and Moonshot prefix a letter
# (`deepseek-v4-pro`, `kimi-k3`).
_VERSION_RE = re.compile(r'\d+(?:\.\d+)*')

_TOP_KEYS = frozenset({'ids', 'exclude_ids', 'families'})
_FAMILY_KEYS = frozenset({'name', 'min_version'})


class WatchPolicyError(ValueError):
    """The policy file is missing, malformed, or matches nothing.

    All three are fatal rather than a silent fallback. A policy that matches
    nothing turns every alert off, which is precisely the failure this tracker
    exists to prevent — a scraper that reported nothing to nobody while a 5x
    price cut went by. Broken config must be as loud as a broken parser.
    """


def parse_version(text: str) -> tuple[int, ...]:
    """'5.5' -> (5, 5). Tuple order is the version order: 5 < 5.1 < 5.5 < 6."""
    try:
        return tuple(int(part) for part in text.split('.'))
    except ValueError as e:
        raise WatchPolicyError(f'not a version: {text!r}') from e


@dataclass(frozen=True)
class FamilyRule:
    """`name` at or above `min_version` is watched; anything older is not."""

    name: str
    min_version: tuple[int, ...]

    def matches(self, model_id: str) -> bool:
        # The separator is required: a bare prefix test would let a future
        # `gptx-1` answer to the `gpt` rule.
        if not model_id.startswith(self.name + '-'):
            return False
        found = _VERSION_RE.search(model_id[len(self.name) :])
        if found is None:
            return False  # `gemini-omni-flash-preview` — unversioned, unwatched
        return parse_version(found.group()) >= self.min_version


@dataclass(frozen=True)
class WatchPolicy:
    """The two-tier split: watched models alert, everything else is reported."""

    families: tuple[FamilyRule, ...] = ()
    ids: tuple[str, ...] = ()
    exclude_ids: tuple[str, ...] = ()

    @classmethod
    def everything(cls) -> WatchPolicy:
        """The `--all` audit policy: every model is tier 1."""
        return cls(ids=('*',))

    def is_watched(self, model_id: str) -> bool:
        model_id = model_id.lower()
        # Exclusions win. They exist for ids whose leading number is not a
        # version at all — `gpt-oss-120b` would otherwise read as gpt 120 and
        # clear every threshold ever set.
        if any(fnmatchcase(model_id, p) for p in self.exclude_ids):
            return False
        if any(fnmatchcase(model_id, p) for p in self.ids):
            return True
        return any(rule.matches(model_id) for rule in self.families)


def _family_rule(entry: object, index: int) -> FamilyRule:
    where = f'[[families]] #{index + 1}'
    if not isinstance(entry, dict):
        raise WatchPolicyError(f'{where} is not a table')
    unknown = set(entry) - _FAMILY_KEYS
    if unknown:
        raise WatchPolicyError(f'{where} has unknown key(s): {sorted(unknown)}')
    missing = _FAMILY_KEYS - set(entry)
    if missing:
        raise WatchPolicyError(f'{where} is missing key(s): {sorted(missing)}')
    return FamilyRule(
        str(entry['name']).lower(), parse_version(str(entry['min_version']))
    )


def _patterns(raw: dict, key: str) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise WatchPolicyError(f'{key} must be a list of glob strings')
    return tuple(v.lower() for v in value)


def load_policy(path: Path | None = None) -> WatchPolicy:
    """Read the policy file. Every failure raises — see `WatchPolicyError`."""
    target = path or CONFIG_PATH
    try:
        raw = tomllib.loads(target.read_text(encoding='utf-8'))
    except OSError as e:
        raise WatchPolicyError(f'cannot read watch policy {target}: {e}') from e
    except tomllib.TOMLDecodeError as e:
        raise WatchPolicyError(f'malformed watch policy {target}: {e}') from e

    # Unknown keys are rejected rather than ignored: a typo'd `id = [...]` that
    # silently parsed to an empty allowlist would quietly stop alerting.
    unknown = set(raw) - _TOP_KEYS
    if unknown:
        raise WatchPolicyError(f'{target} has unknown key(s): {sorted(unknown)}')

    families_raw = raw.get('families', [])
    if not isinstance(families_raw, list):
        raise WatchPolicyError(f'{target}: families must be a list of tables')
    policy = WatchPolicy(
        families=tuple(_family_rule(e, i) for i, e in enumerate(families_raw)),
        ids=_patterns(raw, 'ids'),
        exclude_ids=_patterns(raw, 'exclude_ids'),
    )
    if not policy.families and not policy.ids:
        raise WatchPolicyError(
            f'{target} watches nothing — that silences every alert. '
            'Add a [[families]] rule or an id glob.'
        )
    return policy
