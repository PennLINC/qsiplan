"""Compile a grouping and a method selection into an execution plan.

:func:`compile_plan` is pure - no config reads, no I/O - and produces one
immutable, serializable :class:`ExecutionPlan` describing exactly what the
selected methods would do with the grouped data: one :class:`ProcessingRun`
per HMC+SDC invocation, each an *ordered* sequence of :class:`PlanStage`
steps, and one :class:`OutputAssembly` per output file.

Stage order is the point. eddy+TOPUP estimates the field first and consumes
it *during* motion correction (``ESTIMATE`` then ``HMC_WITH_FIELD``), while
DIFFPREP and SHORELine correct motion first and estimate afterwards
(``HMC`` then ``ESTIMATE_AND_APPLY``). Renderings of the plan - prose,
HTML, diagrams - follow the stage sequence instead of hard-coding either
order.

The plan is the single source of routing truth the other grouping layers
converge on: validation returns ``plan.issues``, the adapters become views
over ``plan.runs``, previews narrate stages, and workflow construction
builds one nipype graph per run.
"""

from __future__ import annotations

import dataclasses
import enum
import os.path as op

from .adapters import (
    PreprocUnit,
    _decompose_unit,
    _decomposes_pepolar_pairs,
    _encoding_key,
    _split_by_encoding,
)
from .methods import HMC_CAPABILITIES, SDC_CAPABILITIES, HmcMethod, MethodSelection, SdcTool
from .models import (
    ANAT_REFERENCES,
    FIELDMAPLESS_METHODS,
    CorrectionMethod,
    DWIGrouping,
    FieldmapEstimation,
)
from .validation import (
    GroupingIssue,
    _pepolar_signature_count,
    blip_pair_polarities,
    blip_sort_key,
    describe_blip_group,
    dwi_blip_pairs,
    error,
    warning,
)

#: Version of the ``ExecutionPlan.to_dict`` JSON layout; bump on incompatible
#: shape changes. 2: per-run ``dwi_phase_files`` (complex-valued DWI).
PLAN_SCHEMA_VERSION = 2


class StageRole(enum.StrEnum):
    """What one stage of a processing run does."""

    ESTIMATE = 'estimate'  # produce a field for a later stage to consume
    HMC = 'hmc'  # motion/eddy-current correction only
    HMC_WITH_FIELD = 'hmc-with-field'  # HMC consuming a previously estimated field
    ESTIMATE_AND_APPLY = 'estimate+apply'  # estimate and correct in one stage
    REFINE = 'refine'  # second-stage refinement of an already-corrected series


@dataclasses.dataclass(frozen=True)
class PlanStage:
    """One step of a processing run.

    ``consumes`` is the index of the ``ESTIMATE`` stage whose field this
    stage applies (eddy consuming TOPUP's field), or ``None``.
    ``structural_target`` is the anatomical target kind a registration-based
    stage uses (``'t2w'``, ``'synb0'`` or ``'t1w'``), or ``None``.

    All SDC warps except the integrated TOPUP-into-eddy field are composed
    into the final resampling; a separate "apply" stage never exists, which
    is why there is no role for it.
    """

    index: int
    role: StageRole
    tool: str
    method: CorrectionMethod | None = None
    estimation: str | None = None
    fieldmap_sources: tuple[str, ...] = ()
    borrowed_b0: tuple[str, ...] = ()
    plus_files: tuple[str, ...] = ()
    minus_files: tuple[str, ...] = ()
    structural_target: str | None = None
    consumes: int | None = None

    def to_dict(self) -> dict:
        return {
            'index': self.index,
            'role': self.role.value,
            'tool': self.tool,
            'method': self.method.value if self.method else None,
            'estimation': self.estimation,
            'fieldmap_sources': list(self.fieldmap_sources),
            'borrowed_b0': list(self.borrowed_b0),
            'plus_files': list(self.plus_files),
            'minus_files': list(self.minus_files),
            'structural_target': self.structural_target,
            'consumes': self.consumes,
        }


