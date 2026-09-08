"""The explorer's policy grid: canonical keys, content dedupe, and the page.

The tier-2 contract under test: a flag combination's canonical key is flat
and sorted; the policy axis regroups the data while the method axis only
recompiles the plan; and the explorer page embeds one rendering per
*distinct* grouping, addressed through policy-key -> signature indirection.
"""

import json
import re

from grouping_scenarios import build_layout

from qsiplan import index_subject, render_explorer_html
from qsiplan.explorer import (
    build_for_policy,
    build_policy_grid,
    drop_fieldmaps,
    grouping_signature,
    reachable_policies,
)
from qsiplan.inference import build_grouping
from qsiplan.methods import combined_key, reachable_selections, selection_for_config
from qsiplan.models import GroupingPolicy
from qsiplan.plan import compile_plan


def _indexed(scenario, tmp_path):
    layout, subject_data = build_layout(scenario, tmp_path)
    return index_subject(layout, subject_data)


def test_policy_key_is_sorted_and_non_default_only():
    assert GroupingPolicy().policy_key() == ''
    policy = GroupingPolicy(
        separate_all_dwis=True, ignore_fov=True, distortion_group_merge='average'
    )
    assert policy.policy_key() == 'distortion-group-merge=average&ignore-fov=1&separate-all-dwis=1'


def test_selection_key_is_sorted():
    selection = selection_for_config('shoreline', 'drbuddi')
    assert (
        selection.combination_key()
        == 'hmc-method=shoreline&sdc-method=drbuddi&shoreline-model=3dshore'
    )


def test_combined_key_composes_both_axes():
    policy = GroupingPolicy(sdc_anat_reference='synb0')
    selection = selection_for_config('eddy', 'topup')
    assert (
        combined_key(policy, selection)
        == 'hmc-method=eddy&sdc-anat-reference=synb0&sdc-method=topup'
    )
    assert combined_key(GroupingPolicy(), selection) == selection.combination_key()


def test_reachable_policies_cover_the_grid():
    policies = reachable_policies()
    # separate + ignore(fieldmaps, pepolar-dwis, t2w, shims, fov)
    # + anat-sdc (reference, forced) pairs(9) + merge(3)
    assert len(policies) == 2 * 2 * 2 * 2 * 2 * 2 * 9 * 3
    keys = {policy.policy_key() for policy in policies}
    assert len(keys) == len(policies)  # every combination spells uniquely
    assert '' in keys  # the all-defaults policy
    anat_pairs = {(p.sdc_anat_reference, p.force_sdc_anat_reference) for p in policies}
    # 'none' is never forced (nothing to force); 'auto' is a first-class value,
    # fallback and forced, because it resolves per session and so can produce a
    # grouping distinct from any single concrete method.
    assert ('none', True) not in anat_pairs
    assert ('auto', False) in anat_pairs
    assert ('auto', True) in anat_pairs


def test_auto_initial_policy_renders_and_is_reachable(tmp_path):
    """Regression: '--sdc-anat-reference auto' used to KeyError in the explorer
    (auto was excluded from the grid); it is now a first-class grid cell."""
    records, issues = _indexed('fieldmapless_t2w', tmp_path)  # has a T1w -> auto = synb0
    auto = GroupingPolicy(sdc_anat_reference='auto')

    grid = build_policy_grid(records, '01', base=auto, index_issues=issues)
    assert auto.policy_key() in grid.policy_index
    forced_auto = GroupingPolicy(sdc_anat_reference='auto', force_sdc_anat_reference=True)
    assert forced_auto.policy_key() in grid.policy_index

    # The static page (the --html / --cohort-html sibling path) renders, opens
    # on auto's grouping, and offers 'auto' in the policy dropdown.
    page = render_explorer_html(records, '01', index_issues=issues, initial_policy=auto)
    assert '<option value="sdc-anat-reference=auto"' in page
    index = _embedded_index(page)
    assert index['policies']['sdc-anat-reference=auto']['sig'] is not None


