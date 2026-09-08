"""The two-axis session model: the filter, the unit partition, and how the
anatomical-reference model plays out end to end on real BIDS trees.

These mirror the worked scenarios from the design: per-session anatomicals,
anat only in one session (HBCD-style), a shared session-less anatomical, and
mixed contrasts across sessions - each checked under the whole-subject models
and under ``sessionwise``.
"""

import json
from types import SimpleNamespace

import pytest

from qsiplan import index_subject
from qsiplan.catalog import Bids2TableCatalog, collect_subject_data
from qsiplan.cohort import build_cohort_data
from qsiplan.explorer import build_for_policy
from qsiplan.models import CorrectionMethod, GroupingPolicy
from qsiplan.scope import ProcessingUnit, SessionScope, plan_units
from qsiplan.serve import ExplorerApp


def _write_dataset(root, tree):
    """Build a BIDS tree from ``{subject: {session_or_None: [suffixes]}}``.

    Suffixes: ``'dwi'`` (with bval/bvec/PE sidecar), ``'T1w'``, ``'T2w'``. A
    ``None`` session places files directly under the subject (session-less).
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / 'dataset_description.json').write_text(
        json.dumps({'Name': 'scope-test', 'BIDSVersion': '1.10.0'})
    )
    (root / 'dwi.json').write_text(json.dumps({'TotalReadoutTime': 0.05}))
    for subject, sessions in tree.items():
        for session, suffixes in sessions.items():
            base = root / f'sub-{subject}'
            entities = f'sub-{subject}'
            if session is not None:
                base = base / f'ses-{session}'
                entities = f'{entities}_ses-{session}'
            for suffix in suffixes:
                if suffix == 'dwi':
                    datadir = base / 'dwi'
                    datadir.mkdir(parents=True, exist_ok=True)
                    stem = f'{entities}_dwi'
                    (datadir / f'{stem}.nii.gz').touch()
                    (datadir / f'{stem}.bval').write_text('0 1000\n')
                    (datadir / f'{stem}.bvec').write_text('0 1\n0 0\n0 0\n')
                    (datadir / f'{stem}.json').write_text(
                        json.dumps({'PhaseEncodingDirection': 'j-'})
                    )
                else:
                    datadir = base / 'anat'
                    datadir.mkdir(parents=True, exist_ok=True)
                    (datadir / f'{entities}_{suffix}.nii.gz').touch()
    return root


def _plan(root, subject, scope, **policy_kwargs):
    """Run one subject through catalog -> filter -> unit partition -> grouping.

    Returns ``{unit.label: DWIGrouping}``.
    """
    catalog = Bids2TableCatalog(root)
    data = collect_subject_data(catalog, subject, scope.session_filter)
    records, issues = index_subject(catalog, data)
    policy = GroupingPolicy(**policy_kwargs)
    return {
        unit.label: build_for_policy(unit_records, subject, policy, issues)
        for unit, unit_records in plan_units(subject, records, scope.model)
    }


def _codes(grouping):
    return {issue.code for issue in grouping.issues}


def _methods(grouping):
    return {est.method for est in grouping.estimations.values()}


# --------------------------------------------------------------------------- #
# The unit partition (pure, on stub records)
# --------------------------------------------------------------------------- #
def _rec(session, is_dwi=True):
    return SimpleNamespace(session=session, is_dwi=is_dwi)


def test_plan_units_subject_wide_is_one_unit_over_all_sessions():
    records = [_rec('01'), _rec('02'), _rec(None, is_dwi=False)]
    ((unit, unit_records),) = plan_units('01', records, 'first-lex')
    assert unit == ProcessingUnit('01', ('01', '02'))
    assert unit.label == 'sub-01'
    assert unit_records == records


def test_plan_units_sessionwise_isolates_each_session_with_shared_anat():
    shared = _rec(None, is_dwi=False)  # session-less anat: shared by every unit
    dwi1, dwi2 = _rec('01'), _rec('02')
    anat2 = _rec('02', is_dwi=False)
    units = plan_units('01', [dwi1, dwi2, anat2, shared], 'sessionwise')

    assert [unit.label for unit, _ in units] == ['sub-01_ses-01', 'sub-01_ses-02']
    (unit1, records1), (unit2, records2) = units
    assert unit1 == ProcessingUnit('01', ('01',), sessionwise=True)
    # ses-01: its own DWI plus the shared anat; nothing from ses-02.
    assert records1 == [dwi1, shared]
    # ses-02: its own DWI and anat, plus the shared anat.
    assert set(map(id, records2)) == {id(dwi2), id(anat2), id(shared)}


def test_plan_units_sessionwise_with_no_sessions_is_a_single_unit():
    records = [_rec(None), _rec(None, is_dwi=False)]
    ((unit, _),) = plan_units('01', records, 'sessionwise')
    assert unit == ProcessingUnit('01', None)
    assert unit.label == 'sub-01'


# --------------------------------------------------------------------------- #
# The catalog filter: uniform, but session-less anat/fmap is shared
# --------------------------------------------------------------------------- #
def test_catalog_filter_keeps_sessionless_anat_and_excludes_other_sessions(tmp_path):
    root = _write_dataset(tmp_path, {'01': {None: ['T1w'], '01': ['dwi'], '02': ['dwi']}})
    data = Bids2TableCatalog(root).subject_data('01', session_filter=('01',))
    assert len(data['dwi']) == 1
    assert '/ses-01/' in data['dwi'][0]
    assert len(data['t1w']) == 1
    assert '/ses-' not in data['t1w'][0]  # session-less anat kept


def test_catalog_filter_does_not_reach_another_sessions_anat(tmp_path):
    root = _write_dataset(tmp_path, {'01': {'01': ['dwi', 'T1w'], '02': ['dwi']}})
    data = Bids2TableCatalog(root).subject_data('01', session_filter=('02',))
    assert len(data['dwi']) == 1
    assert '/ses-02/' in data['dwi'][0]
    assert data['t1w'] == []  # ses-01's T1w is out of scope under a ses-02 filter


def test_catalog_sessions_enumerates_and_filters(tmp_path):
    root = _write_dataset(tmp_path, {'01': {'01': ['dwi'], '02': ['dwi'], '03': ['dwi']}})
    catalog = Bids2TableCatalog(root)
    assert catalog.sessions('01') == ['01', '02', '03']
    assert catalog.sessions('01', ('02', '03')) == ['02', '03']


# --------------------------------------------------------------------------- #
# Scenario A - each session has its own anatomical
# --------------------------------------------------------------------------- #
def test_scenario_a_per_session_t1w(tmp_path):
    root = _write_dataset(tmp_path, {'01': {'01': ['dwi', 'T1w'], '02': ['dwi', 'T1w']}})

    (wide,) = _plan(root, '01', SessionScope(), sdc_anat_reference='synb0').values()
    assert set(wide.estimations) == {'auto+synb0+ses-01', 'auto+synb0+ses-02'}
    assert 'cross-session-anat-reference' not in _codes(wide)

    sessionwise = _plan(root, '01', SessionScope(model='sessionwise'), sdc_anat_reference='synb0')
    assert set(sessionwise) == {'sub-01_ses-01', 'sub-01_ses-02'}
    assert list(sessionwise['sub-01_ses-01'].estimations) == ['auto+synb0+ses-01']
    assert 'cross-session-anat-reference' not in _codes(sessionwise['sub-01_ses-02'])


# --------------------------------------------------------------------------- #
# Scenario B - anatomical only in ses-01 (HBCD-style)
# --------------------------------------------------------------------------- #
def test_scenario_b_subject_wide_reaches_across_sessions_with_a_warning(tmp_path):
    root = _write_dataset(tmp_path, {'01': {'01': ['dwi', 'T1w'], '02': ['dwi']}})
    (wide,) = _plan(root, '01', SessionScope(), sdc_anat_reference='synb0').values()
    assert 'auto+synb0+ses-02' in wide.estimations  # ses-02 DWIs corrected...
    assert 'cross-session-anat-reference' in _codes(wide)  # ...using ses-01's T1w, warned
    assert 'synb0-requires-t1w' not in _codes(wide)


def test_scenario_b_sessionwise_isolates_and_errors(tmp_path):
    root = _write_dataset(tmp_path, {'01': {'01': ['dwi', 'T1w'], '02': ['dwi']}})
    sessionwise = _plan(root, '01', SessionScope(model='sessionwise'), sdc_anat_reference='synb0')
    assert 'synb0-requires-t1w' not in _codes(sessionwise['sub-01_ses-01'])
    assert 'synb0-requires-t1w' in _codes(sessionwise['sub-01_ses-02'])
    assert 'cross-session-anat-reference' not in _codes(sessionwise['sub-01_ses-02'])


def test_scenario_b_single_session_filter_matches_fmriprep(tmp_path):
    root = _write_dataset(tmp_path, {'01': {'01': ['dwi', 'T1w'], '02': ['dwi']}})
    (filtered,) = _plan(
        root, '01', SessionScope(session_filter=('02',)), sdc_anat_reference='synb0'
    ).values()
    # Filtering to ses-02 does not reach ses-01's T1w (uniform mask); no reach.
    assert 'synb0-requires-t1w' in _codes(filtered)
    assert 'cross-session-anat-reference' not in _codes(filtered)


# --------------------------------------------------------------------------- #
# Scenario C - a single shared, session-less anatomical
# --------------------------------------------------------------------------- #
def test_scenario_c_sessionless_anat_is_shared_not_crossed(tmp_path):
    root = _write_dataset(tmp_path, {'01': {None: ['T1w'], '01': ['dwi'], '02': ['dwi']}})
    for scope in (
        SessionScope(),
        SessionScope(model='sessionwise'),
        SessionScope(session_filter=('02',)),
    ):
        for grouping in _plan(root, '01', scope, sdc_anat_reference='synb0').values():
            assert CorrectionMethod.SYNB0 in _methods(grouping)
            assert 'synb0-requires-t1w' not in _codes(grouping)
            assert 'cross-session-anat-reference' not in _codes(grouping)


# --------------------------------------------------------------------------- #
# Scenario E - mixed contrasts: auto resolves per session, own contrast first
# --------------------------------------------------------------------------- #
def test_scenario_e_auto_prefers_each_sessions_own_contrast(tmp_path):
    root = _write_dataset(tmp_path, {'01': {'01': ['dwi', 'T1w'], '02': ['dwi', 'T2w']}})
    (wide,) = _plan(root, '01', SessionScope(), sdc_anat_reference='auto').values()

    # ses-01 -> synb0 (its own T1w); ses-02 -> T2Wreg (its own T2w), NOT a
    # cross-session synb0 borrowed from ses-01.
    assert wide.estimations['auto+synb0+ses-01'].method is CorrectionMethod.SYNB0
    assert wide.estimations['auto+t2wreg+ses-02'].method is CorrectionMethod.T2WREG
    assert 'cross-session-anat-reference' not in _codes(wide)


def test_scenario_e_sessionwise_matches_per_session_auto(tmp_path):
    root = _write_dataset(tmp_path, {'01': {'01': ['dwi', 'T1w'], '02': ['dwi', 'T2w']}})
    sessionwise = _plan(root, '01', SessionScope(model='sessionwise'), sdc_anat_reference='auto')
    assert _methods(sessionwise['sub-01_ses-01']) == {CorrectionMethod.SYNB0}
    assert _methods(sessionwise['sub-01_ses-02']) == {CorrectionMethod.T2WREG}


# --------------------------------------------------------------------------- #
# The dashboard (--cohort-html) honors both axes
# --------------------------------------------------------------------------- #
def test_cohort_subject_wide_drilldown_targets_the_subject(tmp_path):
    root = _write_dataset(tmp_path, {'01': {'01': ['dwi', 'T1w'], '02': ['dwi', 'T1w']}})
    data = build_cohort_data(Bids2TableCatalog(root), ['01'])
    assert {e['href'] for e in data['session']} == {'01'}  # one subject-wide page
    assert data['subject'][0]['href'] == '01'


def test_cohort_sessionwise_drilldown_targets_per_session_units(tmp_path):
    root = _write_dataset(tmp_path, {'01': {'01': ['dwi', 'T1w'], '02': ['dwi']}})
    data = build_cohort_data(
        Bids2TableCatalog(root),
        ['01'],
        scope=SessionScope(model='sessionwise'),
        policy=GroupingPolicy(sdc_anat_reference='synb0'),
    )
    # Each session row drills into its own unit page; the subject row into the first.
    assert {(e['session'], e['href']) for e in data['session']} == {
        ('01', '01_ses-01'),
        ('02', '01_ses-02'),
    }
    assert data['subject'][0]['href'] == '01_ses-01'


# --------------------------------------------------------------------------- #
# The live server (--serve) routes per unit
# --------------------------------------------------------------------------- #
def test_serve_subject_wide_routes_by_subject(tmp_path):
    root = _write_dataset(tmp_path, {'01': {'01': ['dwi', 'T1w'], '02': ['dwi', 'T1w']}})
    app = ExplorerApp(Bids2TableCatalog(root), ['01'])
    assert [unit.label for unit in app.units] == ['sub-01']
    assert '/sub-01/view' in app.page('01')
    with pytest.raises(KeyError):
        app.page('99')


def test_serve_sessionwise_routes_by_session_unit(tmp_path):
    root = _write_dataset(tmp_path, {'01': {'01': ['dwi', 'T1w'], '02': ['dwi']}})
    app = ExplorerApp(
        Bids2TableCatalog(root),
        ['01'],
        scope=SessionScope(model='sessionwise'),
        base_policy=GroupingPolicy(sdc_anat_reference='synb0'),
    )
    assert [unit.label for unit in app.units] == ['sub-01_ses-01', 'sub-01_ses-02']
    # Each unit routes to its own isolated live page/view.
    assert '/sub-01_ses-02/view' in app.page('01_ses-02')
    assert (
        app.view('01_ses-02', 'sdc-anat-reference=synb0&hmc-method=eddy&sdc-method=topup')[
            'policyKey'
        ]
        == 'sdc-anat-reference=synb0'
    )
    with pytest.raises(KeyError):
        app.page('01_ses-99')  # a session with no unit


# --------------------------------------------------------------------------- #
# The explorer page title/header names the session under sessionwise
# --------------------------------------------------------------------------- #
def test_explorer_page_title_carries_the_session(tmp_path):
    from qsiplan.interactive import render_explorer_html

    root = _write_dataset(tmp_path, {'01': {'01': ['dwi', 'T1w'], '02': ['dwi', 'T1w']}})
    catalog = Bids2TableCatalog(root)
    records, issues = index_subject(catalog, collect_subject_data(catalog, '01', None))

    whole = render_explorer_html(records, '01', index_issues=issues)
    assert '<title>DWI grouping explorer for sub-01</title>' in whole
    assert 'process sub-01&rsquo;s diffusion data' in whole  # h1 names no session

    ((_unit, ses02),) = [
        pair for pair in plan_units('01', records, 'sessionwise') if pair[0].session == '02'
    ]
    page = render_explorer_html(ses02, '01', session='02', index_issues=issues)
    assert '<title>DWI grouping explorer for sub-01_ses-02</title>' in page
    assert 'process sub-01 ses-02&rsquo;s diffusion data' in page  # h1 carries the session
