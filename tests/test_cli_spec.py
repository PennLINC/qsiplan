"""The shared plan-CLI spec: internal consistency and argparse round-trips.

The spec (:mod:`qsiplan.cli_spec`) is the single description of the
plan-relevant CLI surface; qsiplan and qsiprep both build (or validate) their
parsers from it. These tests pin the spec against qsiplan's own truth - the
:class:`~qsiplan.models.GroupingPolicy` fields and the argparse round-trip - so
a flag added on one side or a field left unspelled fails here, in CI, instead
of drifting silently across the split (as ``selection_for_config``'s signature
already did once). qsiprep carries the mirror-image test against its parser.
"""

import argparse
import dataclasses

import pytest

from qsiplan.cli_spec import (
    PLAN_OPTIONS,
    Axis,
    Kind,
    add_plan_arguments,
    policy_from_namespace,
    scope_from_namespace,
    selection_from_namespace,
)
from qsiplan.models import GroupingPolicy
from qsiplan.scope import ANAT_MODELS, SessionScope


def _parser():
    parser = argparse.ArgumentParser()
    add_plan_arguments(parser)
    return parser


def _policy_fields():
    return {field.name for field in dataclasses.fields(GroupingPolicy)}


def test_policy_options_cover_every_grouping_policy_field():
    # Every GroupingPolicy field must be reachable from some flag, so adding a
    # policy knob without a CLI spelling fails CI here.
    covered = set()
    for option in PLAN_OPTIONS:
        if option.axis is not Axis.POLICY:
            continue
        if option.policy_field:
            covered.add(option.policy_field)
        covered.update(field for _, field in option.members)
    assert covered == _policy_fields()


def test_every_named_target_is_a_real_policy_field():
    fields = _policy_fields()
    for option in PLAN_OPTIONS:
        if option.axis is not Axis.POLICY:
            continue
        if option.policy_field:
            assert option.policy_field in fields, option.flag
        for value, field in option.members:
            assert field in fields, f'{option.flag} {value}'


def test_method_options_name_real_selection_arguments():
    assert {opt.selection_arg for opt in PLAN_OPTIONS if opt.axis is Axis.METHOD} == {
        'hmc',
        'sdc',
        'shoreline_model',
    }


def test_default_namespace_is_the_default_policy():
    assert policy_from_namespace(_parser().parse_args([])) == GroupingPolicy()


def test_policy_from_namespace_tolerates_missing_flags():
    # A consumer can omit a flag it has not wired up (or a config object that
    # predates it); the missing attribute falls back to its default.
    namespace = argparse.Namespace(
        separate_all_dwis=True,
        ignore=['fieldmaps'],
        force=[],
        distortion_group_merge='concat',
        # note: no `sdc_anat_reference` attribute at all
    )
    policy = policy_from_namespace(namespace)
    assert policy == GroupingPolicy(separate_all_dwis=True, ignore_fieldmaps=True)
    assert policy.sdc_anat_reference == 'none'


def test_ignore_is_one_list_flag_toggling_the_right_fields():
    policy = policy_from_namespace(_parser().parse_args(['--ignore', 'fieldmaps', 'shims']))
    assert policy.ignore_fieldmaps
    assert policy.ignore_shims
    assert not policy.ignore_fov
    assert not policy.ignore_sdc


def test_ignore_rejects_values_qsiplan_does_not_own():
    # 'phase' is a qsiprep-only --ignore choice; qsiplan's parser must not
    # silently accept it (it would map to no grouping field). 't2w' *is* owned
    # by qsiplan now (it drops the T2Wreg fieldmap-less fallback).
    with pytest.raises(SystemExit):
        _parser().parse_args(['--ignore', 'phase'])
    assert policy_from_namespace(_parser().parse_args(['--ignore', 't2w'])).ignore_t2w


def test_sdc_anat_reference_choice_reads_into_the_policy():
    parser = _parser()
    assert policy_from_namespace(parser.parse_args([])).sdc_anat_reference == 'none'
    chosen = parser.parse_args(['--sdc-anat-reference', 'synb0'])
    assert policy_from_namespace(chosen).sdc_anat_reference == 'synb0'
    with pytest.raises(SystemExit):
        parser.parse_args(['--sdc-anat-reference', 'bogus'])


