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
    AUTO_ANAT_REFERENCES,
    CorrectionMethod,
    GroupingPolicy,
)

REFERENCES = list(ANAT_REFERENCES.values())


def _codes(issues):
    return {issue.code for issue in issues}


def test_choices_are_the_meta_values_plus_the_table():
    assert ANAT_REFERENCE_CHOICES == ('none', 'auto', *ANAT_REFERENCES)
    assert len(set(ANAT_REFERENCE_CHOICES)) == len(ANAT_REFERENCE_CHOICES)


def test_auto_ladder_is_the_ranked_subset():
    # 'auto' picks by rank; explicit-only references (no rank) never appear.
    assert [ref.name for ref in AUTO_ANAT_REFERENCES] == ['synb0', 't2w']
    assert ANAT_REFERENCES['invt1w'].auto_rank is None


@pytest.mark.parametrize('reference', REFERENCES, ids=lambda r: r.name)
def test_reference_is_a_cli_choice_and_reads_into_the_policy(reference):
    parser = argparse.ArgumentParser()
    add_plan_arguments(parser)
    namespace = parser.parse_args(['--sdc-anat-reference', reference.name])
    assert policy_from_namespace(namespace).sdc_anat_reference == reference.name


@pytest.mark.parametrize('reference', REFERENCES, ids=lambda r: r.name)
def test_reference_round_trips_through_the_key_parser(reference):
    policy, _selection = parse_combined_key(
        f'sdc-anat-reference={reference.name}&hmc-method=eddy&sdc-method=topup'
    )
    assert policy.sdc_anat_reference == reference.name


@pytest.mark.parametrize('reference', REFERENCES, ids=lambda r: r.name)
def test_reference_is_in_the_explorer_grid_fallback_and_forced(reference):
    pairs = {(p.sdc_anat_reference, p.force_sdc_anat_reference) for p in reachable_policies()}
    assert (reference.name, False) in pairs
    assert (reference.name, True) in pairs


def test_every_reference_is_offered_in_the_dropdown(tmp_path):
    grouping = load_scenario('fieldmapless_t2w', tmp_path, strict=False)
    page = render_explorer_html(list(grouping.files.values()), '01')
    for reference in REFERENCES:
        assert f'<option value="sdc-anat-reference={reference.name}"' in page


@pytest.mark.parametrize('reference', REFERENCES, ids=lambda r: r.name)
def test_reference_creates_its_method_and_names_its_id_stem(tmp_path, reference):
    # fieldmapless_t2w carries both a T1w and a T2w, so every reference has
    # its source image; the estimation it creates must carry its method.
    grouping = load_scenario(
        'fieldmapless_t2w', tmp_path, strict=False, sdc_anat_reference=reference.name
    )
    (estimation,) = grouping.estimations.values()
    assert estimation.method is reference.method
    assert f'auto+{reference.id_stem}' in grouping.estimations


@pytest.mark.parametrize('reference', REFERENCES, ids=lambda r: r.name)
def test_missing_source_anatomy_emits_the_registered_error(tmp_path, reference):
    # A fieldmap-less subject with its anatomicals stripped: no fieldmap
    # reaches any series, so the reference fallback fires and must fail with
    # its own registered missing-anatomy code - never a KeyError.
    grouping = load_scenario('fieldmapless_t1w_only', tmp_path, strict=False)
    records = [record for record in grouping.files.values() if not record.is_anat]
    regrouped = build_grouping(records, subject_id='01', sdc_anat_reference=reference.name)
    assert reference.missing_anat_issue[0] in _codes(regrouped.errors)


@pytest.mark.parametrize(
    'reference', [r for r in REFERENCES if r.pedir_issue is not None], ids=lambda r: r.name
)
def test_missing_pedir_emits_the_registered_error(tmp_path, reference):
    grouping = load_scenario('fieldmapless_t2w', tmp_path, strict=False)
    records = [
        dataclasses.replace(record, signature=dataclasses.replace(record.signature, pe_dir=None))
        if record.is_dwi
        else record
        for record in grouping.files.values()
    ]
    regrouped = build_grouping(records, subject_id='01', sdc_anat_reference=reference.name)
    assert reference.pedir_issue[0] in _codes(regrouped.errors)


def test_t2w_is_a_pure_registration_needing_no_pe_direction():
    assert ANAT_REFERENCES['t2w'].pedir_issue is None
    assert ANAT_REFERENCES['t2w'].method is CorrectionMethod.T2WREG


def test_structural_target_kinds_are_registry_values():
    kinds = {ref.structural_target for ref in REFERENCES}
    assert kinds == {'synb0', 't2w', 't1w'}
    assert GroupingPolicy(sdc_anat_reference='synb0').sdc_anat_reference in ANAT_REFERENCES