@dataclasses.dataclass(frozen=True)
class ProcessingRun:
    """One HMC+SDC invocation: the selection-level refinement of a correction unit.

    ``key`` doubles as the run's output name (matching the corresponding
    :class:`~.adapters.PreprocUnit`'s ``output_name``, split suffixes
    included); ``logical_unit`` is the :class:`~.models.CorrectionUnit` this
    run refines - usually 1:1, but a decomposing HMC method (DIFFPREP,
    SHORELine) splits a multi-blip-group PEPOLAR unit into one run per
    group. ``estimation`` is the (possibly pair-restricted) fieldmap
    estimation correcting these series, mirroring the adapter's unit.
    """

    key: str
    logical_unit: str
    dwi_files: tuple[str, ...]
    estimation: FieldmapEstimation | None
    stages: tuple[PlanStage, ...]
    output_group: str
    #: Phase companion of each complex-valued series, keyed by the magnitude
    #: path in ``dwi_files`` (see :attr:`~.adapters.PreprocUnit.dwi_phase_files`),
    #: so a persisted plan says which images are combined without a re-glob.
    dwi_phase_files: dict[str, str] = dataclasses.field(default_factory=dict)

    def stage_with(self, tool) -> PlanStage | None:
        """The first stage run by ``tool`` (an :class:`~.methods.SdcTool`,
        :class:`~.methods.HmcMethod` or raw string), or ``None``."""
        wanted = getattr(tool, 'value', tool)
        for stage in self.stages:
            if stage.tool == wanted:
                return stage
        return None

    @property
    def hmc_stage(self) -> PlanStage:
        """The single motion-correction stage (HMC or HMC_WITH_FIELD)."""
        for stage in self.stages:
            if stage.role in (StageRole.HMC, StageRole.HMC_WITH_FIELD):
                return stage
        raise ValueError(f"run '{self.key}' has no HMC stage")

    def to_dict(self) -> dict:
        return {
            'key': self.key,
            'logical_unit': self.logical_unit,
            'dwi_files': list(self.dwi_files),
            'estimation': self.estimation.b0field_id if self.estimation else None,
            'stages': [stage.to_dict() for stage in self.stages],
            'output_group': self.output_group,
            'dwi_phase_files': dict(sorted(self.dwi_phase_files.items())),
        }


@dataclasses.dataclass(frozen=True)
class OutputAssembly:
    """How one output file is assembled from its runs' corrected results."""

    output_group: str
    input_runs: tuple[str, ...]
    strategy: str  # 'concat' | 'average' | 'none' (identity)
    output_name: str

    def to_dict(self) -> dict:
        return {
            'output_group': self.output_group,
            'input_runs': list(self.input_runs),
            'strategy': self.strategy,
            'output_name': self.output_name,
        }


@dataclasses.dataclass(frozen=True)
class ExecutionPlan:
    """Everything the selected methods would do with a grouped dataset."""

    selection: MethodSelection
    schema_version: int
    runs: tuple[ProcessingRun, ...]
    outputs: tuple[OutputAssembly, ...]
    issues: tuple[GroupingIssue, ...]

    def run(self, key: str) -> ProcessingRun:
        for processing_run in self.runs:
            if processing_run.key == key:
                return processing_run
        raise KeyError(key)

    def runs_for(self, output_group: str) -> list[ProcessingRun]:
        return [r for r in self.runs if r.output_group == output_group]

    def to_dict(self) -> dict:
        return {
            'schema_version': self.schema_version,
            'selection': {
                'hmc': self.selection.hmc.value,
                'shoreline_model': self.selection.shoreline_model,
                'pepolar_tools': [tool.value for tool in self.selection.pepolar_tools],
                'label': self.selection.label(),
            },
            'runs': [run.to_dict() for run in self.runs],
            'outputs': [assembly.to_dict() for assembly in self.outputs],
            'issues': [
                {
                    'severity': issue.severity,
                    'code': issue.code,
                    'message': issue.message,
                    'files': list(issue.files),
                    'scope': issue.scope,
                    'run': issue.run,
                }
                for issue in self.issues
            ],
        }


