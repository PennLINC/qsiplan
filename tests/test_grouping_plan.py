"""Parity harness for the execution-plan compiler.

The adapter views - ``to_preproc_units`` (run keys, file sets, estimations)
and ``concatenation_scheme`` (assemblies) - are derived from the compiled
plan, and this harness pins that derivation: every scenario, plus the flag
variants the golden reports cover, under each preview selection. Stage
shapes - the ordered estimate/HMC/refine sequences - are pinned separately
for each method family.
"""

import json

import pytest
from grouping_scenarios import SCENARIOS, load_scenario

from qsiplan import concatenation_scheme, to_preproc_units
from qsiplan.integrity import check_plan
from qsiplan.methods import selection_for_config
from qsiplan.models import CorrectionMethod
from qsiplan.plan import ExecutionPlan, StageRole, compile_plan

#: (scenario, build kwargs) - every scenario plain, plus the golden flag variants.
CASES = [(scenario, {}) for scenario in SCENARIOS] + [
    ('fieldmapless_t1w_only', {'sdc_anat_reference': 'invt1w'}),
    ('fieldmapless_t1w_only', {'sdc_anat_reference': 'synb0'}),
    ('t2w_hcp', {'sdc_anat_reference': 'synb0', 'force_sdc_anat_reference': True}),
    ('curated_t2wreg', {'sdc_anat_reference': 't2w', 'force_sdc_anat_reference': True}),
]

#: The eddy/TORTOISE preview selections every case is compiled under.
SELECTIONS = [
    selection_for_config('eddy', 'topup'),
    selection_for_config('tortoise', 'drbuddi'),
    selection_for_config('eddy', 'topup+drbuddi'),
]


def _case_id(case):
    scenario, kwargs = case
    suffix = '+'.join(sorted(kwargs)) if kwargs else 'plain'
    return f'{scenario}-{suffix}'


@pytest.mark.parametrize('selection', SELECTIONS, ids=lambda s: s.combination_key())
@pytest.mark.parametrize('case', CASES, ids=_case_id)
def test_adapter_views_derive_from_the_plan(tmp_path, case, selection):
    scenario, kwargs = case
    grouping = load_scenario(scenario, tmp_path, strict=False, **kwargs)
    plan = compile_plan(grouping, selection)

    # Runs <-> PreprocUnits: same keys in the same order, same files, same
    # (possibly pair-restricted) estimations.
    units = to_preproc_units(grouping, selection)
    assert [run.key for run in plan.runs] == [unit.output_name for unit in units]
    for run, unit in zip(plan.runs, units, strict=True):
        assert run.dwi_files == unit.dwi_files
        assert run.estimation == unit.estimation

    # Assemblies <-> concatenation_scheme, inverted.
    scheme = concatenation_scheme(grouping, selection)
    from_plan = {
        run_key: assembly.output_name
        for assembly in plan.outputs
        for run_key in assembly.input_runs
    }
    assert from_plan == scheme

    # The compiler kept its own invariants.
    assert check_plan(grouping, plan) == []

    # Serialization is complete and stable.
    payload = json.dumps(plan.to_dict(), sort_keys=True)
    assert json.dumps(plan.to_dict(), sort_keys=True) == payload


@pytest.mark.parametrize('case', CASES, ids=_case_id)
def test_stage_order_per_method_family(tmp_path, case):
    """eddy estimates before HMC (TOPUP integrated); the others correct first."""
    scenario, kwargs = case
    grouping = load_scenario(scenario, tmp_path, strict=False, **kwargs)
    selections = [*SELECTIONS, selection_for_config('shoreline', 'drbuddi')]
    for selection in selections:
        plan = compile_plan(grouping, selection)
        for run in plan.runs:
            roles = [stage.role for stage in run.stages]
            assert roles, f'{run.key} has no stages'
            hmc_roles = {StageRole.HMC, StageRole.HMC_WITH_FIELD}
            assert sum(role in hmc_roles for role in roles) == 1
            if run.estimation is not None and run.estimation.is_pepolar:
                topup = run.stage_with('topup')
                if topup is not None:
                    # The integrated path: the field is estimated first and
                    # eddy consumes it during motion correction.
                    assert topup.role is StageRole.ESTIMATE
                    eddy = run.stage_with('eddy')
                    assert eddy.role is StageRole.HMC_WITH_FIELD
                    assert eddy.consumes == topup.index
                elif run.stage_with('drbuddi') is not None:
                    # DRBUDDI always estimates after motion correction.
                    assert roles[0] in hmc_roles


