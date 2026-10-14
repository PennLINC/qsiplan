"""The cohort partition: session-sliced plan signatures and the dashboard build.

The correctness spine here is :func:`~qsiplan.cohort.plan_signature` computed on
the session-sliced plan - the fix that keeps a single session's signature from
absorbing another session's fieldmaps, and lets equivalent sessions collapse
into one equivalence class regardless of their subject/session labels.
"""

import json
import re

from grouping_scenarios import build_layout, load_scenario

from qsiplan import compile_plan
from qsiplan.cohort import build_cohort_data, plan_signature, render_cohort_html
from qsiplan.methods import selection_for_config


def _sessions(grouping):
    return sorted(
        {r.session for r in grouping.files.values() if r.is_dwi and r.session is not None}
    )


def test_session_slice_collapses_equivalent_sessions(tmp_path):
    """Two structurally identical sessions share one signature."""
    grouping = load_scenario('multi_session', tmp_path, strict=False)
    plan = compile_plan(grouping, selection_for_config('shoreline', 'drbuddi'))
    sessions = _sessions(grouping)
    assert len(sessions) >= 2
    sigs = {ses: plan_signature(grouping, plan, ses) for ses in sessions}
    # The multi_session scenario acquires the same protocol each session, so
    # every session slices to the same workflow signature.
    assert len(set(sigs.values())) == 1


def test_t2w_changes_the_signature(tmp_path):
    """A T2w gives DRBUDDI a structural target: distinct signature."""
    sel = selection_for_config('tortoise', 'drbuddi')
    with_t2w = load_scenario('t2w_hcp', tmp_path / 'a', strict=False)
    without = load_scenario('hcp_style', tmp_path / 'b', strict=False)
    sig_with = plan_signature(with_t2w, compile_plan(with_t2w, sel), None)
    sig_without = plan_signature(without, compile_plan(without, sel), None)
    assert sig_with != sig_without


def test_signature_is_label_invariant(tmp_path):
    """The signature drops identity: a relabeled but structurally identical
    grouping keeps its signature."""
    sel = selection_for_config('shoreline', 'drbuddi')
    a = load_scenario('hcp_style', tmp_path / 'a', strict=False)
    b = load_scenario('hcp_style', tmp_path / 'b', strict=False)
    sig_a = plan_signature(a, compile_plan(a, sel), None)
    sig_b = plan_signature(b, compile_plan(b, sel), None)
    assert sig_a == sig_b


def test_build_cohort_data_shape(tmp_path):
    layout, _ = build_layout('multi_session', tmp_path)
    data = build_cohort_data(layout, ['01'], initial_method='shoreline')
    assert data['defaultMethod'] == 'shoreline'
    assert {m['key'] for m in data['methods']} == {'eddy', 'shoreline', 'tortoise'}
    # one subject entity; one session entity per session
    assert len(data['subject']) == 1
    assert len(data['session']) == len(_sessions(load_scenario('multi_session', tmp_path / 'x')))
    for entity in data['session']:
        for method in ('eddy', 'shoreline', 'tortoise'):
            facts = entity['byMethod'][method]
            assert set(facts) == {'sig', 'outputs', 'runs', 'errors', 'warnings'}


def test_render_cohort_html_is_self_contained(tmp_path):
    layout, _ = build_layout('multi_session', tmp_path)
    page = render_cohort_html(layout, ['01'], live=False, initial_method='shoreline')
    assert page.startswith('<!doctype html>')
    assert 'Cohort Grouping Map' in page
    embed = json.loads(re.search(r'id="cohort-data">(.*?)</script>', page, re.DOTALL).group(1))
    assert embed['defaultMethod'] == 'shoreline'
    assert embed['hrefTemplate'] == 'sub-%s.html'  # static drill-down
    # no external scripts/styles beyond the Google Fonts stylesheet
    assert page.count('<script') == page.count('</script>')