def _pepolar_stages(
    grouping: DWIGrouping, selection: MethodSelection, unit: PreprocUnit
) -> list[PlanStage]:
    """The stage sequence for a PEPOLAR-corrected unit under ``selection``."""
    t2w_reference = ANAT_REFERENCES['t2w']
    has_t2w = bool(grouping.anat_files(t2w_reference.source_suffix))
    t2w = t2w_reference.structural_target if has_t2w else None
    estimation = unit.estimation
    common = {
        'method': CorrectionMethod.PEPOLAR,
        'estimation': estimation.b0field_id,
        'fieldmap_sources': tuple(estimation.sources),
        'borrowed_b0': unit.extra_b0,
        'plus_files': unit.plus_files,
        'minus_files': unit.minus_files,
    }

    if selection.hmc is not HmcMethod.EDDY:
        # DIFFPREP/SHORELine correct motion first; the unit has already been
        # decomposed to a single matched blip pair, which DRBUDDI corrects.
        return [
            PlanStage(index=0, role=StageRole.HMC, tool=selection.hmc.value),
            PlanStage(
                index=1,
                role=StageRole.ESTIMATE_AND_APPLY,
                tool=SdcTool.DRBUDDI.value,
                structural_target=t2w,
                **common,
            ),
        ]

    stages: list[PlanStage] = []
    if SdcTool.TOPUP in selection.pepolar_tools:
        stages.append(
            PlanStage(index=0, role=StageRole.ESTIMATE, tool=SdcTool.TOPUP.value, **common)
        )
        stages.append(
            PlanStage(
                index=1, role=StageRole.HMC_WITH_FIELD, tool=HmcMethod.EDDY.value, consumes=0
            )
        )
    else:
        stages.append(PlanStage(index=0, role=StageRole.HMC, tool=HmcMethod.EDDY.value))

    # The single-pass DRBUDDI stage consumes exactly one matched blip pair;
    # eddy keeps multi-group units pooled, so those skip the refinement. The
    # refinement additionally needs a reverse-PE DWI *series*: with a lone
    # reverse b=0, eddy's output is already unwarped by the TOPUP field, so a
    # DRBUDDI pass would apply the same correction a second time.
    wants_drbuddi = SdcTool.DRBUDDI in selection.pepolar_tools and unit.is_single_blip_pair
    refining = SdcTool.TOPUP in selection.pepolar_tools
    if wants_drbuddi and refining and not dwi_blip_pairs(grouping, estimation):
        wants_drbuddi = False
    if wants_drbuddi:
        role = StageRole.REFINE if refining else StageRole.ESTIMATE_AND_APPLY
        stages.append(
            PlanStage(
                index=len(stages),
                role=role,
                tool=SdcTool.DRBUDDI.value,
                structural_target=t2w,
                **common,
            )
        )
    return stages


def _fieldmapless_fallback(
    grouping: DWIGrouping, selection: MethodSelection
) -> tuple[CorrectionMethod, str, tuple[str, ...]] | None:
    """The fieldmap-less correction a blip group with no opposing pair falls back to.

    A decomposed PEPOLAR unit's unpaired blip group has no estimation of its
    own (its series were reached by the PEPOLAR one), so the policy's
    fieldmap-less correction is planned for it directly: ``(method,
    structural_target, anatomical sources)``, or ``None`` when fieldmap-less
    SDC is off or the anatomy is missing.
    """
    policy = grouping.policy
    if policy.ignore_sdc or policy.sdc_anat_reference == 'none':
        return None
    spec = FIELDMAPLESS_METHODS[policy.fieldmapless_method]
    reference = ANAT_REFERENCES[policy.sdc_anat_reference]
    sources = tuple(grouping.anat_files(reference.source_suffix))
    if not sources:
        return None
    return spec.method, reference.structural_target, sources


