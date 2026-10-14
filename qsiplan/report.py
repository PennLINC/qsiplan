"""Human-readable renderings of a DWIGrouping.

Two views are provided:

- :func:`report_text` describes the grouping itself: which files were grouped
  with which, and whether each decision was curated, translated from
  IntendedFor, or inferred.
- :func:`describe_processing` previews what a method selection would actually
  do with the grouping, including any errors it would raise. The grouping
  model itself knows nothing about methods; all
  tool knowledge lives here and in the plan compiler
  (:func:`~.plan.compile_plan`).

The preview prose exists at two deliberate granularities. The per-unit
narratives (``_describe_*``) are the product: hand-written, data-rich text -
blip-pair names and readout times, acquisition-parameter row counts, borrowed
b=0 notes, *why* a refinement is skipped - frozen by the golden reports. The
per-stage sentences (:func:`_stage_text` via :func:`plan_step_records`) are
node labels for diagrams and JSON payloads, one short line per compiled
:class:`~.plan.PlanStage`. They are not a duplicate of each other and are not
meant to converge; what they *share* is factored (e.g. the per-pair DRBUDDI
narration), and both draw their facts from the same compiled plan.
"""

from __future__ import annotations

import dataclasses
import os.path as op
import re
from collections import Counter, defaultdict
from typing import NamedTuple

from .methods import HmcMethod, MethodSelection, SdcTool, selection_for_config
from .models import CorrectionMethod, DWIGrouping
from .plan import _fieldmapless_fallback
from .validation import (
    blip_pair_polarities,
    blip_sort_key,
    describe_blip_group,
    dwi_blip_pairs,
)

_METHOD_LABELS = {
    CorrectionMethod.PEPOLAR: 'PEPOLAR (reverse phase-encoding)',
    CorrectionMethod.DIRECT: 'precomputed fieldmap',
    CorrectionMethod.PHASEDIFF: 'GRE phase difference',
    CorrectionMethod.PHASES: 'GRE two-phase',
    CorrectionMethod.SYNB0: 'SyNb0 synthetic b=0',
    CorrectionMethod.UNPILE: 'unpile fieldmap-less (experimental)',
    CorrectionMethod.NIPREPS_SYN: 'fieldmap-less SyN',
}


def _basename(path: str) -> str:
    return op.basename(path)


