"""The anatomical-reference registry is the single source for the reference set.

Every surface that spells ``--sdc-anat-reference`` values - the shared CLI
spec, the combined-key parser, the explorer grid and dropdown, the inference
ladder and its error messages, and the structural-target vocabulary - must
derive from :data:`~qsiplan.models.ANAT_REFERENCES`. These conformance tests
walk the registry and assert each reference is reachable everywhere, so a
reference added to the table but left unwired somewhere fails here, in CI,
instead of surfacing as a KeyError on one entry path (as ``'auto'`` once did).
"""

import argparse
import dataclasses

import pytest
from grouping_scenarios import load_scenario

from qsiplan.cli_spec import add_plan_arguments, policy_from_namespace
from qsiplan.explorer import reachable_policies
from qsiplan.inference import build_grouping
from qsiplan.interactive import render_explorer_html
from qsiplan.methods import parse_combined_key
from qsiplan.models import (
    ANAT_REFERENCE_CHOICES,
    ANAT_REFERENCES,
    FIELDMAPLESS_METHOD_CHOICES,
    FIELDMAPLESS_METHODS,
    CorrectionMethod,
    GroupingPolicy,
)

REFERENCES = list(ANAT_REFERENCES.values())
#: Every valid (fieldmap-less method, reference) pair.
PAIRS = [
    (method, ANAT_REFERENCES[name])
    for method in FIELDMAPLESS_METHODS.values()
    for name in method.references
]


def _pair_id(pair):
    method, reference = pair
    return f'{method.name}+{reference.name}'


def _codes(issues):
    return {issue.code for issue in issues}


def test_choices_are_none_plus_the_tables():
    assert ANAT_REFERENCE_CHOICES == ('none', *ANAT_REFERENCES)
    assert FIELDMAPLESS_METHOD_CHOICES == tuple(FIELDMAPLESS_METHODS)
    assert GroupingPolicy().fieldmapless_method == 'topup'
    assert GroupingPolicy().sdc_anat_reference == 'synb0'


def test_every_reference_is_used_by_some_method():
    used = {name for method in FIELDMAPLESS_METHODS.values() for name in method.references}
    assert used == set(ANAT_REFERENCES)


@pytest.mark.parametrize('pair', PAIRS, ids=_pair_id)
def test_pair_is_a_cli_choice_and_reads_into_the_policy(pair):
    method, reference = pair
    parser = argparse.ArgumentParser()
    add_plan_arguments(parser)
    namespace = parser.parse_args(
        ['--sdc-fieldmapless-method', method.name, '--sdc-anat-reference', reference.name]
    )
    policy = policy_from_namespace(namespace)
    assert (policy.fieldmapless_method, policy.sdc_anat_reference) == (method.name, reference.name)


@pytest.mark.parametrize('pair', PAIRS, ids=_pair_id)
def test_pair_round_trips_through_the_key_parser(pair):
    method, reference = pair
    policy, _selection = parse_combined_key(
        f'fieldmapless-method={method.name}&sdc-anat-reference={reference.name}'
        '&hmc-method=eddy&sdc-method=topup'
    )
    assert (policy.fieldmapless_method, policy.sdc_anat_reference) == (method.name, reference.name)


@pytest.mark.parametrize('pair', PAIRS, ids=_pair_id)
def test_pair_is_in_the_explorer_grid_fallback_and_forced(pair):
    method, reference = pair
    triples = {
        (p.fieldmapless_method, p.sdc_anat_reference, p.force_sdc_anat_reference)
        for p in reachable_policies()
    }
    assert (method.name, reference.name, False) in triples
    assert (method.name, reference.name, True) in triples


def test_every_pair_is_offered_in_the_dropdown(tmp_path):
    grouping = load_scenario('fieldmapless_t2w', tmp_path, strict=False)
    page = render_explorer_html(list(grouping.files.values()), '01')
    assert '<option value="sdc-anat-reference=none"' in page
    for method, reference in PAIRS:
        flags = []
        if method.name != 'topup':
            flags.append(f'--sdc-fieldmapless-method {method.name}')
        if reference.name != 'synb0':
            flags.append(f'--sdc-anat-reference {reference.name}')
        label = ' '.join(flags) or 'topup + synb0 (default)'
        assert f'>{label}</option>' in page