def test_cli_phrase_composes_ignore_as_one_flag():
    # qsiprep's --ignore takes a space-delimited list; the grouping values
    # compose one flag in qsiprep's declared choice order, never one
    # --ignore-<value> flag per value.
    assert GroupingPolicy().cli_phrase() == ''
    assert GroupingPolicy(ignore_fieldmaps=True).cli_phrase() == '--ignore fieldmaps'
    policy = GroupingPolicy(
        ignore_fieldmaps=True, ignore_sdc=True, ignore_shims=True, ignore_fov=True
    )
    assert policy.cli_phrase() == '--ignore fieldmaps sdc shims fov'
    ordered = GroupingPolicy(ignore_fieldmaps=True, ignore_pepolar_dwis=True, ignore_sdc=True)
    assert ordered.cli_phrase() == '--ignore fieldmaps pepolar-dwis sdc'
    assert (
        GroupingPolicy(separate_all_dwis=True, ignore_fov=True).cli_phrase()
        == '--separate-all-dwis --ignore fov'
    )
    # The anatomical-SDC flags stay separate and real (never folded into --ignore).
    assert (
        GroupingPolicy(sdc_anat_reference='invt1w').cli_phrase() == '--sdc-anat-reference invt1w'
    )
    forced = GroupingPolicy(sdc_anat_reference='t2w', force_sdc_anat_reference=True)
    assert forced.cli_phrase() == '--sdc-anat-reference t2w --force sdc-anat-reference'


def test_noop_toggle_collapses_by_content(tmp_path):
    records, issues = _indexed('hcp_style', tmp_path)
    assert not any(record.datatype == 'fmap' for record in records)
    base = build_for_policy(records, '01', GroupingPolicy(), issues)
    no_fmaps = build_for_policy(records, '01', GroupingPolicy(ignore_fieldmaps=True), issues)
    # No fmap/ files: ignoring them is a no-op and collapses onto the same content.
    assert grouping_signature(no_fmaps) == grouping_signature(base)
    separate = build_for_policy(records, '01', GroupingPolicy(separate_all_dwis=True), issues)
    assert grouping_signature(separate) != grouping_signature(base)


def test_drop_fieldmaps_matches_ignore_fieldmaps_indexing(tmp_path):
    layout, subject_data = build_layout('abcd_style', tmp_path)
    full_records, full_issues = index_subject(layout, subject_data)
    assert any(record.datatype == 'fmap' for record in full_records)
    direct_records, direct_issues = index_subject(layout, subject_data, ignore_fieldmaps=True)
    filtered_records, filtered_issues = drop_fieldmaps(full_records, full_issues)
    # FileRecord equality is identity; the groupings' matching signatures
    # below assert the structural equivalence.
    assert [record.path for record in filtered_records] == [
        record.path for record in direct_records
    ]
    assert filtered_issues == direct_issues
    via_filter = build_for_policy(
        full_records, '01', GroupingPolicy(ignore_fieldmaps=True), full_issues
    )
    via_index = build_grouping(
        direct_records,
        subject_id='01',
        ignore_fieldmaps=True,
        extra_issues=list(direct_issues),
    )
    assert grouping_signature(via_filter) == grouping_signature(via_index)


def test_policy_grid_dedupes_by_content(tmp_path):
    records, issues = _indexed('abcd_style', tmp_path)
    grid = build_policy_grid(records, '01', index_issues=issues)
    assert len(grid.policy_index) == len(reachable_policies())
    assert set(grid.policy_index.values()) == set(grid.groupings)
    # Most toggles are no-ops for any one dataset: the grid must collapse.
    assert len(grid.groupings) < len(grid.policy_index)
    assert grid.policy_cli[''] == ''
    assert grid.policy_cli['separate-all-dwis=1'] == '--separate-all-dwis'


def test_selection_refines_runs_never_the_grouping(tmp_path):
    """The i,j-case invariant: the method axis splits runs, not the grouping.

    A multi-blip-pair PEPOLAR unit is ONE correction unit in the grouping
    under every selection; TORTOISE merely compiles it into more runs than
    eddy does. The grouping signature must not see the difference.
    """
    records, issues = _indexed('multi_readout', tmp_path)
    grouping = build_for_policy(records, '01', GroupingPolicy(), issues)
    signature = grouping_signature(grouping)
    pooled = compile_plan(grouping, selection_for_config('eddy', 'topup'))
    split = compile_plan(grouping, selection_for_config('tortoise', 'drbuddi'))
    assert len(split.runs) > len(pooled.runs)
    assert grouping_signature(grouping) == signature
    assert {run.logical_unit for run in split.runs} == {run.logical_unit for run in pooled.runs}