def round_bval(bval) -> int:
    """A b-value rounded to the nearest 100 for display.

    Small acquisition deviations (e.g. 1585, 4985) should not read as distinct
    shells. The JS viewer (``qsiprep/data/qspace_viewer.js`` ``roundB``) applies
    the same rule, so every b-value shown to a reader is rounded consistently.
    """
    return int((bval + 50) // 100) * 100


def shell_label(record) -> str:
    """``b=<v>/<v>...`` of a shelled record's centres, rounded for display."""
    return 'b=' + '/'.join(dict.fromkeys(str(round_bval(c)) for c in record.shells))


def _shell_tag(record) -> str:
    """Bracketed sampling-scheme annotation for a DWI file line."""
    if record.shelled is True:
        return f' [shelled: {shell_label(record)}]'
    if record.shelled is False:
        return ' [non-shelled q-space sampling]'
    return ''


def _companion_lines(grouping: DWIGrouping, path: str, indent: str) -> list[str]:
    """The phase companion of a complex-valued image, if it has one."""
    record = grouping.files.get(path)
    if record is None or not record.phase_path:
        return []
    return [f'{indent}with phase image {_basename(record.phase_path)}']


def report_text(grouping: DWIGrouping) -> str:
    """The grouping decisions, with provenance, as printable text."""
    lines = []
    title = f'DWI grouping for sub-{grouping.subject_id}'
    lines.append(title)
    lines.append('=' * len(title))
    lines.append('')

    for concat_key, concat in sorted(grouping.concatenation_groups.items()):
        lines.append(
            f'Output "{concat.output_name}" '
            f'(MultipartID {concat.multipart_id} {concat.provenance.tag()}): '
            f'{len(concat.dwi_files)} series'
        )
        for dgroup in grouping.distortion_groups_in(concat_key):
            lines.append(f'  Distortion group {dgroup.key} ({dgroup.signature.describe()}):')
            for path in dgroup.dwi_files:
                lines.append(f'    - {_basename(path)}{_shell_tag(grouping.files[path])}')
                lines.extend(_companion_lines(grouping, path, indent='      '))
            if dgroup.b0field_source is None:
                lines.append('    corrected by: nothing (no fieldmap found)')
            else:
                provenance = grouping.application_provenance[dgroup.dwi_files[0]]
                lines.append(f'    corrected by: {dgroup.b0field_source} {provenance.tag()}')
                losing = [
                    candidate
                    for candidate in grouping.application_candidates.get(dgroup.dwi_files[0], ())
                    if candidate != dgroup.b0field_source
                ]
                if losing:
                    lines.append(f'    (also eligible: {", ".join(losing)})')
        borrowed = grouping.borrowed_sources(concat_key)
        for b0field_id, paths in sorted(borrowed.items()):
            lines.append(
                f'  Borrows for fieldmap estimation ({b0field_id}), not included in this output:'
            )
            for path in paths:
                lines.append(f'    - {_basename(path)}')
        lines.append('')

    if grouping.estimations:
        applied_ids = {b0field_id for b0field_id in grouping.application.values() if b0field_id}
        lines.append('Fieldmap estimations:')
        for b0field_id, estimation in sorted(grouping.estimations.items()):
            if b0field_id in applied_ids:
                unused = ''
            elif grouping.initializes_only(b0field_id):
                unused = ' (initializes DRBUDDI/unpile)'
            else:
                unused = ' (not used)'
            lines.append(
                f'  {b0field_id} {estimation.provenance.tag()}: '
                f'{_METHOD_LABELS[estimation.method]}{unused}'
            )
            for path in estimation.sources:
                lines.append(f'    - {_basename(path)}')
                lines.extend(_companion_lines(grouping, path, indent='      '))
            if estimation.pe_axes:
                axes = ', '.join(
                    f'{axis} (bidirectional)'
                    if axis in estimation.bidirectional_axes
                    else f'{axis} (one direction only)'
                    for axis in sorted(estimation.pe_axes)
                )
                lines.append(f'    phase encoding axes: {axes}')
        lines.append('')
    else:
        lines.append('Fieldmap estimations: none found.')
        lines.append('')

    if grouping.issues:
        lines.append('Notes:')
        for issue in grouping.issues:
            lines.append(f'  {issue.render()}')
        lines.append('')

    return '\n'.join(lines)


def _unit_corrected(grouping: DWIGrouping, unit) -> dict[str, list[str]]:
    """estimation id -> keys of the unit's distortion groups it corrects."""
    corrected = defaultdict(list)
    for key in unit.distortion_groups:
        dgroup = grouping.distortion_groups[key]
        if dgroup.b0field_source is not None:
            corrected[dgroup.b0field_source].append(key)
    return corrected


def _split_polarities(dgroups, pair_key: tuple):
    """Distortion groups in one blip pair (pe_axis, readout, shim), split into
    (blip-up, blip-down)."""
    up, down = [], []
    for dgroup in dgroups:
        sig = dgroup.signature
        if (sig.pe_axis, sig.readout_time, sig.shim) != pair_key:
            continue
        (up if sig.pe_polarity == 1 else down).append(dgroup)
    return up, down


def _describe_unit(lines, grouping, selection, multipart_id, unit, step) -> int:
    """One correction unit's HMC+SDC steps; returns the next step number."""
    corrected = _unit_corrected(grouping, unit)
    dgroups = [grouping.distortion_groups[key] for key in unit.distortion_groups]
    if selection.hmc is HmcMethod.TORTOISE:
        return _describe_tortoise(
            lines, grouping, selection, multipart_id, corrected, dgroups, step
        )
    if selection.hmc is HmcMethod.SHORELINE:
        return _describe_shoreline(
            lines, grouping, selection, multipart_id, corrected, dgroups, step
        )
    if SdcTool.DRBUDDI in selection.pepolar_tools:
        return _describe_eddy_drbuddi(
            lines, grouping, selection, multipart_id, corrected, dgroups, step
        )
    return _describe_eddy(lines, grouping, multipart_id, corrected, dgroups, step)


def _pipelines(grouping, selection, unit) -> list:
    """``unit``, or one view of it per phase encoding when its correction is one
    warp built for one encoding (the per-encoding runs of the compiled plan)."""
    from .adapters import PreprocUnit, _split_by_encoding
    from .plan import _mixes_encodings_under_one_warp

    estimation = grouping.estimations[unit.b0field_source] if unit.b0field_source else None
    whole = PreprocUnit(
        grouping=grouping, output_name=unit.key, dwi_files=unit.dwi_files, estimation=estimation
    )
    if not _mixes_encodings_under_one_warp(grouping, whole, selection):
        return [unit]
    return [
        dataclasses.replace(
            unit,
            key=part.output_name,
            dwi_files=part.dwi_files,
            distortion_groups=tuple(
                key
                for key in unit.distortion_groups
                if set(grouping.distortion_groups[key].dwi_files) <= set(part.dwi_files)
            ),
        )
        for part in _split_by_encoding(grouping, unit, estimation)
    ]


def _output_step_lines(grouping, selection, multipart_id, backend_issues) -> list[str]:
    """The numbered step lines for one output, as printed by describe_processing."""
    concat = grouping.concatenation_groups[multipart_id]
    units = grouping.correction_units_in(multipart_id)
    pipelines = [part for unit in units for part in _pipelines(grouping, selection, unit)]
    split = len(pipelines) > len(units)
    n_series = len(concat.dwi_files)
    lines = []

    if len(pipelines) == 1:
        if n_series > 1:
            lines.append(
                '  1. Each series is denoised on its own, then all '
                f'{n_series} series are concatenated.'
            )
        else:
            lines.append('  1. The series is denoised.')
        step = _describe_unit(lines, grouping, selection, multipart_id, pipelines[0], step=2)
        lines.append(f'  {step}. The corrected series is written as one output file.')
    elif split:
        lines.append(
            f'  1. Each series is denoised on its own. The {n_series} series run in '
            f'{len(pipelines)} pipelines: a correction built as one warp for one phase '
            'encoding runs once per encoding.'
        )
        step = 2
        for pipeline in pipelines:
            names = ', '.join(_basename(path) for path in pipeline.dwi_files)
            kind = 'Correction unit' if pipeline in units else 'Phase encoding'
            lines.append(f"  {step}. {kind} '{pipeline.key}' ({names}):")
            step = _describe_unit(lines, grouping, selection, multipart_id, pipeline, step + 1)
        lines.append(
            f'  {step}. The corrected results of the {len(pipelines)} pipelines are '
            'resampled onto one grid and concatenated into one output file.'
        )
    else:
        lines.append(
            f'  1. Each series is denoised on its own. The {n_series} series span '
            f'{len(units)} correction units; each unit is concatenated and '
            'corrected in its own pipeline.'
        )
        step = 2
        for unit in units:
            names = ', '.join(_basename(path) for path in unit.dwi_files)
            lines.append(f"  {step}. Correction unit '{unit.key}' ({names}):")
            step = _describe_unit(lines, grouping, selection, multipart_id, unit, step + 1)
        lines.append(
            f'  {step}. The corrected results of the {len(units)} units are '
            'resampled onto one grid and concatenated into one output file.'
        )

    for issue in backend_issues:
        if issue.scope in (None, multipart_id):
            lines.append(f'  !! {issue.render()}')
    return lines


def describe_processing(grouping: DWIGrouping, selection: MethodSelection) -> str:
    """Preview what a method selection would do with this grouping."""
    from .plan import compile_plan

    backend_issues = compile_plan(grouping, selection).issues

    lines = []
    title = f'Processing preview: {selection.label()} ({selection.cli_phrase()})'
    lines.append(title)
    lines.append('-' * len(title))
    lines.append('')

    for multipart_id, concat in sorted(grouping.concatenation_groups.items()):
        n_series = len(concat.dwi_files)
        plural = 'series' if n_series != 1 else 'single series'
        lines.append(f'Output "{concat.output_name}" ({n_series} {plural}):')
        lines.extend(_output_step_lines(grouping, selection, multipart_id, backend_issues))
        lines.append('')

    return '\n'.join(lines)


def processing_steps(grouping: DWIGrouping, selection) -> dict[str, list[str]]:
    """The describe_processing steps, structured for non-text renderings.

    Returns ``{output_name: [step, ...]}`` where each step is the text of one
    numbered line with its continuation notes folded in. Feasibility issues
    keep their leading ``'!! '`` marker so renderers can style them.
    """
    from .plan import compile_plan

    backend_issues = compile_plan(grouping, selection).issues
    result = {}
    for multipart_id, concat in sorted(grouping.concatenation_groups.items()):
        steps = []
        for line in _output_step_lines(grouping, selection, multipart_id, backend_issues):
            text = line.strip()
            numbered = re.match(r'\d+\.\s+(.*)', text)
            if numbered:
                steps.append(numbered.group(1))
            elif text.startswith('!!') or not steps:
                steps.append(text)
            else:
                steps[-1] += ' ' + text
        result[concat.output_name] = steps
    return result


@dataclasses.dataclass(frozen=True)
class StepRecord:
    """One structured step of a planned pipeline, for non-prose renderings.

    The prose previews (:func:`describe_processing`) narrate correction units
    with hand-written detail; records narrate the compiled plan's stages one
    at a time, in stage order, so diagrams and JSON payloads can render each
    step as a node without parsing prose.
    """

    kind: str  # 'denoise'|'hmc'|'sdc-estimate'|'sdc-apply'|'refine'|'assemble'|'issue'
    text: str
    run: str | None = None
    stage: int | None = None
    tool: str | None = None
    method: str | None = None
    files: tuple[str, ...] = ()
    severity: str | None = None


def _stage_kind(role) -> str:
    from .plan import StageRole

    return {
        StageRole.ESTIMATE: 'sdc-estimate',
        StageRole.HMC: 'hmc',
        StageRole.HMC_WITH_FIELD: 'hmc',
        StageRole.ESTIMATE_AND_APPLY: 'sdc-apply',
        StageRole.REFINE: 'refine',
    }[role]


def drbuddi_channels(stage) -> str:
    """The registration channels a DRBUDDI stage will use, e.g. ``'b=0+FA+T2w'``.

    DRBUDDI always registers the blip-up/blip-down b=0 images; a reverse-PE
    DWI *series* on both sides adds the FA channel (the ``rpe_series`` path),
    and a structural target adds the multimodal T2w channel.
    """
    channels = ['b=0']
    if stage.plus_files and stage.minus_files:
        channels.append('FA')
    if stage.structural_target == 't2w':
        channels.append('T2w')
    return '+'.join(channels)


def _stage_text(grouping: DWIGrouping, selection: MethodSelection, stage) -> str:
    """One concise sentence for a plan stage, keyed on (role, tool)."""
    from .plan import StageRole

    if stage.tool == 'topup':
        if stage.role is StageRole.ESTIMATE_AND_APPLY:
            return (
                'TOPUP estimates distortion from the motion-corrected b=0 and a SyNb0 '
                'synthetic b=0, and the correction is applied.'
            )
        return 'TOPUP estimates the susceptibility field from the b=0 images.'
    if stage.tool == 'eddy':
        if stage.role is StageRole.HMC_WITH_FIELD:
            return (
                'eddy corrects head motion, eddy currents, and susceptibility '
                'distortion using the TOPUP field.'
            )
        return 'eddy corrects head motion and eddy currents.'
    if stage.tool == 'shoreline':
        if selection.shoreline_model == 'none':
            return (
                'SHORELine registers the b=0 images; each non-b=0 volume gets its '
                'nearest b=0 transform (no signal model).'
            )
        model = '3dSHORE' if selection.shoreline_model == '3dshore' else 'tensor'
        return f'SHORELine corrects head motion using a {model} signal model.'
    if stage.tool == 'tortoise':
        return 'TORTOISE DIFFPREP corrects head motion and eddy currents.'
    if stage.tool == 'drbuddi':
        channels = drbuddi_channels(stage)
        if stage.role is StageRole.REFINE:
            return (
                'DRBUDDI refines the correction from the corrected '
                f'blip-up/blip-down series ({channels} registration).'
            )
        return (
            'DRBUDDI estimates distortion from the blip-up/blip-down pair '
            f'({channels} registration) and applies the correction.'
        )
    if stage.tool == 'fieldmap':
        label = _METHOD_LABELS.get(stage.method, 'fieldmap')
        return f'A fieldmap is computed from the {label} acquisition and applied.'
    if stage.tool == 'unpile':
        target = 'a SyNb0 synthetic b=0' if stage.structural_target == 'synb0' else 'the T2w image'
        return (
            f'unpile fits the susceptibility field by matching the b=0 to {target} with a '
            'model of the EPI readout, and the correction is applied.'
        )
    if stage.tool == 'syn':
        if stage.structural_target == 'synb0':
            return 'A fieldmap-less SyN warp to a SyNb0 synthetic b=0 is estimated and applied.'
        return 'A fieldmap-less SyN warp is estimated from the T1w and applied.'
    return f'{stage.tool} ({stage.role.value})'  # pragma: no cover - future tools


def plan_step_records(grouping: DWIGrouping, plan) -> dict[str, list[StepRecord]]:
    """Structured step records per output, from a compiled execution plan.

    Returns ``{output_name: [StepRecord, ...]}`` covering denoising, every
    run's stages in order, the assembly step, and the plan issues scoped to
    that output.
    """
    records: dict[str, list[StepRecord]] = {}
    for assembly in plan.outputs:
        runs = [plan.run(key) for key in assembly.input_runs]
        n_series = sum(len(run.dwi_files) for run in runs)
        steps: list[StepRecord] = []
        steps.append(
            StepRecord(
                kind='denoise',
                text=(
                    'The series is denoised.'
                    if n_series == 1
                    else f'Each of the {n_series} series is denoised on its own.'
                ),
                files=tuple(path for run in runs for path in run.dwi_files),
            )
        )
        for run in runs:
            for stage in run.stages:
                steps.append(
                    StepRecord(
                        kind=_stage_kind(stage.role),
                        text=_stage_text(grouping, plan.selection, stage),
                        run=run.key,
                        stage=stage.index,
                        tool=stage.tool,
                        method=stage.method.value if stage.method else None,
                        files=run.dwi_files,
                    )
                )
        if len(runs) > 1:
            verb = 'averaged' if assembly.strategy == 'average' else 'concatenated'
            text = (
                f'The corrected results of the {len(runs)} runs are resampled '
                f'onto one grid and {verb} into one output file.'
            )
        else:
            text = 'The corrected series is written as one output file.'
        steps.append(StepRecord(kind='assemble', text=text))
        for issue in plan.issues:
            if issue.scope in (None, assembly.output_group) or issue.run in assembly.input_runs:
                steps.append(
                    StepRecord(
                        kind='issue',
                        text=issue.render(),
                        run=issue.run,
                        severity=issue.severity,
                        files=issue.files,
                    )
                )
        records[assembly.output_name] = steps
    return records


def _borrow_note(grouping: DWIGrouping, multipart_id: str, b0field_id: str) -> str | None:
    borrowed = grouping.borrowed_sources(multipart_id).get(b0field_id)
    if not borrowed:
        return None
    names = ', '.join(_basename(path) for path in borrowed)
    return (
        f'b=0 images are borrowed from {names} for the estimation; '
        'those series are NOT included in this output.'
    )


class MethodGroups(NamedTuple):
    """A group's applied estimation ids, split by how backends treat them."""

    pepolar: list[str]
    gre: list[str]
    synb0: list[str]
    anat: list[str]
    syn: list[str]


def _ids_by_kind(grouping, corrected) -> MethodGroups:
    kinds = MethodGroups([], [], [], [], [])
    for b0field_id in sorted(corrected):
        method = grouping.estimations[b0field_id].method
        if method is CorrectionMethod.PEPOLAR:
            kinds.pepolar.append(b0field_id)
        elif method is CorrectionMethod.SYNB0:
            kinds.synb0.append(b0field_id)
        elif method is CorrectionMethod.UNPILE:
            kinds.anat.append(b0field_id)
        elif method is CorrectionMethod.NIPREPS_SYN:
            kinds.syn.append(b0field_id)
        else:
            kinds.gre.append(b0field_id)
    return kinds


def _no_sdc_sentence(grouping) -> str:
    """Definite no-correction wording for a series no estimation corrects."""
    if grouping.policy.sdc_anat_reference != 'none' and not grouping.policy.ignore_sdc:
        # A fieldmap-less correction was enabled but could not be set up for
        # this series (the grouping reported why as an error).
        return (
            'No fieldmap could be set up (the fieldmap-less correction could not be '
            'used for this series): susceptibility distortion is NOT corrected.'
        )
    return (
        'No fieldmap is available and fieldmap-less SDC is disabled '
        '(--sdc-anat-reference none): susceptibility distortion is NOT corrected.'
    )


def _fieldmapless_label(method: str, reference: str) -> str:
    """A short name for a ``(--sdc-fieldmapless-method, --sdc-anat-reference)`` pair."""
    target = {
        'synb0': 'a SyNb0 synthetic b=0',
        'invt1w': 'the inverted T1w',
        't2w': 'the T2w',
    }[reference]
    if method == 'topup':
        return 'SyNb0 + TOPUP'
    if method == 'syn':
        return f'SyN registration to {target}'
    return f'unpile against {target}'


def _fallback_phrase(grouping, selection) -> str:
    """What happens to a blip group with no opposing pair on a DRBUDDI path."""
    if _fieldmapless_fallback(grouping, selection) is None:
        return 'they are left uncorrected (fieldmap-less SDC is disabled or has no anatomy)'
    policy = grouping.policy
    label = _fieldmapless_label(policy.fieldmapless_method, policy.sdc_anat_reference)
    return f'they get the fieldmap-less correction instead ({label})'


def _fieldmapless_after_hmc(lines, grouping, kinds, step, hmc_name, output) -> int | None:
    """Narrate a fieldmap-less correction that runs after HMC; ``None`` if there is none."""
    if kinds.synb0:
        for b0field_id in kinds.synb0:
            names = _names(grouping.estimations[b0field_id].sources)
            lines.append(
                f'  {step}. SyNb0 synthesizes an undistorted b=0 image from the T1w ({names}) '
                f'and the {hmc_name}-corrected b=0; TOPUP pairs the two (the synthetic b=0 '
                'as a zero-readout-time volume), and the resulting field is applied to '
                f'unwarp the {output}.'
            )
            step += 1
        return step
    if kinds.syn:
        for b0field_id in kinds.syn:
            estimation = grouping.estimations[b0field_id]
            names = _names(estimation.sources)
            if estimation.reference == 'synb0':
                lines.append(
                    f'  {step}. SyNb0 synthesizes an undistorted b=0 image from the T1w '
                    f'({names}); a constrained ANTs SyN registration of the b=0 to it '
                    f'estimates a fieldmap-less warp, applied to unwarp the {output}.'
                )
            else:
                lines.append(
                    f'  {step}. A standalone fieldmap-less SyN warp is estimated by a '
                    f'constrained ANTs registration of the T1w ({names}) to a fieldmap '
                    f'atlas, and applied to unwarp the {output}.'
                )
            step += 1
        return step
    if kinds.anat:
        for b0field_id in kinds.anat:
            estimation = grouping.estimations[b0field_id]
            names = _names(estimation.sources)
            if estimation.reference == 'synb0':
                lines.append(
                    f'  {step}. SyNb0 synthesizes an undistorted b=0 image from the T1w '
                    f'({names}) and the {hmc_name}-corrected b=0; unpile fits the '
                    'susceptibility field by matching the b=0 to it with a model of the EPI '
                    f'readout, and the field is applied to unwarp the {output}.'
                )
            else:
                lines.append(
                    f'  {step}. unpile fits the susceptibility field by matching the '
                    f'{hmc_name}-corrected b=0 to the T2w ({names}) with a model of the EPI '
                    'readout, fitting the position of the T2w at the same time, and the '
                    f'field is applied to unwarp the {output}.'
                )
            step += 1
        return step
    return None


def _names(paths) -> str:
    return ', '.join(_basename(path) for path in paths)


def _structural_note(grouping) -> str | None:
    """DRBUDDI's multimodal --structural line, when the subject has a T2w."""
    t2ws = grouping.anat_files('T2w')
    if not t2ws:
        return None
    return (
        f'     DRBUDDI additionally uses the T2w image ({_names(t2ws)}) as a structural '
        'registration target (multimodal correction).'
    )


def _describe_drbuddi_pairs(
    lines, grouping, multipart_id, pepolar_ids, dgroups, step, *, pipeline, fallback
) -> int:
    """Narrate per-blip-pair DRBUDDI correction for the methods that route PEPOLAR through it.

    DIFFPREP and SHORELine both hand DRBUDDI one matched blip pair at a time
    (:attr:`~.methods.HmcCapabilities.decomposes_pepolar_pairs`), so their
    narration is identical apart from the ``pipeline`` name in the multi-pair
    note and the ``fallback`` clause for groups with no opposing blip -
    the fieldmap-less correction takes those on either path. Returns the next
    step number.
    """
    for b0field_id in pepolar_ids:
        estimation = grouping.estimations[b0field_id]
        pairs = blip_pair_polarities(grouping, estimation)
        complete = sorted(
            (key for key, pols in pairs.items() if len(pols) == 2), key=blip_sort_key
        )
        unpaired = sorted((key for key, pols in pairs.items() if len(pols) < 2), key=blip_sort_key)
        if len(complete) > 1:
            labels = '; '.join(describe_blip_group(key) for key in complete)
            lines.append(
                f'     Estimation forms {len(complete)} blip pairs ({labels}); each is '
                f'corrected in its own {pipeline} pipeline and the corrected '
                'results are combined.'
            )
        axis_counts = Counter(key[0] for key in complete)
        for key in complete:
            up, down = _split_polarities(dgroups, key)
            up_names = ', '.join(dgroup.key for dgroup in up) or 'borrowed series'
            down_names = ', '.join(dgroup.key for dgroup in down) or 'borrowed series'
            where = f'the {key[0]} axis'
            if key[1] is not None and axis_counts[key[0]] > 1:
                where += f' (TRT {key[1]:g}s)'
            lines.append(
                f'  {step}. DRBUDDI estimates distortion along {where} '
                f'from the blip-up ({up_names}) and blip-down ({down_names}) '
                'data and applies the correction to every volume.'
            )
            step += 1
        if unpaired:
            groups = '; '.join(describe_blip_group(key) for key in unpaired)
            lines.append(f'  {step}. DRBUDDI has no opposing blip for {groups}, so {fallback}.')
            step += 1
        note = _borrow_note(grouping, multipart_id, b0field_id)
        if note:
            lines.append(f'     {note}')
    structural = _structural_note(grouping)
    if structural:
        lines.append(structural)
    return step


def _describe_eddy(lines, grouping, multipart_id, corrected, dgroups, step):
    kinds = _ids_by_kind(grouping, corrected)
    pepolar_ids = kinds.pepolar
    gre_ids = kinds.gre

    if pepolar_ids:
        all_sources = set()
        signatures = set()
        for b0field_id in pepolar_ids:
            estimation = grouping.estimations[b0field_id]
            all_sources.update(estimation.sources)
            signatures.update(
                grouping.files[path].signature.key
                for path in estimation.sources
                if path in grouping.files and grouping.files[path].is_epi_like
            )
        fmap_only = sorted(
            path
            for path in all_sources
            if path in grouping.files and grouping.files[path].datatype == 'fmap'
        )
        lines.append(
            f'  {step}. TOPUP estimates the susceptibility field from b=0 images '
            f'spanning {len(signatures)} distortion groups '
            f'({len(signatures)} rows in the acquisition-parameters file).'
        )
        if fmap_only:
            lines.append(
                f'     Extra b=0 images from fmap/: '
                f'{", ".join(_basename(path) for path in fmap_only)}.'
            )
        for b0field_id in pepolar_ids:
            note = _borrow_note(grouping, multipart_id, b0field_id)
            if note:
                lines.append(f'     {note}')
        step += 1
        lines.append(
            f'  {step}. eddy corrects head motion, eddy currents, and susceptibility '
            'distortion in one model, using the TOPUP field. Every volume is '
            'assigned to its distortion group in the eddy index file.'
        )
        step += 1
    elif kinds.synb0:
        for b0field_id in kinds.synb0:
            estimation = grouping.estimations[b0field_id]
            t1w_names = ', '.join(_basename(path) for path in estimation.sources)
            lines.append(
                f'  {step}. SyNb0 synthesizes an undistorted b=0 image from the T1w ({t1w_names}).'
            )
            step += 1
        lines.append(
            f'  {step}. TOPUP estimates the susceptibility field from the acquired '
            'b=0 images plus the synthetic b=0, which enters as a zero-readout-time '
            'volume (an extra row in the acquisition-parameters file).'
        )
        step += 1
        lines.append(
            f'  {step}. eddy corrects head motion, eddy currents, and susceptibility '
            'distortion in one model, using the TOPUP field.'
        )
        step += 1
    elif gre_ids:
        for b0field_id in gre_ids:
            estimation = grouping.estimations[b0field_id]
            lines.append(
                f'  {step}. A fieldmap is computed from the '
                f'{_METHOD_LABELS[estimation.method]} acquisition '
                f'({", ".join(_basename(path) for path in estimation.sources)}).'
            )
            step += 1
        lines.append(
            f'  {step}. eddy corrects head motion and eddy currents; the fieldmap '
            'is applied to unwarp the results.'
        )
        step += 1
    elif kinds.anat:
        lines.append(f'  {step}. eddy corrects head motion and eddy currents.')
        step += 1
        step = _fieldmapless_after_hmc(lines, grouping, kinds, step, 'eddy', 'eddy output')
    elif kinds.syn:
        lines.append(f'  {step}. eddy corrects head motion and eddy currents.')
        step = _fieldmapless_after_hmc(lines, grouping, kinds, step + 1, 'eddy', 'results')
    else:
        lines.append(
            f'  {step}. eddy corrects head motion and eddy currents. ' + _no_sdc_sentence(grouping)
        )
        step += 1

    return step


def _describe_tortoise(lines, grouping, selection, multipart_id, corrected, dgroups, step):
    kinds = _ids_by_kind(grouping, corrected)
    pepolar_ids = kinds.pepolar
    gre_ids = kinds.gre

    if len(dgroups) > 1:
        keys = ', '.join(dgroup.key for dgroup in dgroups)
        lines.append(
            f'  {step}. TORTOISE DIFFPREP corrects head motion and eddy currents separately '
            f'for each distortion group ({keys}).'
        )
    else:
        lines.append(f'  {step}. TORTOISE DIFFPREP corrects head motion and eddy currents.')
    step += 1

    if pepolar_ids:
        return _describe_drbuddi_pairs(
            lines,
            grouping,
            multipart_id,
            pepolar_ids,
            dgroups,
            step,
            pipeline='DIFFPREP+DRBUDDI',
            fallback=_fallback_phrase(grouping, selection),
        )
    after = _fieldmapless_after_hmc(lines, grouping, kinds, step, 'DIFFPREP', 'DIFFPREP output')
    if after is not None:
        return after
    if gre_ids:
        for b0field_id in gre_ids:
            estimation = grouping.estimations[b0field_id]
            lines.append(
                f'  {step}. A fieldmap is computed from the '
                f'{_METHOD_LABELS[estimation.method]} acquisition and applied to '
                'unwarp the DIFFPREP output.'
            )
            step += 1
    else:
        lines.append(f'  {step}. ' + _no_sdc_sentence(grouping))
        step += 1

    return step


def _describe_shoreline(lines, grouping, selection, multipart_id, corrected, dgroups, step):
    kinds = _ids_by_kind(grouping, corrected)
    pepolar_ids = kinds.pepolar
    gre_ids = kinds.gre

    if selection.shoreline_model == 'none':
        lines.append(
            f'  {step}. SHORELine registers the b=0 images to one another; each '
            'non-b=0 volume gets the transform of its nearest b=0 image '
            '(no signal model; eddy currents are not corrected).'
        )
    else:
        model = '3dSHORE' if selection.shoreline_model == '3dshore' else 'tensor'
        lines.append(
            f'  {step}. SHORELine iteratively corrects head motion by registering '
            f'each volume to a {model} signal-model prediction '
            '(eddy currents are not corrected).'
        )
    step += 1

    if pepolar_ids:
        step = _describe_drbuddi_pairs(
            lines,
            grouping,
            multipart_id,
            pepolar_ids,
            dgroups,
            step,
            pipeline='SHORELine+DRBUDDI',
            fallback=_fallback_phrase(grouping, selection),
        )
    elif kinds.synb0 or kinds.syn or kinds.anat:
        step = _fieldmapless_after_hmc(
            lines, grouping, kinds, step, 'SHORELine', 'corrected series'
        )
    elif gre_ids:
        for b0field_id in gre_ids:
            estimation = grouping.estimations[b0field_id]
            lines.append(
                f'  {step}. A fieldmap is computed from the '
                f'{_METHOD_LABELS[estimation.method]} acquisition and applied to '
                'unwarp the corrected series.'
            )
            step += 1
    else:
        lines.append(f'  {step}. ' + _no_sdc_sentence(grouping))
        step += 1

    return step


def _describe_eddy_drbuddi(lines, grouping, selection, multipart_id, corrected, dgroups, step):
    kinds = _ids_by_kind(grouping, corrected)
    pepolar_ids = kinds.pepolar
    with_topup = SdcTool.TOPUP in selection.pepolar_tools

    if pepolar_ids:
        if with_topup:
            lines.append(
                f'  {step}. TOPUP estimates an initial susceptibility field from the '
                'b=0 images (as in the FSL path).'
            )
            step += 1
            lines.append(
                f'  {step}. eddy corrects head motion and eddy currents using the TOPUP field.'
            )
            step += 1
        else:
            lines.append(
                f'  {step}. eddy corrects head motion and eddy currents (no TOPUP '
                'field: a mask from the distorted images guides it).'
            )
            step += 1
        # The refinement stage depends on the data: DRBUDDI needs reverse
        # phase-encoded dMRI *series* (a lone reverse b=0 was already consumed
        # by TOPUP); otherwise there is no second stage.
        all_pairs: dict = {}
        for b0field_id in pepolar_ids:
            all_pairs.update(blip_pair_polarities(grouping, grouping.estimations[b0field_id]))
        refine_pairs = sorted(
            {
                key
                for b0field_id in pepolar_ids
                for key in dwi_blip_pairs(grouping, grouping.estimations[b0field_id])
            },
            key=blip_sort_key,
        )
        if not refine_pairs:
            if with_topup:
                # A lone reverse b=0 was already consumed by TOPUP and eddy's
                # output is already unwarped, so there is no second stage.
                lines.append(
                    f'  {step}. There is no reverse phase-encoded dMRI series for '
                    'DRBUDDI to refine with: correction is single-stage (TOPUP + eddy).'
                )
                step += 1
            elif any(len(pols) == 2 for pols in all_pairs.values()):
                # DRBUDDI-only with the pair completed by an epi fieldmap:
                # DRBUDDI runs in b=0-only (epi) mode after eddy.
                lines.append(
                    f'  {step}. DRBUDDI estimates distortion from the mean '
                    'motion-corrected b=0 and a reverse phase-encoded fieldmap '
                    'b=0 (b=0-only registration) and applies the correction to '
                    'every volume.'
                )
                step += 1
            else:
                lines.append(
                    f'  {step}. There is no reverse phase-encoded dMRI series for '
                    'DRBUDDI to correct with: susceptibility distortion is NOT corrected.'
                )
                step += 1
        elif len(all_pairs) > 1:
            # DRBUDDI's single pass corrects one matched pair. TOPUP can pool
            # the groups; without it this selection is infeasible.
            labels = '; '.join(
                describe_blip_group(key) for key in sorted(all_pairs, key=blip_sort_key)
            )
            if with_topup:
                lines.append(
                    f'  {step}. The estimation spans {len(all_pairs)} blip groups ({labels}); '
                    'TOPUP+eddy corrects them together, but the single-pass DRBUDDI '
                    'refinement covers one matched pair, so no DRBUDDI refinement is applied.'
                )
            else:
                lines.append(
                    f'  {step}. The estimation spans {len(all_pairs)} blip groups ({labels}); '
                    'DRBUDDI corrects exactly one matched pair per run, so this pooled '
                    'DRBUDDI-only selection is infeasible and no distortion-correction '
                    'stage is planned.'
                )
            step += 1
        else:
            axis_counts = Counter(key[0] for key in refine_pairs)
            for key in refine_pairs:
                where = f'the {key[0]} axis'
                if key[1] is not None and axis_counts[key[0]] > 1:
                    where += f' (TRT {key[1]:g}s)'
                if with_topup:
                    lines.append(
                        f'  {step}. DRBUDDI re-estimates distortion along {where} '
                        'from the eddy-corrected blip-up/blip-down dMRI series, refining '
                        'the TOPUP correction.'
                    )
                else:
                    lines.append(
                        f'  {step}. DRBUDDI estimates distortion along {where} '
                        'from the eddy-corrected blip-up/blip-down dMRI series and '
                        'applies the correction.'
                    )
                step += 1
            for b0field_id in pepolar_ids:
                note = _borrow_note(grouping, multipart_id, b0field_id)
                if note:
                    lines.append(f'     {note}')
            structural = _structural_note(grouping)
            if structural:
                lines.append(structural)
    elif kinds.synb0:
        for b0field_id in kinds.synb0:
            estimation = grouping.estimations[b0field_id]
            t1w_names = ', '.join(_basename(path) for path in estimation.sources)
            lines.append(
                f'  {step}. SyNb0 synthesizes an undistorted b=0 image from the T1w '
                f'({t1w_names}); TOPUP treats it as a zero-readout-time volume, and eddy '
                'corrects head motion, eddy currents, and susceptibility distortion in one '
                'model using the resulting field. With no reverse phase-encoded dMRI series '
                'the DRBUDDI second stage does not run.'
            )
            step += 1
    elif kinds.anat:
        lines.append(
            f'  {step}. eddy corrects head motion and eddy currents. With no reverse '
            'phase-encoded dMRI series the DRBUDDI second stage does not run.'
        )
        step += 1
        step = _fieldmapless_after_hmc(lines, grouping, kinds, step, 'eddy', 'eddy output')
    elif kinds.gre:
        lines.append(
            f'  {step}. Without a PEPOLAR fieldmap the DRBUDDI second stage adds '
            'nothing: processing follows the FSL path (fieldmap-based correction).'
        )
        step += 1
    elif kinds.syn:
        against_synb0 = any(
            grouping.estimations[b0field_id].reference == 'synb0' for b0field_id in kinds.syn
        )
        lines.append(
            f'  {step}. Without a PEPOLAR fieldmap the DRBUDDI second stage adds '
            'nothing: processing follows the FSL path with a fieldmap-less SyN warp'
            + (' against a SyNb0 synthetic b=0.' if against_synb0 else '.')
        )
        step += 1
    else:
        lines.append(f'  {step}. ' + _no_sdc_sentence(grouping))
        step += 1

    return step


def default_preview_selections() -> tuple[MethodSelection, ...]:
    """The method selections every full report previews, in display order."""
    return (
        selection_for_config('eddy', 'topup'),
        selection_for_config('tortoise', 'drbuddi'),
        selection_for_config('eddy', 'topup+drbuddi'),
        selection_for_config('shoreline', 'drbuddi'),
    )


def full_report(grouping: DWIGrouping) -> str:
    """The grouping report followed by every default method-selection preview."""
    sections = [report_text(grouping)]
    for selection in default_preview_selections():
        sections.append(describe_processing(grouping, selection))
    return '\n'.join(sections)