@pytest.mark.parametrize('pair', PAIRS, ids=_pair_id)
def test_pair_creates_its_method_and_names_its_id_stem(tmp_path, pair):
    # fieldmapless_t2w carries both a T1w and a T2w, so every pair has its
    # source image; the estimation it creates must carry the method and reference.
    method, reference = pair
    grouping = load_scenario(
        'fieldmapless_t2w',
        tmp_path,
        strict=False,
        sdc_anat_reference=reference.name,
        fieldmapless_method=method.name,
    )
    (estimation,) = grouping.estimations.values()
    assert estimation.method is method.method
    assert estimation.reference == reference.name
    assert estimation.structural_target == reference.structural_target
    assert f'auto+{method.id_stems[reference.name]}' in grouping.estimations


def test_invalid_pairs_are_rejected():
    for method in FIELDMAPLESS_METHODS.values():
        for name in ANAT_REFERENCES:
            if name in method.references:
                continue
            with pytest.raises(ValueError, match='cannot use'):
                build_grouping(
                    [], subject_id='01', sdc_anat_reference=name, fieldmapless_method=method.name
                )


@pytest.mark.parametrize('pair', PAIRS, ids=_pair_id)
def test_missing_source_anatomy_emits_the_registered_error(tmp_path, pair):
    # A fieldmap-less subject with its anatomicals stripped: no fieldmap
    # reaches any series, so the fallback fires and must fail with the
    # reference's own registered missing-anatomy code - never a KeyError.
    method, reference = pair
    grouping = load_scenario('fieldmapless_t1w_only', tmp_path, strict=False)
    records = [record for record in grouping.files.values() if not record.is_anat]
    regrouped = build_grouping(
        records,
        subject_id='01',
        sdc_anat_reference=reference.name,
        fieldmapless_method=method.name,
    )
    assert reference.missing_anat_issue[0] in _codes(regrouped.errors)


@pytest.mark.parametrize(
    'method', [m for m in FIELDMAPLESS_METHODS.values() if m.pedir_issue], ids=lambda m: m.name
)
def test_missing_pedir_emits_the_registered_error(tmp_path, method):
    grouping = load_scenario('fieldmapless_t2w', tmp_path, strict=False)
    records = [
        dataclasses.replace(record, signature=dataclasses.replace(record.signature, pe_dir=None))
        if record.is_dwi
        else record
        for record in grouping.files.values()
    ]
    regrouped = build_grouping(
        records,
        subject_id='01',
        sdc_anat_reference=method.references[0],
        fieldmapless_method=method.name,
    )
    assert method.pedir_issue[0] in _codes(regrouped.errors)


def test_topup_needs_a_readout_time(tmp_path):
    grouping = load_scenario('fieldmapless_t2w', tmp_path, strict=False)
    records = [
        dataclasses.replace(
            record, signature=dataclasses.replace(record.signature, readout_time=None)
        )
        if record.is_dwi
        else record
        for record in grouping.files.values()
    ]
    regrouped = build_grouping(records, subject_id='01')
    assert 'synb0-missing-readout' in _codes(regrouped.errors)
    assert not regrouped.estimations


def test_unpile_needs_the_phase_encoding_direction_and_readout_time():
    assert FIELDMAPLESS_METHODS['unpile'].pedir_issue is not None
    assert FIELDMAPLESS_METHODS['unpile'].readout_issue is not None
    assert FIELDMAPLESS_METHODS['unpile'].method is CorrectionMethod.UNPILE


def test_structural_target_kinds_are_registry_values():
    kinds = {ref.structural_target for ref in REFERENCES}
    assert kinds == {'synb0', 't2w', 't1w'}