def _stages_for_unit(
    grouping: DWIGrouping, selection: MethodSelection, unit: PreprocUnit
) -> tuple[PlanStage, ...]:
    """The ordered stage sequence one :class:`~.adapters.PreprocUnit` runs."""
    if unit.is_pepolar:
        return tuple(_pepolar_stages(grouping, selection, unit))

    hmc = PlanStage(index=0, role=StageRole.HMC, tool=selection.hmc.value)
    estimation = unit.estimation
    if estimation is None:
        # Series some estimation reaches but this unit has none for: an unpaired
        # blip group split off a PEPOLAR unit.
        unpaired = any(grouping.application.get(path) for path in unit.dwi_files)
        fallback = _fieldmapless_fallback(grouping, selection) if unpaired else None
        if fallback is None:
            return (hmc,)
        method, target, sources = fallback
        tool = {
            CorrectionMethod.SYNB0: SdcTool.TOPUP,
            CorrectionMethod.NIPREPS_SYN: SdcTool.SYN,
            CorrectionMethod.UNPILE: SdcTool.UNPILE,
        }[method]
        return (
            hmc,
            PlanStage(
                index=1,
                role=StageRole.ESTIMATE_AND_APPLY,
                tool=tool.value,
                method=method,
                fieldmap_sources=sources,
                structural_target=target,
            ),
        )
    common = {
        'method': unit.method,
        'estimation': estimation.b0field_id,
        'fieldmap_sources': tuple(estimation.sources),
        'structural_target': estimation.structural_target,
    }

    if unit.is_gre:
        # The classic fieldmap workflow estimates after HMC; the warp is
        # composed into the final resampling.
        return (
            hmc,
            PlanStage(
                index=1,
                role=StageRole.ESTIMATE_AND_APPLY,
                tool=SdcTool.FIELDMAP.value,
                **{**common, 'structural_target': None},
            ),
        )
    if unit.is_nipreps_syn:
        # SyN registers the b=0 to the inverted T1w or to a SyNb0 synthetic b=0.
        return (
            hmc,
            PlanStage(
                index=1, role=StageRole.ESTIMATE_AND_APPLY, tool=SdcTool.SYN.value, **common
            ),
        )

    # SYNB0 is pepolar-by-synthesis: the synthetic undistorted b=0 is TOPUP's
    # zero-readout partner. Under eddy it joins TOPUP's inputs before eddy,
    # which consumes the field like a measured PEPOLAR one (whatever
    # --sdc-method says, which only chooses the PEPOLAR tools); after DIFFPREP
    # or SHORELine TOPUP pairs it with the motion-corrected b=0, and the field
    # is composed into the final resampling.
    if unit.method is CorrectionMethod.SYNB0:
        if selection.hmc is HmcMethod.EDDY:
            return (
                PlanStage(index=0, role=StageRole.ESTIMATE, tool=SdcTool.TOPUP.value, **common),
                PlanStage(
                    index=1, role=StageRole.HMC_WITH_FIELD, tool=HmcMethod.EDDY.value, consumes=0
                ),
            )
        return (
            hmc,
            PlanStage(
                index=1, role=StageRole.ESTIMATE_AND_APPLY, tool=SdcTool.TOPUP.value, **common
            ),
        )

    # unpile (explicit only) fits the field after head motion correction, with
    # every HMC method: the b=0 against a SyNb0 synthetic b=0 or the T2w.
    if unit.method is CorrectionMethod.UNPILE:
        return (
            hmc,
            PlanStage(
                index=1, role=StageRole.ESTIMATE_AND_APPLY, tool=SdcTool.UNPILE.value, **common
            ),
        )
    return (hmc,)