def _plan_for(tmp_path, scenario, hmc, sdc, **kwargs):
    grouping = load_scenario(scenario, tmp_path, strict=False, **kwargs)
    return compile_plan(grouping, selection_for_config(hmc, sdc))


def _role_tool(run):
    return [(stage.role.value, stage.tool) for stage in run.stages]


def test_eddy_topup_sequence(tmp_path):
    plan = _plan_for(tmp_path, 'hcp_style', 'eddy', 'topup')
    (run,) = plan.runs
    assert _role_tool(run) == [('estimate', 'topup'), ('hmc-with-field', 'eddy')]
    assert run.stages[1].consumes == 0


def test_eddy_topup_drbuddi_sequence(tmp_path):
    plan = _plan_for(tmp_path, 'hcp_style', 'eddy', 'topup+drbuddi')
    (run,) = plan.runs
    assert _role_tool(run) == [
        ('estimate', 'topup'),
        ('hmc-with-field', 'eddy'),
        ('refine', 'drbuddi'),
    ]


def test_eddy_drbuddi_only_corrects_after_hmc(tmp_path):
    plan = _plan_for(tmp_path, 'hcp_style', 'eddy', 'drbuddi')
    (run,) = plan.runs
    assert _role_tool(run) == [('hmc', 'eddy'), ('estimate+apply', 'drbuddi')]


def test_eddy_mixed_epi_fmap_skips_drbuddi_refinement(tmp_path):
    """A lone reverse b=0 completes the blip pair, but eddy's output is already
    unwarped by the TOPUP field, so scheduling the DRBUDDI refinement would
    correct the distortion a second time."""
    plan = _plan_for(tmp_path / 'topup_drbuddi', 'abcd_style', 'eddy', 'topup+drbuddi')
    (run,) = plan.runs
    assert _role_tool(run) == [('estimate', 'topup'), ('hmc-with-field', 'eddy')]
    assert 'drbuddi-refinement-not-useful' in {issue.code for issue in plan.issues}

    # DRBUDDI-only has no TOPUP stage to collide with: the fieldmap-completed
    # pair is corrected by DRBUDDI in b=0-only (epi) mode.
    plan = _plan_for(tmp_path / 'only', 'abcd_style', 'eddy', 'drbuddi')
    (run,) = plan.runs
    assert _role_tool(run) == [('hmc', 'eddy'), ('estimate+apply', 'drbuddi')]


@pytest.mark.parametrize('scenario', ['multi_readout', 'cross_axis_b0field', 'partial_pair'])
def test_eddy_drbuddi_only_rejects_pooled_blip_groups(tmp_path, scenario):
    """A pooled eddy run must not silently drop DRBUDDI-only SDC."""
    plan = _plan_for(tmp_path, scenario, 'eddy', 'drbuddi')
    issues = [issue for issue in plan.issues if issue.code == 'drbuddi-only-infeasible']
    assert issues
    assert all(issue.severity == 'error' for issue in issues)
    assert not any(run.stage_with('drbuddi') for run in plan.runs)


def test_eddy_drbuddi_only_multigroup_report_does_not_claim_topup(tmp_path):
    from qsiplan.report import describe_processing

    grouping = load_scenario('multi_readout', tmp_path, strict=False)
    selection = selection_for_config('eddy', 'drbuddi')
    report = describe_processing(grouping, selection)
    assert 'DRBUDDI-only selection is infeasible' in report
    assert 'TOPUP+eddy corrects them together' not in report


def test_tortoise_drbuddi_sequence(tmp_path):
    plan = _plan_for(tmp_path, 'hcp_style', 'tortoise', 'drbuddi')
    (run,) = plan.runs
    assert _role_tool(run) == [('hmc', 'tortoise'), ('estimate+apply', 'drbuddi')]


def test_shoreline_is_named_not_tortoise(tmp_path):
    plan = _plan_for(tmp_path, 'hcp_style', 'shoreline', 'drbuddi')
    (run,) = plan.runs
    assert _role_tool(run) == [('hmc', 'shoreline'), ('estimate+apply', 'drbuddi')]