def test_force_sdc_anat_reference_reads_into_the_policy():
    parser = _parser()
    assert policy_from_namespace(parser.parse_args([])).force_sdc_anat_reference is False
    namespace = parser.parse_args(['--sdc-anat-reference', 't2w', '--force', 'sdc-anat-reference'])
    policy = policy_from_namespace(namespace)
    assert policy.sdc_anat_reference == 't2w'
    assert policy.force_sdc_anat_reference is True


def test_selection_from_namespace_bridges_to_selection_for_config():
    parser = _parser()
    assert selection_from_namespace(parser.parse_args([])) is None
    namespace = parser.parse_args(
        ['--hmc-method', 'shoreline', '--shoreline-model', 'tensor', '--sdc-method', 'drbuddi']
    )
    selection = selection_from_namespace(namespace)
    assert (
        selection.combination_key()
        == 'hmc-method=shoreline&sdc-method=drbuddi&shoreline-model=tensor'
    )


def test_shoreline_model_requires_shoreline():
    namespace = _parser().parse_args(['--hmc-method', 'eddy', '--shoreline-model', 'tensor'])
    with pytest.raises(ValueError, match='requires --hmc-method shoreline'):
        selection_from_namespace(namespace)


@pytest.mark.parametrize(
    'policy',
    [
        GroupingPolicy(),
        GroupingPolicy(ignore_fieldmaps=True, ignore_shims=True),
        GroupingPolicy(ignore_pepolar_dwis=True, ignore_fieldmaps=True),
        GroupingPolicy(ignore_sdc=True, separate_all_dwis=True),
        GroupingPolicy(sdc_anat_reference='invt1w', distortion_group_merge='none'),
        GroupingPolicy(sdc_anat_reference='t2w', force_sdc_anat_reference=True),
    ],
)
def test_cli_phrase_round_trips_through_the_parser(policy):
    # A policy -> its qsiprep flags -> parse -> the same policy. This closes the
    # loop between the display (cli_phrase) and the input (the spec parser).
    namespace = _parser().parse_args(policy.cli_phrase().split())
    assert policy_from_namespace(namespace) == policy


def _scope_parser():
    # A consumer's parser owns --session-label; the spec adds the model flag.
    parser = argparse.ArgumentParser()
    parser.add_argument('--session-label', nargs='+')
    add_plan_arguments(parser)
    return parser


def test_scope_options_name_a_real_session_scope_field():
    fields = {field.name for field in dataclasses.fields(SessionScope)}
    for option in PLAN_OPTIONS:
        if option.axis is Axis.SCOPE:
            assert option.scope_field in fields, option.flag


def test_default_namespace_is_the_default_scope():
    assert scope_from_namespace(_scope_parser().parse_args([])) == SessionScope()


def test_subject_anatomical_reference_reads_into_the_scope():
    parser = _scope_parser()
    assert scope_from_namespace(parser.parse_args([])).model == 'first-lex'
    for model in ANAT_MODELS:
        namespace = parser.parse_args(['--subject-anatomical-reference', model])
        assert scope_from_namespace(namespace).model == model
    with pytest.raises(SystemExit):
        parser.parse_args(['--subject-anatomical-reference', 'bogus'])


def test_session_label_is_read_as_a_filter():
    parser = _scope_parser()
    scope = scope_from_namespace(parser.parse_args(['--session-label', '01', '02']))
    assert scope.session_filter == ('01', '02')
    assert scope_from_namespace(parser.parse_args([])).session_filter is None


def test_scope_flags_do_not_leak_into_the_policy():
    # The scope axis feeds SessionScope, never GroupingPolicy: a sessionwise
    # run under the default policy is still the default policy.
    namespace = _scope_parser().parse_args(['--subject-anatomical-reference', 'sessionwise'])
    assert policy_from_namespace(namespace) == GroupingPolicy()
    assert scope_from_namespace(namespace).sessionwise


def test_owned_choices_are_the_conformance_contract():
    # The choices a consumer must accept, spelled per kind.
    by_flag = {opt.flag: opt for opt in PLAN_OPTIONS}
    assert set(by_flag['--ignore'].owned_choices()) == {
        'fieldmaps',
        'pepolar-dwis',
        't2w',
        'sdc',
        'shims',
        'fov',
    }
    assert by_flag['--ignore'].extendable  # qsiprep adds phase
    assert set(by_flag['--force'].owned_choices()) == {'sdc-anat-reference'}
    assert set(by_flag['--sdc-anat-reference'].owned_choices()) == {
        'none',
        'auto',
        'synb0',
        't2w',
        'invt1w',
    }
    assert by_flag['--hmc-method'].kind is Kind.CHOICE