def _plan_issues(grouping: DWIGrouping, selection: MethodSelection) -> list[GroupingIssue]:
    """The feasibility issues for ``selection``, one output at a time.

    The message text is frozen by the golden reports; the branch conditions
    are expressed in selection terms - eddy with or without a DRBUDDI
    refinement, TORTOISE, SHORELine.
    """
    is_eddy = selection.hmc is HmcMethod.EDDY
    with_topup = is_eddy and SdcTool.TOPUP in selection.pepolar_tools
    with_drbuddi = is_eddy and SdcTool.DRBUDDI in selection.pepolar_tools
    refining = with_topup and with_drbuddi
    drbuddi_only = with_drbuddi and not with_topup

    issues: list[GroupingIssue] = []
    for multipart_id, concat in sorted(grouping.concatenation_groups.items()):
        issues.extend(_check_shelling(grouping, selection, multipart_id, concat))
        estimations = {
            dgroup.b0field_source
            for dgroup in grouping.distortion_groups_in(multipart_id)
            if dgroup.b0field_source is not None
        }
        uncorrected = [
            dgroup.key
            for dgroup in grouping.distortion_groups_in(multipart_id)
            if dgroup.b0field_source is None
        ]

        if uncorrected and not estimations:
            policy = grouping.policy
            why = (
                'fieldmap-less SDC is disabled for it (--sdc-anat-reference none or --ignore sdc)'
                if policy.ignore_sdc or policy.sdc_anat_reference == 'none'
                else 'the fieldmap-less correction could not be set up for it (see the errors)'
            )
            issues.append(
                warning(
                    'no-sdc',
                    f"Output '{concat.output_name}' has no fieldmap and {why}: no "
                    'susceptibility distortion correction will be performed.',
                    concat.dwi_files,
                    scope=multipart_id,
                )
            )

        for b0field_id in sorted(estimations):
            estimation = grouping.estimations[b0field_id]

            if estimation.method is CorrectionMethod.SYNB0:
                # The synthetic b=0 is a target image every method can consume;
                # DRBUDDI's dual-blip refinement simply never runs for these
                # series (there is no reverse-PE dMRI data).
                continue

            if not estimation.is_pepolar:
                # GRE-style fieldmaps route to the classic fieldmap workflow
                # under every selection; the only note is that a requested
                # DRBUDDI refinement has nothing to refine.
                if refining:
                    issues.append(
                        warning(
                            'mixed-non-pepolar',
                            f"Estimation '{b0field_id}' is not PEPOLAR; the DRBUDDI "
                            f'second stage only refines PEPOLAR corrections, so '
                            f"'{concat.output_name}' will get single-stage "
                            'correction.',
                            estimation.sources,
                            scope=multipart_id,
                        )
                    )
                continue

            if with_topup and not with_drbuddi:
                if _pepolar_signature_count(grouping, estimation) < 2:
                    issues.append(
                        error(
                            'topup-single-signature',
                            f"Estimation '{b0field_id}' has only one distortion "
                            f'signature among its sources. TOPUP needs at least two '
                            f'(e.g. opposite phase encoding directions) to estimate '
                            f"a fieldmap for '{concat.output_name}'.",
                            estimation.sources,
                            scope=multipart_id,
                        )
                    )
            elif drbuddi_only:
                pairs = blip_pair_polarities(grouping, estimation)
                if len(pairs) != 1 or any(len(polarities) < 2 for polarities in pairs.values()):
                    labels = '; '.join(
                        describe_blip_group(key) for key in sorted(pairs, key=blip_sort_key)
                    )
                    issues.append(
                        error(
                            'drbuddi-only-infeasible',
                            f"Estimation '{b0field_id}' spans {len(pairs)} blip groups "
                            f'({labels}). DRBUDDI corrects exactly one matched '
                            'blip-up/blip-down group per run, while eddy keeps this '
                            f'output pooled. Use --sdc-method topup or topup+drbuddi '
                            f"for '{concat.output_name}', or split the acquisition into "
                            'separate outputs.',
                            estimation.sources,
                            scope=multipart_id,
                        )
                    )
            elif refining and not dwi_blip_pairs(grouping, estimation):
                # DRBUDDI refinement needs reverse phase-encoded dMRI *series*;
                # a lone reverse b=0 was already consumed by TOPUP.
                fallback = f"'{concat.output_name}' gets single-stage (TOPUP+eddy) correction."
                issues.append(
                    warning(
                        'drbuddi-refinement-not-useful',
                        f"Estimation '{b0field_id}' has no reverse phase-encoded "
                        'dMRI series: a DRBUDDI second stage would reuse the same '
                        'b=0 images TOPUP already consumed and is probably not '
                        f'useful. {fallback}',
                        estimation.sources,
                        scope=multipart_id,
                    )
                )
            else:  # a decomposing method, or eddy refinement with reverse-PE dMRI
                pairs = blip_pair_polarities(grouping, estimation)
                unpaired = sorted(
                    (key for key, pols in pairs.items() if len(pols) < 2), key=blip_sort_key
                )
                if not is_eddy and unpaired:
                    # Each blip group routes on its own: complete pairs to
                    # DRBUDDI, an unpaired group to the fieldmap-less fallback
                    # (DIFFPREP only; SHORELine has none).
                    groups = '; '.join(describe_blip_group(key) for key in unpaired)
                    path_name = 'TORTOISE' if selection.hmc is HmcMethod.TORTOISE else 'SHORELine'
                    policy = grouping.policy
                    spec = FIELDMAPLESS_METHODS[policy.fieldmapless_method]
                    fallback = (
                        f'corrected by the fieldmap-less correction instead ({spec.description}, '
                        f'--sdc-anat-reference {policy.sdc_anat_reference})'
                        if _fieldmapless_fallback(grouping, selection) is not None
                        else 'left uncorrected (fieldmap-less SDC is disabled or has no anatomy)'
                    )
                    issues.append(
                        warning(
                            'drbuddi-no-opposing-pair',
                            f"Estimation '{b0field_id}' has blip group(s) with no "
                            f'opposing (blip-up/blip-down) pair: {groups}. DRBUDDI '
                            f'needs a matched pair, so on the {path_name} path those '
                            f'series are {fallback}. Add the missing reverse blip(s) '
                            f"to use DRBUDDI for '{concat.output_name}'.",
                            estimation.sources,
                            scope=multipart_id,
                        )
                    )
                elif refining and len(pairs) > 1:
                    # eddy pools every group into one TOPUP+eddy, but the
                    # single-pass DRBUDDI refinement handles only one matched pair.
                    labels = '; '.join(
                        describe_blip_group(key) for key in sorted(pairs, key=blip_sort_key)
                    )
                    issues.append(
                        warning(
                            'drbuddi-refinement-multigroup',
                            f"Estimation '{b0field_id}' spans {len(pairs)} blip groups "
                            f'({labels}). TOPUP+eddy corrects them together, but the '
                            f'DRBUDDI refinement handles one matched pair at a time, so '
                            f"'{concat.output_name}' gets single-stage TOPUP+eddy "
                            'correction (no DRBUDDI refinement).',
                            estimation.sources,
                            scope=multipart_id,
                        )
                    )

    return issues