def test_gre_fieldmap_estimates_after_hmc(tmp_path):
    plan = _plan_for(tmp_path, 'gre_phasediff', 'eddy', 'topup')
    stages = {run.key: _role_tool(run) for run in plan.runs}
    assert all(
        seq == [('hmc', 'eddy'), ('estimate+apply', 'fieldmap')] for seq in stages.values()
    ), stages


def test_tortoise_t2wreg_fallback_stage(tmp_path):
    plan = _plan_for(tmp_path, 'fieldmapless_t2w', 'tortoise', 'drbuddi', sdc_anat_reference='t2w')
    (run,) = plan.runs
    assert _role_tool(run) == [('hmc', 'tortoise'), ('estimate+apply', 't2wreg')]
    assert run.stages[1].structural_target == 't2w'


def test_eddy_cannot_run_t2wreg(tmp_path):
    plan = _plan_for(tmp_path, 'fieldmapless_t2w', 'eddy', 'topup', sdc_anat_reference='t2w')
    (run,) = plan.runs
    assert _role_tool(run) == [('hmc', 'eddy')]
    assert any(issue.code == 'anat-sdc-unsupported' for issue in plan.issues)


def test_synb0_prefers_synthetic_target_on_tortoise(tmp_path):
    plan = _plan_for(
        tmp_path,
        'partial_curation_stranded',
        'tortoise',
        'drbuddi',
        sdc_anat_reference='synb0',
    )
    targets = {
        stage.structural_target
        for run in plan.runs
        for stage in run.stages
        if stage.tool == 't2wreg'
    }
    assert targets in (set(), {'synb0'})


def test_synb0_feeds_topup_on_eddy(tmp_path):
    plan = _plan_for(
        tmp_path, 'fieldmapless_t1w_only', 'eddy', 'topup', sdc_anat_reference='synb0'
    )
    (run,) = plan.runs
    assert _role_tool(run) == [('estimate', 'topup'), ('hmc-with-field', 'eddy')]
    topup = run.stages[0]
    assert topup.method is CorrectionMethod.SYNB0
    assert topup.structural_target == 'synb0'
    assert topup.estimation == run.estimation.b0field_id
    assert run.stages[1].consumes == 0


def test_synb0_topup_drbuddi_never_refines(tmp_path):
    # There is no reverse phase-encoded dMRI data for a DRBUDDI second stage.
    plan = _plan_for(
        tmp_path, 'fieldmapless_t1w_only', 'eddy', 'topup+drbuddi', sdc_anat_reference='synb0'
    )
    (run,) = plan.runs
    assert _role_tool(run) == [('estimate', 'topup'), ('hmc-with-field', 'eddy')]


@pytest.mark.parametrize(('hmc', 'sdc'), [('eddy', 'drbuddi'), ('shoreline', 'drbuddi')])
def test_synb0_without_a_consumer_stays_uncorrected(tmp_path, hmc, sdc):
    plan = _plan_for(tmp_path, 'fieldmapless_t1w_only', hmc, sdc, sdc_anat_reference='synb0')
    (run,) = plan.runs
    assert _role_tool(run) == [('hmc', hmc)]


def test_syn_stage_targets_t1w(tmp_path):
    plan = _plan_for(
        tmp_path, 'fieldmapless_t1w_only', 'eddy', 'topup', sdc_anat_reference='invt1w'
    )
    (run,) = plan.runs
    assert _role_tool(run) == [('hmc', 'eddy'), ('estimate+apply', 'syn')]
    assert run.stages[1].structural_target == 't1w'
    assert run.stages[1].method is CorrectionMethod.NIPREPS_SYN


def test_decomposed_pairs_get_their_own_runs(tmp_path):
    """A multi-readout PEPOLAR unit splits per blip pair on decomposing methods."""
    grouping = load_scenario('multi_readout', tmp_path, strict=False)
    pooled = compile_plan(grouping, selection_for_config('eddy', 'topup'))
    split = compile_plan(grouping, selection_for_config('tortoise', 'drbuddi'))
    assert len(split.runs) > len(pooled.runs)
    assert {run.logical_unit for run in split.runs} == {run.logical_unit for run in pooled.runs}
    # Every complete pair corrects with DRBUDDI; each run's blip files stay
    # inside its own pair.
    for run in split.runs:
        drbuddi = run.stage_with('drbuddi')
        if drbuddi is not None:
            assert run.estimation is not None
            assert set(drbuddi.fieldmap_sources) == set(run.estimation.sources)


