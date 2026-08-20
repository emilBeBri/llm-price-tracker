"""The watch policy decides what stays silent, so its failure mode is silence.

Every id used here is a real row from the committed book. That is the point:
the question "is this a version I still use?" is answered by id shapes that
differ per vendor and per era — OpenAI leads with the version, Anthropic
trails it, DeepSeek and Moonshot prefix a letter, and half of Google's catalog
carries a build date that looks like one.
"""

from __future__ import annotations

import pytest

from llm_price_tracker.watch import (
    FamilyRule,
    WatchPolicy,
    WatchPolicyError,
    load_policy,
)

WATCHED = [
    'gpt-5.5',
    'gpt-5.5-pro',
    'gpt-5.6-terra',
    'claude-haiku-4.5',
    'claude-opus-4.6',
    'claude-sonnet-5',
    'claude-fable-5',
    'claude-mythos-5',
    'gemini-3.1-flash-lite',
    'gemini-3.7-flash',
    'glm-5',
    'glm-5.2',
    'deepseek-v4-pro',
    'kimi-k3',
]

BACKGROUND = [
    'gpt-5.4',
    'gpt-5.4-pro',
    'gpt-4o',
    'gpt-3.5-turbo-0125',
    'o3',
    'babbage-002',
    'claude-opus-4',
    'claude-opus-4.1',
    'claude-haiku-3.5',
    'gemini-2.5-pro',
    'gemini-3-flash-preview',
    'gemini-robotics-er-2',
    'gemini-omni-flash-preview',
    'glm-4.7',
    'glm-4-32b-0414-128k',
]


@pytest.fixture(scope='module')
def shipped() -> WatchPolicy:
    return load_policy()


@pytest.mark.parametrize('model_id', WATCHED)
def test_shipped_policy_watches_the_current_rotation(shipped, model_id):
    assert shipped.is_watched(model_id)


@pytest.mark.parametrize('model_id', BACKGROUND)
def test_shipped_policy_leaves_retired_generations_quiet(shipped, model_id):
    assert not shipped.is_watched(model_id)


def test_version_is_found_wherever_the_vendor_puts_it():
    """OpenAI leads with it, Anthropic trails it, DeepSeek prefixes a `v`."""
    assert FamilyRule('gpt', (5, 5)).matches('gpt-5.6-luna')
    assert FamilyRule('claude', (4, 5)).matches('claude-opus-4.6')
    assert FamilyRule('deepseek', (4,)).matches('deepseek-v4-flash')


def test_a_trailing_build_date_is_not_a_version():
    """`0125` and `10-2025` would clear every threshold ever written."""
    assert not FamilyRule('gpt', (5, 5)).matches('gpt-3.5-turbo-0125')
    assert not FamilyRule('gpt', (5, 5)).matches('gpt-4-0613')
    assert not FamilyRule('gemini', (3, 1)).matches(
        'gemini-2.5-computer-use-preview-10-2025'
    )


def test_a_shorter_version_sorts_below_a_longer_one():
    """`glm-5` is watched at min 5 but `gpt-5` is not watched at min 5.5."""
    assert FamilyRule('glm', (5,)).matches('glm-5')
    assert not FamilyRule('gpt', (5, 5)).matches('gpt-5')
    assert FamilyRule('gpt', (5, 5)).matches('gpt-5.5')


def test_an_unversioned_id_is_not_watched():
    assert not FamilyRule('gemini', (3, 1)).matches('gemini-omni-flash-preview')


def test_the_family_prefix_requires_a_separator():
    assert not FamilyRule('gpt', (5, 5)).matches('gptx-9')


def test_exclusion_beats_a_family_threshold():
    """`gpt-oss-120b` reads as gpt 120 — the one shape the version scan can't
    tell from a real version, so it is defused by id, not by heuristic."""
    rule = FamilyRule('gpt', (5, 5))
    assert rule.matches('gpt-oss-120b')
    policy = WatchPolicy(families=(rule,), exclude_ids=('gpt-oss*',))
    assert not policy.is_watched('gpt-oss-120b')
    assert policy.is_watched('gpt-5.6-sol')


def test_an_id_glob_watches_regardless_of_version():
    policy = WatchPolicy(ids=('gemini-2.0-*',))
    assert policy.is_watched('gemini-2.0-flash-lite')
    assert not policy.is_watched('gemini-2.5-pro')


def test_everything_watches_everything():
    policy = WatchPolicy.everything()
    assert all(policy.is_watched(m) for m in WATCHED + BACKGROUND)


def _policy_file(tmp_path, body: str):
    path = tmp_path / 'watch.toml'
    path.write_text(body, encoding='utf-8')
    return path


def test_a_policy_that_matches_nothing_is_refused(tmp_path):
    """An empty policy silences every alert, which is the failure this whole
    repository exists to prevent. It must not be a valid configuration."""
    with pytest.raises(WatchPolicyError, match='watches nothing'):
        load_policy(_policy_file(tmp_path, 'ids = []\nexclude_ids = []\n'))


def test_a_typo_is_refused_rather_than_ignored(tmp_path):
    """`id` instead of `ids` would parse to an empty allowlist and go quiet."""
    with pytest.raises(WatchPolicyError, match='unknown key'):
        load_policy(_policy_file(tmp_path, 'id = ["gpt-*"]\n'))


def test_a_malformed_family_is_refused(tmp_path):
    with pytest.raises(WatchPolicyError, match='missing key'):
        load_policy(_policy_file(tmp_path, '[[families]]\nname = "gpt"\n'))
    with pytest.raises(WatchPolicyError, match='not a version'):
        load_policy(
            _policy_file(
                tmp_path, '[[families]]\nname = "gpt"\nmin_version = "latest"\n'
            )
        )


def test_a_missing_or_broken_file_is_refused(tmp_path):
    with pytest.raises(WatchPolicyError, match='cannot read'):
        load_policy(tmp_path / 'absent.toml')
    with pytest.raises(WatchPolicyError, match='malformed'):
        load_policy(_policy_file(tmp_path, 'ids = [unquoted]\n'))
