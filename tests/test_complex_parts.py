"""Complex-valued (``part-mag`` + ``part-phase``) acquisitions.

A phase image is the *companion* of its magnitude, never a series of its own:
it is split out at enumeration (:func:`qsiplan.metadata._split_parts`), rides
on the magnitude's :class:`~qsiplan.models.FileRecord`, and reaches the
workflow through :attr:`~qsiplan.adapters.PreprocUnit.dwi_phase_files` and
the emitted plan. Real/imaginary parts, and a phase with no magnitude, are
ignored with a warning. These tests pin that contract end to end.
"""

import json
import os.path as op
from glob import glob

import pytest
from grouping_scenarios import build_layout, load_scenario

from qsiplan import build_dwi_grouping, concatenation_scheme, full_report, to_preproc_units
from qsiplan.adapters import _unit_scan_grouping, plan_preproc_units, unit_to_sidecar
from qsiplan.integrity import check_model_integrity, check_plan
from qsiplan.methods import selection_for_config
from qsiplan.models import CorrectionMethod, Provenance
from qsiplan.plan import compile_plan
from qsiplan.validation import describe_issue

EDDY_TOPUP = selection_for_config('eddy', 'topup')
TORTOISE_DRBUDDI = selection_for_config('tortoise', 'drbuddi')


def _names(paths):
    return sorted(op.basename(path) for path in paths)


def _issues(grouping, code):
    return [issue for issue in grouping.issues if issue.code == code]


def _phase_name(magnitude):
    return op.basename(magnitude).replace('part-mag', 'part-phase')


def test_phase_is_a_companion_not_a_series(tmp_path):
    """A mag+phase DWI corrected by a mag+phase epi fieldmap (IntendedFor)."""
    grouping = load_scenario('complex_pepolar', tmp_path, strict=False)

    # Only magnitudes are indexed - in dwi/ and in fmap/ alike.
    assert _names(grouping.dwi_files) == ['sub-01_dir-AP_part-mag_dwi.nii.gz']
    assert not [path for path in grouping.files if 'part-phase' in path]
    (dwi,) = grouping.dwi_files
    assert grouping.files[dwi].part == 'mag'
    assert op.basename(grouping.files[dwi].phase_path) == _phase_name(dwi)
    (epi,) = [path for path, rec in grouping.files.items() if rec.suffix == 'epi']
    assert op.basename(grouping.files[epi].phase_path) == 'sub-01_dir-PA_part-phase_epi.nii.gz'

    # IntendedFor named both parts: it resolves to the magnitude, once, and
    # the estimation is the magnitude epi plus the magnitude DWI - no phase.
    assert not _issues(grouping, 'intendedfor-missing-target')
    (estimation,) = grouping.estimations.values()
    assert estimation.method is CorrectionMethod.PEPOLAR
    assert estimation.provenance is Provenance.TRANSLATED
    assert _names(estimation.sources) == [
        'sub-01_dir-AP_part-mag_dwi.nii.gz',
        'sub-01_dir-PA_part-mag_epi.nii.gz',
    ]
    assert check_model_integrity(grouping) == []

    # The unit the workflow consumes: magnitude members, phase as a lookup,
    # and no part- token in any output name.
    (unit,) = to_preproc_units(grouping, EDDY_TOPUP)
    assert unit.dwi_files == (dwi,)
    assert unit.dwi_phase_files == {dwi: grouping.files[dwi].phase_path}
    assert 'part-' not in unit.output_name
    assert all(
        'part-' not in name
        for pair in concatenation_scheme(grouping, EDDY_TOPUP).items()
        for name in pair
    )
    assert _names(unit.extra_b0) == ['sub-01_dir-PA_part-mag_epi.nii.gz']
    assert not [path for path in unit.sidecar_overrides() if 'part-phase' in path]
    scan_grouping = _unit_scan_grouping(unit)
    assert scan_grouping['dwi_series'] == ['sub-01_dir-AP_part-mag_dwi.nii.gz']
    assert scan_grouping['dwi_phase_series'] == ['sub-01_dir-AP_part-phase_dwi.nii.gz']
    assert not [name for name in unit_to_sidecar(unit)['Sources'] if 'part-phase' in str(name)]


def test_magnitude_only_units_carry_no_phase_entries(tmp_path):
    (unit,) = to_preproc_units(load_scenario('abcd_style', tmp_path, strict=False), EDDY_TOPUP)
    assert unit.dwi_phase_files == {}
    assert 'dwi_phase_series' not in _unit_scan_grouping(unit)