def test_plan_serialization_shape(tmp_path):
    plan = _plan_for(tmp_path, 'hcp_style', 'eddy', 'topup+drbuddi')
    payload = plan.to_dict()
    assert payload['schema_version'] == 2  # 2: runs carry dwi_phase_files
    assert payload['selection']['hmc'] == 'eddy'
    assert payload['selection']['pepolar_tools'] == ['topup', 'drbuddi']
    assert payload['selection']['label'] == 'eddy + TOPUP→DRBUDDI'
    (run,) = payload['runs']
    assert [stage['role'] for stage in run['stages']] == [
        'estimate',
        'hmc-with-field',
        'refine',
    ]
    assert isinstance(plan, ExecutionPlan)


def test_run_lookup_helpers(tmp_path):
    plan = _plan_for(tmp_path, 'hcp_style', 'eddy', 'topup')
    (run,) = plan.runs
    assert plan.run(run.key) is run
    assert plan.runs_for(run.output_group) == [run]
    with pytest.raises(KeyError):
        plan.run('nope')


def test_case_and_selection_coverage_stays_in_sync():
    """The parity sweep covers every scenario and flag variant under three selections."""
    assert len(CASES) == len(SCENARIOS) + 4
    assert len(SELECTIONS) == 3
    assert len({s.combination_key() for s in SELECTIONS}) == 3  # distinct selections


def test_plan_step_records_follow_stage_order(tmp_path):
    from qsiplan.report import plan_step_records

    grouping = load_scenario('hcp_style', tmp_path, strict=False)
    plan = compile_plan(grouping, selection_for_config('eddy', 'topup+drbuddi'))
    (records,) = plan_step_records(grouping, plan).values()
    kinds = [record.kind for record in records]
    assert kinds == ['denoise', 'sdc-estimate', 'hmc', 'refine', 'assemble']
    tools = [record.tool for record in records if record.tool]
    assert tools == ['topup', 'eddy', 'drbuddi']
    assert 'TOPUP estimates' in records[1].text
    assert 'using the TOPUP field' in records[2].text


def test_plan_step_records_scope_issues_to_their_output(tmp_path):
    from qsiplan.report import plan_step_records

    grouping = load_scenario('nonshelled_pair', tmp_path, strict=False)
    plan = compile_plan(grouping, selection_for_config('eddy', 'topup'))
    (records,) = plan_step_records(grouping, plan).values()
    issues = [record for record in records if record.kind == 'issue']
    assert issues
    assert issues[0].severity == 'error'
    assert 'eddy-requires-shelled' in issues[0].text


def test_drbuddi_registration_channels(tmp_path):
    """The plan names DRBUDDI's registration mode from the stage's facts:
    reverse-PE DWI series on both sides add FA, a T2w adds the multimodal
    channel, and an epi-fieldmap-only pair is b=0-only."""
    from qsiplan.report import drbuddi_channels

    # Reverse-PE DWI pair, subject has a T2w: b=0+FA+T2w.
    plan = _plan_for(tmp_path, 'partial_pair', 'tortoise', 'drbuddi')
    pair_stage = plan.run('sub-01_acq-fast').stage_with('drbuddi')
    assert drbuddi_channels(pair_stage) == 'b=0+FA+T2w'

    # Reverse-PE DWI pair, no T2w: b=0+FA.
    plan = _plan_for(tmp_path, 'hcp_style', 'tortoise', 'drbuddi')
    (run,) = plan.runs
    assert drbuddi_channels(run.stage_with('drbuddi')) == 'b=0+FA'

    # Dedicated epi fieldmap only (single-polarity DWI), no T2w: b=0 only.
    from qsiplan.report import plan_step_records
    from qsiplan.viz.pipeline import plan_payload

    grouping = load_scenario('abcd_style', tmp_path, strict=False)
    plan = compile_plan(grouping, selection_for_config('tortoise', 'drbuddi'))
    (run,) = plan.runs
    assert drbuddi_channels(run.stage_with('drbuddi')) == 'b=0'
    # The step sentence and the diagram payload carry the mode.
    (records,) = plan_step_records(grouping, plan).values()
    (drbuddi_record,) = (r for r in records if r.tool == 'drbuddi')
    assert '(b=0 registration)' in drbuddi_record.text
    (payload_run,) = plan_payload(grouping, plan)['runs']
    (drbuddi_stage,) = (s for s in payload_run['stages'] if s['tool'] == 'drbuddi')
    assert drbuddi_stage['channels'] == 'b=0'