def _check_shelling(grouping, selection, multipart_id, concat) -> list[GroupingIssue]:
    """Shelled/non-shelled rules for one output, in selection terms."""
    issues = []
    shelled = [path for path in concat.dwi_files if grouping.files[path].shelled is True]
    non_shelled = [path for path in concat.dwi_files if grouping.files[path].shelled is False]

    if non_shelled and HMC_CAPABILITIES[selection.hmc].requires_shelled:
        names = ', '.join(op.basename(path) for path in non_shelled)
        issues.append(
            error(
                'eddy-requires-shelled',
                f'eddy requires shelled (DTI/multi-shell) q-space sampling, but '
                f"{names} in output '{concat.output_name}' is not shelled. "
                'Use --hmc-method shoreline or tortoise, which handle non-shelled data.',
                tuple(non_shelled),
                scope=multipart_id,
            )
        )

    if shelled and non_shelled and selection.hmc is not HmcMethod.EDDY:
        issues.append(
            warning(
                'mixed-shelled-nonshelled',
                f"Output '{concat.output_name}' concatenates shelled and "
                f'non-shelled series ({len(shelled)} shelled, '
                f'{len(non_shelled)} non-shelled). TORTOISE can process both, '
                'but consider whether these acquisitions belong in one output '
                '(MultipartID can separate them).',
                tuple(shelled + non_shelled),
                scope=multipart_id,
            )
        )

    return issues