def test_merge_strategy_splits_only_multi_unit_outputs(tmp_path):
    records, issues = _indexed('two_gre_fmaps', tmp_path)
    base = build_for_policy(records, '01', GroupingPolicy(), issues)
    split = build_for_policy(records, '01', GroupingPolicy(distortion_group_merge='none'), issues)
    assert len(base.concatenation_groups) < len(split.concatenation_groups)
    assert grouping_signature(base) != grouping_signature(split)


def _embedded_index(page):
    match = re.search(
        r'<script type="application/json" class="explorer-index">(.*?)</script>',
        page,
        re.DOTALL,
    )
    assert match, 'explorer page must embed its index'
    return json.loads(match.group(1))


def test_explorer_page_embeds_the_factored_index(tmp_path):
    records, issues = _indexed('abcd_style', tmp_path)
    page = render_explorer_html(records, '01', index_issues=issues)
    index = _embedded_index(page)
    # Every policy key resolves to a signature; every signature has exactly
    # one rendering and one full set of compiled plans.
    signatures = {entry['sig'] for entry in index['policies'].values()}
    assert signatures == set(index['groupings']) == set(index['plans'])
    assert '' in index['policies']
    selection_keys = {selection.combination_key() for selection in reachable_selections()}
    for by_selection in index['plans'].values():
        assert set(by_selection) == selection_keys
        for payload in by_selection.values():
            assert payload['prose']
            assert payload['selection']['label']
    for rendering in index['groupings'].values():
        assert 'tagline' in rendering['view']
    # The controls carry the canonical key parts the page script assembles.
    assert 'data-part="separate-all-dwis=1"' in page
    assert 'data-part="ignore-fieldmaps=1"' in page
    assert 'data-part="ignore-pepolar-dwis=1"' in page
    assert 'value="distortion-group-merge=average"' in page
    # The --ignore values render as one flag's checkboxes, and the
    # --sdc-anat-reference select offers every method plus its forced variant
    # (a composite option value carrying both canonical key parts).
    assert 'class="ignore-group"' in page
    assert 'value="sdc-anat-reference=invt1w"' in page
    assert 'value="sdc-anat-reference=t2w&force-sdc-anat-reference=1"' in page
    assert 'class="grouping-view"' in page
    assert 'class="grouping-notes"' in page


def test_explorer_initial_state_matches_cli_flags(tmp_path):
    records, issues = _indexed('abcd_style', tmp_path)
    page = render_explorer_html(
        records,
        '01',
        index_issues=issues,
        initial_policy=GroupingPolicy(separate_all_dwis=True),
        initial_selection=selection_for_config('tortoise', 'drbuddi'),
    )
    assert 'data-part="separate-all-dwis=1" checked' in page
    assert '<option value="tortoise" selected>' in page
    index = _embedded_index(page)
    assert 'separate-all-dwis=1' in index['policies']


def test_explorer_preserves_fixed_ignore_sdc_policy(tmp_path):
    """The non-editable ignore-sdc base flag remains part of every browser key."""
    records, issues = _indexed('abcd_style', tmp_path)
    page = render_explorer_html(
        records,
        '01',
        index_issues=issues,
        initial_policy=GroupingPolicy(ignore_sdc=True),
    )
    index = _embedded_index(page)
    assert index['fixedPolicyParts'] == ['ignore-sdc=1']
    assert 'ignore-sdc=1' in index['policies']
    assert 'const parts = [...(index.fixedPolicyParts || [])]' in page


def test_explorer_canonicalizes_force_without_method(tmp_path):
    """force_sdc_anat_reference without a method resolves to the plain policy."""
    policy = GroupingPolicy(force_sdc_anat_reference=True)
    records, issues = _indexed('fieldmapless_t2w', tmp_path)
    page = render_explorer_html(
        records,
        '01',
        index_issues=issues,
        initial_policy=policy,
    )
    index = _embedded_index(page)
    assert '' in index['policies']
    assert policy.policy_key() not in index['policies']


def test_explorer_places_analysis_controls_before_output_cards(tmp_path):
    records, issues = _indexed('abcd_style', tmp_path)
    page = render_explorer_html(records, '01', index_issues=issues)
    assert page.index('class="plan-explorer"') < page.index('class="grouping-view"')
    assert 'role="status" aria-live="polite"' in page