def test_real_and_imaginary_parts_are_ignored_with_a_warning(tmp_path):
    grouping = load_scenario('complex_realimag', tmp_path, strict=False)

    dropped = _issues(grouping, 'complex-part-unsupported')
    assert sorted(op.basename(f) for issue in dropped for f in issue.files) == [
        'sub-01_dir-AP_part-imag_dwi.nii.gz',
        'sub-01_dir-AP_part-real_dwi.nii.gz',
    ]
    assert {issue.severity for issue in dropped} == {'warning'}
    assert 'ignored' in describe_issue('complex-part-unsupported')

    assert _names(grouping.dwi_files) == [
        'sub-01_dir-AP_part-mag_dwi.nii.gz',
        'sub-01_dir-PA_part-mag_dwi.nii.gz',
    ]
    assert not [
        path
        for path in grouping.files
        if any(t in path for t in ('part-real', 'part-imag', 'part-phase'))
    ]
    (estimation,) = grouping.estimations.values()
    assert estimation.bidirectional_axes == {'j'}
    (unit,) = to_preproc_units(grouping, EDDY_TOPUP)
    assert set(unit.dwi_phase_files) == set(unit.dwi_files)
    assert check_model_integrity(grouping) == []


def test_orphan_phase_is_ignored_with_a_warning(tmp_path):
    grouping = load_scenario('complex_orphan_phase', tmp_path, strict=False)

    (issue,) = _issues(grouping, 'phase-without-magnitude')
    assert issue.severity == 'warning'
    assert _names(issue.files) == ['sub-01_dir-PA_part-phase_dwi.nii.gz']
    assert _names(grouping.dwi_files) == ['sub-01_dir-AP_part-mag_dwi.nii.gz']
    (dwi,) = grouping.dwi_files
    assert op.basename(grouping.files[dwi].phase_path) == _phase_name(dwi)
    # The PA magnitude never existed, so there is no reverse-PE partner.
    assert not grouping.estimations


def test_intendedfor_naming_only_the_phase_means_its_magnitude(tmp_path):
    layout, subject_data = build_layout('complex_pepolar', tmp_path)
    (epi_json,) = glob(
        str(tmp_path / 'complex_pepolar' / 'sub-01' / 'fmap' / '*part-mag_epi.json')
    )
    with open(epi_json) as fobj:
        metadata = json.load(fobj)
    metadata['IntendedFor'] = ['dwi/sub-01_dir-AP_part-phase_dwi.nii.gz']
    with open(epi_json, 'w') as fobj:
        json.dump(metadata, fobj)

    grouping = build_dwi_grouping(layout, subject_data, strict=False)
    assert not _issues(grouping, 'intendedfor-missing-target')
    (estimation,) = grouping.estimations.values()
    assert estimation.provenance is Provenance.TRANSLATED
    assert 'sub-01_dir-AP_part-mag_dwi.nii.gz' in _names(estimation.sources)


@pytest.mark.parametrize('selection', [EDDY_TOPUP, TORTOISE_DRBUDDI], ids=['eddy', 'tortoise'])
def test_plan_carries_the_phase_companions(tmp_path, selection):
    """Every run - decomposed or not - maps each magnitude to its phase."""
    grouping = load_scenario('nibs_style', tmp_path, strict=False)
    plan = compile_plan(grouping, selection)
    assert check_plan(grouping, plan) == []
    assert plan.runs
    for run in plan.runs:
        assert set(run.dwi_phase_files) == set(run.dwi_files)
        for magnitude, phase in run.dwi_phase_files.items():
            assert op.basename(phase) == _phase_name(magnitude)

    payload = plan.to_dict()
    assert payload['schema_version'] == 2
    assert all(run['dwi_phase_files'] for run in payload['runs'])
    json.dumps(payload)

    for unit, run in zip(plan_preproc_units(grouping, plan), plan.runs, strict=True):
        assert unit.dwi_phase_files == run.dwi_phase_files


def test_grouping_serialization_carries_parts(tmp_path):
    payload = load_scenario('nibs_style', tmp_path / 'complex', strict=False).to_dict()
    assert payload['schema_version'] == 2
    dwi_entries = {p: e for p, e in payload['files'].items() if e['datatype'] == 'dwi'}
    assert dwi_entries
    for path, entry in dwi_entries.items():
        assert entry['part'] == 'mag'
        assert op.basename(entry['phase_path']) == _phase_name(path)

    plain = load_scenario('hcp_style', tmp_path / 'plain', strict=False).to_dict()
    assert {(e['part'], e['phase_path']) for e in plain['files'].values()} == {(None, None)}


def test_report_names_the_phase_companion_but_never_lists_it(tmp_path):
    report = full_report(load_scenario('nibs_style', tmp_path, strict=False))
    assert '      with phase image sub-01_dir-AP_part-phase_dwi.nii.gz' in report
    assert '- sub-01_dir-AP_part-phase_dwi.nii.gz' not in report
    assert 'Output "sub-01"' in report