def _mixes_encodings_under_one_warp(grouping: DWIGrouping, unit, selection) -> bool:
    """True when ``unit``'s series are encoded more than one way but its
    correction is one warp for one encoding, applied after HMC
    (:attr:`~.methods.SdcCapabilities.single_encoding`): each encoding then
    needs its own run and warp."""
    if len({_encoding_key(grouping, path) for path in unit.dwi_files}) < 2:
        return False
    return any(
        stage.role is StageRole.ESTIMATE_AND_APPLY
        and stage.tool in SDC_CAPABILITIES
        and (
            SDC_CAPABILITIES[stage.tool].single_encoding
            # Post-HMC TOPUP pairs one encoding with the synthetic b=0.
            or stage.method is CorrectionMethod.SYNB0
        )
        for stage in _stages_for_unit(grouping, selection, unit)
    )


def compile_plan(grouping: DWIGrouping, selection: MethodSelection) -> ExecutionPlan:
    """Compile the execution plan for ``selection`` over a finished grouping.

    Pure: everything comes from the grouping and the selection. The adapter
    views (``to_preproc_units``/``concatenation_scheme``) are derived from
    this plan's runs and assemblies; the parity suite pins that derivation.
    """
    concat_of_unit = {
        unit_key: concat
        for concat in grouping.concatenation_groups.values()
        for unit_key in concat.correction_units
    }
    runs: list[ProcessingRun] = []
    for unit_key in sorted(grouping.correction_units):
        unit = grouping.correction_units[unit_key]
        estimation = grouping.estimations[unit.b0field_source] if unit.b0field_source else None
        concat = concat_of_unit.get(unit.key)
        output_group = concat.key if concat is not None else unit.key

        whole = PreprocUnit(
            grouping=grouping,
            output_name=unit.key,
            dwi_files=unit.dwi_files,
            estimation=estimation,
        )
        if _decomposes_pepolar_pairs(grouping, unit, estimation, selection):
            subunits = _decompose_unit(grouping, unit, estimation)
        elif _mixes_encodings_under_one_warp(grouping, whole, selection):
            subunits = _split_by_encoding(grouping, unit, estimation)
        else:
            subunits = [whole]
        for subunit in subunits:
            runs.append(
                ProcessingRun(
                    key=subunit.output_name,
                    logical_unit=unit.key,
                    dwi_files=subunit.dwi_files,
                    estimation=subunit.estimation,
                    stages=_stages_for_unit(grouping, selection, subunit),
                    output_group=output_group,
                    dwi_phase_files=subunit.dwi_phase_files,
                )
            )

    outputs: list[OutputAssembly] = []
    strategy = grouping.policy.distortion_group_merge
    for _key, concat in sorted(grouping.concatenation_groups.items()):
        input_runs = tuple(run.key for run in runs if run.output_group == concat.key)
        if not input_runs:
            continue
        outputs.append(
            OutputAssembly(
                output_group=concat.key,
                input_runs=input_runs,
                strategy=strategy if len(input_runs) > 1 else 'none',
                output_name=concat.output_name,
            )
        )
    # Defensive mirror of the adapters' fallback for a unit outside every
    # concatenation group (the integrity checker forbids it).
    covered = {run_key for assembly in outputs for run_key in assembly.input_runs}
    for run in runs:
        if run.key not in covered:
            outputs.append(
                OutputAssembly(
                    output_group=run.output_group,
                    input_runs=(run.key,),
                    strategy='none',
                    output_name=run.output_group,
                )
            )

    return ExecutionPlan(
        selection=selection,
        schema_version=PLAN_SCHEMA_VERSION,
        runs=tuple(runs),
        outputs=tuple(outputs),
        issues=tuple(_plan_issues(grouping, selection)),
    )
