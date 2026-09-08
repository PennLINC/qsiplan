"""Issue collection and validation rules for DWI grouping.

Two layers of checks live here:

1. **Grouping-time rules** are applied while a :class:`~.models.DWIGrouping`
   is being built (called from ``inference.py``). They are about the data
   itself and hold no matter which processing software runs later.
2. **Feasibility** is the plan compiler's job (:func:`~.plan.compile_plan`):
   the compiled plan's issues answer "could this method selection actually
   process this?", keeping all tool-specific knowledge out of the grouping
   model.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict

from .models import ANAT_REFERENCES, DWIGrouping, FieldmapEstimation


class GroupingError(RuntimeError):
    """Raised when the grouping contains error-severity issues."""


@dataclasses.dataclass(frozen=True)
class GroupingIssue:
    """One problem or notable decision discovered while grouping.

    ``scope`` names the concatenation group (MultipartID) an issue belongs
    to, when it belongs to exactly one; reports use it to show each issue
    under the right output. ``run`` names the execution-plan processing run
    an issue is about, when it is about exactly one.
    """

    severity: str  # 'error' | 'warning'
    code: str  # stable machine-readable identifier
    message: str
    files: tuple[str, ...] = ()
    scope: str | None = None
    run: str | None = None

    def render(self) -> str:
        return f'{self.severity.upper()} [{self.code}]: {self.message}'


def error(code: str, message: str, files=(), scope=None) -> GroupingIssue:
    return GroupingIssue('error', code, message, tuple(files), scope)


def warning(code: str, message: str, files=(), scope=None) -> GroupingIssue:
    return GroupingIssue('warning', code, message, tuple(files), scope)


@dataclasses.dataclass(frozen=True)
class IssueSpec:
    """What one issue code means, and the severities it may be emitted at.

    Most codes have one severity. A few are decided at emit time - an
    anatomical SDC method that was *demanded* is an error where an inferred
    one is a warning - and list both.
    """

    description: str
    severities: frozenset[str]


def _spec(description: str, *severities: str) -> IssueSpec:
    return IssueSpec(description, frozenset(severities))


#: Every issue code the package can emit, with a one-line meaning. This is
#: the registry the conformance test checks emitters against (every emitted
#: code is registered, every registered code is emitted, severities match),
#: so a typo in an emitter fails CI instead of quietly minting a new code.
#: It is deliberately *not* enforced at emit time: ``error``/``warning`` are
#: public, and a consumer may emit codes of its own through them.
ISSUE_CODES: dict[str, IssueSpec] = {
    # ---- sidecar / metadata indexing --------------------------------------
    'invalid-json-sidecar': _spec(
        'A JSON sidecar could not be parsed or is not an object.', 'error'
    ),
    'ambiguous-json-inheritance': _spec(
        'Two sidecars at the same inheritance level both apply to a file.', 'error'
    ),
    'ambiguous-gradient-inheritance': _spec(
        'Two .bval/.bvec files at the same inheritance level both apply to a file.', 'error'
    ),
    'intendedfor-absolute-path': _spec(
        'An IntendedFor entry is an absolute path (not portable BIDS); used as-is.', 'warning'
    ),
    'intendedfor-missing-target': _spec(
        'An IntendedFor entry names a file that does not exist in the dataset.', 'warning'
    ),
    # ---- complex-valued (part-) acquisitions --------------------------------
    'complex-part-unsupported': _spec(
        'A part-real/part-imag (non-magnitude, non-phase) image was ignored.', 'warning'
    ),
    'phase-without-magnitude': _spec(
        'A part-phase image has no magnitude sibling to accompany, so it was ignored.',
        'warning',
    ),
    # ---- curated fieldmap linkage (E1) --------------------------------------
    'reserved-b0field-prefix': _spec(
        "A curated B0FieldIdentifier uses the reserved 'auto+' prefix.", 'error'
    ),
    'b0field-multisession': _spec(
        'One B0FieldIdentifier is declared by files in several sessions.', 'error'
    ),
    'curated-estimation-unclassifiable': _spec(
        "A B0FieldIdentifier's files do not determine an estimation method.", 'error'
    ),
    'curated-pepolar-single-signature': _spec(
        'A curated PEPOLAR estimation has only one distortion signature.', 'error'
    ),
    'estimation-shim-mismatch': _spec(
        'Files combined for one estimation were acquired with different shims.',
        'error',
        'warning',
    ),
    'intendedfor-superseded': _spec(
        'A fieldmap carries both B0FieldIdentifier and IntendedFor; IntendedFor is ignored.',
        'warning',
    ),
    # ---- IntendedFor translation (E2) ---------------------------------------
    'unlinked-fmap': _spec(
        'An fmap/ EPI has no B0FieldIdentifier or IntendedFor and will not be used.', 'warning'
    ),
    'intendedfor-unclassifiable': _spec(
        'IntendedFor fmap files do not determine an estimation method; not used.', 'warning'
    ),
    # ---- reverse-PE heuristic (E3) and shims --------------------------------
    'reverse-pe-not-inferred': _spec(
        'A session has curated fieldmap metadata, so reverse-PE pairing is not inferred.',
        'warning',
    ),
    'session-multiple-shims': _spec(
        'A session has several shim settings; series only pair within one.', 'warning'
    ),
    'shims-ignored': _spec(
        'Shim checking is disabled; all series treated as compatible.', 'warning'
    ),
    'shim-wildcard': _spec(
        'Series without a ShimSetting are treated as compatible with every shim.', 'warning'
    ),
    'pepolar-dwis-ignored': _spec(
        '--ignore pepolar-dwis dropped a curated DWI-to-DWI reverse-PE pairing.', 'warning'
    ),
    # ---- application ------------------------------------------------------
    'unresolvable-b0fieldsource': _spec(
        'A B0FieldSource names an identifier no file in the subject declares.', 'error'
    ),
    'mixed-application-provenance': _spec(
        'Some DWI series have curated B0FieldSource metadata and others were '
        'assigned automatically.',
        'warning',
    ),
    'estimation-unused': _spec(
        'A curated or translated estimation corrects no DWI series.', 'warning'
    ),
    'cross-session-fieldmap-application': _spec(
        'One estimation corrects DWI series in several sessions (honored, but sessions reshim).',
        'warning',
    ),
    'cross-session-anat-reference': _spec(
        "A session has no anatomical of its own, so another session's is used for SDC.",
        'warning',
    ),
    # ---- anatomical (fieldmap-less) SDC references --------------------------
    'force-sdc-anat-reference-needs-method': _spec(
        "--force sdc-anat-reference was given with --sdc-anat-reference 'none'.", 'error'
    ),
    'sdc-anat-reference-auto-no-anatomicals': _spec(
        '--sdc-anat-reference auto found no T1w or T2w for some series; no anatomical SDC.',
        'warning',
    ),
    'synb0-requires-t1w': _spec('SyNb0 was requested but the subject has no T1w.', 'error'),
    'synb0-missing-pedir': _spec(
        'SyNb0 was requested for series with no PhaseEncodingDirection.', 'error'
    ),
    't2wreg-requires-t2w': _spec('T2Wreg was requested but the subject has no T2w.', 'error'),
    'syn-requires-t1w': _spec('SyN-SDC was requested but the subject has no T1w.', 'error'),
    'syn-missing-pedir': _spec(
        'SyN-SDC was requested for series with no PhaseEncodingDirection.', 'error'
    ),
    # ---- distortion groups, units, outputs ----------------------------------
    'missing-pedir': _spec(
        'A DWI series has no PhaseEncodingDirection and cannot be combined or PEPOLAR-corrected.',
        'warning',
    ),
    'reserved-multipartid-prefix': _spec(
        "A curated MultipartID uses the reserved 'auto+' prefix.", 'error'
    ),
    'multipartid-overridden': _spec(
        'separate_all_dwis overrides the MultipartIDs in the sidecars.', 'warning'
    ),
    'partial-multipart': _spec(
        'Some DWI series have a MultipartID and others do not; the latter stand alone.', 'warning'
    ),
    'multipart-overlap': _spec(
        'A DWI series lists several MultipartIDs and is preprocessed once per group.', 'warning'
    ),
    'multipartid-acq-invalid': _spec(
        "An 'acq-' MultipartID's label is not a valid BIDS label.", 'error'
    ),
    'output-name-collision': _spec(
        'Two output groups would produce the same output name.', 'error'
    ),
    'estimation-spans-outputs': _spec(
        'One estimation corrects series in several correction units (estimated once per unit).',
        'warning',
    ),
    # ---- data compatibility within a unit / output --------------------------
    'maxb-mismatch': _spec(
        'Series concatenated into one output have different maximum b-values.', 'warning'
    ),
    'fov-grid-mismatch': _spec(
        'Series stacked in one correction unit are sampled on different voxel grids.', 'error'
    ),
    'fov-oblique': _spec(
        'Series stacked in one correction unit have differently oriented fields of view.',
        'error',
        'warning',
    ),
    'fov-shifted': _spec(
        'Series stacked in one correction unit share a grid but their fields of view are offset.',
        'warning',
    ),
    # ---- plan feasibility (per method selection) ----------------------------
    'no-sdc': _spec(
        'An output has no fieldmap and no anatomical SDC method enabled; not corrected.',
        'warning',
    ),
    'anat-sdc-unsupported': _spec(
        'A T2Wreg estimation is requested on a path that cannot run it (TORTOISE only).',
        'error',
        'warning',
    ),
    'mixed-non-pepolar': _spec(
        'A DRBUDDI refinement was requested for a non-PEPOLAR estimation; single-stage instead.',
        'warning',
    ),
    'topup-single-signature': _spec(
        'TOPUP needs at least two distortion signatures but the estimation has one.', 'error'
    ),
    'drbuddi-only-infeasible': _spec(
        'A pooled DRBUDDI-only selection spans several blip groups; DRBUDDI corrects one.',
        'error',
    ),
    'drbuddi-refinement-multigroup': _spec(
        'The estimation spans several blip groups, so no DRBUDDI refinement is applied.',
        'warning',
    ),
    'drbuddi-refinement-not-useful': _spec(
        'DRBUDDI has no reverse-PE dMRI series to refine with; correction stays single-stage.',
        'warning',
    ),
    'drbuddi-no-opposing-pair': _spec(
        'DRBUDDI has no opposing blip for some series under this selection.', 'warning'
    ),
    'eddy-requires-shelled': _spec(
        'eddy requires shelled q-space sampling but a series is non-shelled.', 'error'
    ),
    'mixed-shelled-nonshelled': _spec(
        'Shelled and non-shelled series are mixed within one output.', 'warning'
    ),
}


def describe_issue(code: str) -> str:
    """The registered one-line meaning of ``code``, or the code itself if unknown.

    Unknown codes are tolerated (a consumer may emit its own), so rendering
    never fails on them; the conformance test keeps the package's own codes
    registered.
    """
    spec = ISSUE_CODES.get(code)
    return spec.description if spec is not None else code


def raise_for_errors(grouping: DWIGrouping):
    """Raise :class:`GroupingError` if any error-severity issue was collected."""
    errors = grouping.errors
    if errors:
        rendered = '\n'.join(issue.render() for issue in errors)
        raise GroupingError(
            f'The DWI grouping for sub-{grouping.subject_id} has '
            f'{len(errors)} unresolvable problem(s):\n{rendered}'
        )


def _pepolar_signature_count(grouping: DWIGrouping, estimation: FieldmapEstimation) -> int:
    """Number of distinct distortion signatures among an estimation's EPI sources."""
    signatures = set()
    for path in estimation.sources:
        record = grouping.files.get(path)
        if record is not None and record.is_epi_like and record.signature.pe_dir:
            signatures.add(record.signature.key)
    return len(signatures)


def blip_pair_polarities(grouping: DWIGrouping, estimation: FieldmapEstimation) -> dict:
    """Blip-pair identity ``(pe_axis, readout_time, shim)`` -> polarities present.

    DRBUDDI corrects one matched blip-up/blip-down pair at a time: two EPI-like
    sources that share this identity (same axis, readout time and shim) and carry
    opposite polarity. A group with both polarities is a complete pair; one
    polarity is unpaired. (TOPUP, by contrast, pools every group into a single
    estimation regardless of readout.)
    """
    polarities: dict[tuple, set] = defaultdict(set)
    for path in estimation.sources:
        record = grouping.files.get(path)
        if record is not None and record.is_epi_like and record.signature.pe_dir:
            sig = record.signature
            polarities[(sig.pe_axis, sig.readout_time, sig.shim)].add(sig.pe_polarity)
    return dict(polarities)


def blip_sort_key(key: tuple) -> tuple:
    """Deterministic ordering for blip-pair keys (readout/shim may be None)."""
    axis, readout, shim = key
    return (axis, float('-inf') if readout is None else readout, str(shim))


def describe_blip_group(key: tuple) -> str:
    """Human-readable label for a blip-pair identity, e.g. 'axis j, TRT 0.05s'."""
    axis, readout, _shim = key
    return f'axis {axis}' + (f', TRT {readout:g}s' if readout is not None else '')


def dwi_blip_pairs(grouping: DWIGrouping, estimation: FieldmapEstimation) -> list:
    """Blip-pair identities with reverse phase-encoded *dMRI series* in both
    polarities among the sources - the matched pairs DRBUDDI can *refine*.

    Unlike :func:`blip_pair_polarities` (which counts epi fieldmaps too), a lone
    reverse b=0 was already consumed by TOPUP and adds nothing more; only a
    reverse-PE dMRI pair carries new information. Keys on the full blip-pair
    identity (axis, readout time and shim), since DRBUDDI needs a readout match.
    Sorted.
    """
    polarities: dict[tuple, set] = defaultdict(set)
    for path in estimation.sources:
        record = grouping.files.get(path)
        if record is not None and record.is_dwi and record.signature.pe_dir:
            sig = record.signature
            polarities[(sig.pe_axis, sig.readout_time, sig.shim)].add(sig.pe_polarity)
    return sorted((key for key, pols in polarities.items() if len(pols) == 2), key=blip_sort_key)


def structural_target(grouping: DWIGrouping) -> tuple[str, list[str]] | None:
    """The structural image registration-based stages should use.

    Returns ``(kind, paths)`` where kind is ``'synb0'`` (a synthetic
    undistorted b=0 from the T1w - preferred when SyNb0 was requested, even
    over a real T2w, since its contrast matches the b=0 exactly) or ``'t2w'``;
    ``None`` when neither is available.
    """
    synb0, t2w = ANAT_REFERENCES['synb0'], ANAT_REFERENCES['t2w']
    t1ws = grouping.anat_files(synb0.source_suffix)
    if grouping.synb0_requested and t1ws:
        return synb0.structural_target, t1ws
    t2ws = grouping.anat_files(t2w.source_suffix)
    if t2ws:
        return t2w.structural_target, t2ws
    return None


#: Maximum b-values within one output may differ by this much before warning.
MAXB_TOLERANCE = 100.0


def check_data_compatibility(
    records: dict,
    correction_units: dict,
    concatenation_groups: dict,
    ignore_fov: bool = False,
) -> list[GroupingIssue]:
    """Backend-independent data checks on the grouped series.

    These read properties of the images themselves (b-values, NIfTI grids)
    and therefore run at grouping time, once, rather than per backend:

    - **Maximum b-value spread** (per final output): scanners often adjust
      acquisition parameters (TE, gradient timings) when the maximum b-value
      changes, so concatenating a b=1000 series with a b=3000 series
      deserves a warning.
    - **Field of view** (per correction unit - raw series are only ever
      stacked within a unit; final concatenation happens after resampling):
      a pure translation offset is fixable by overwriting affines (warning,
      with shim evidence); differing orientations break axis-aligned
      distortion correction (error, downgradable with ``ignore_fov``);
      differing matrix/voxel sizes cannot be stacked at all (error, not
      downgradable).

    Series whose b-values or headers could not be read are skipped.
    """
    issues: list[GroupingIssue] = []

    unit_scope = {
        unit_key: multipart_id
        for multipart_id, concat in concatenation_groups.items()
        for unit_key in concat.correction_units
    }

    for multipart_id, concat in sorted(concatenation_groups.items()):
        members = [records[path] for path in concat.dwi_files if path in records]

        # --- maximum b-value spread ---------------------------------------
        max_bvals = {
            record.filename: record.max_bval for record in members if record.max_bval is not None
        }
        if max_bvals and max(max_bvals.values()) - min(max_bvals.values()) > MAXB_TOLERANCE:
            described = ', '.join(
                f'{name}: b={int(val)}' for name, val in sorted(max_bvals.items())
            )
            issues.append(
                warning(
                    'maxb-mismatch',
                    f"Series concatenated in output '{concat.output_name}' have "
                    f'different maximum b-values ({described}). Many scanners '
                    'adjust acquisition parameters (TE, gradient timings) when '
                    'the maximum b-value changes, so these series may differ in '
                    'more than diffusion weighting. If they should be processed '
                    'separately, give them different MultipartID values.',
                    tuple(record.path for record in members),
                    scope=multipart_id,
                )
            )

    for unit_key, unit in sorted(correction_units.items()):
        multipart_id = unit_scope.get(unit_key)
        members = [records[path] for path in unit.dwi_files if path in records]

        # --- field of view -------------------------------------------------
        gridded = [record for record in members if record.grid is not None]
        if len(gridded) < 2:
            continue
        reference = gridded[0]
        worst = {'grid': [], 'oblique': [], 'shifted': []}
        max_shift = 0.0
        max_rotation = 0.0
        for record in gridded[1:]:
            relation = reference.grid.compare(record.grid)
            if relation == 'match':
                continue
            worst[relation].append(record)
            if relation == 'shifted':
                max_shift = max(max_shift, reference.grid.shift_mm(record.grid))
            if relation == 'oblique':
                max_rotation = max(max_rotation, reference.grid.rotation_deg(record.grid))

        involved = tuple(record.path for record in gridded)
        separate_advice = (
            'process them as separate outputs by giving them different '
            'MultipartID values or using --separate-all-dwis'
        )

        if worst['grid']:
            names = ', '.join(record.filename for record in [reference] + worst['grid'])
            issues.append(
                error(
                    'fov-grid-mismatch',
                    f"Series stacked in correction unit '{unit.key}' are "
                    f'sampled on different voxel grids ({names}: matrix size or '
                    'voxel size differs). They cannot be stacked volumewise. '
                    f'Either {separate_advice}, or resample them to a common '
                    'grid before running qsiprep.',
                    involved,
                    scope=multipart_id,
                )
            )
        elif worst['oblique']:
            make_issue = warning if ignore_fov else error
            proceed = (
                'Proceeding anyway because field-of-view checking is disabled: '
                'expect distortion corrections to be misapplied.'
                if ignore_fov
                else 'To proceed anyway, accepting misapplied corrections, '
                'disable field-of-view checking (ignore_fov).'
            )
            issues.append(
                make_issue(
                    'fov-oblique',
                    f"Series stacked in correction unit '{unit.key}' have "
                    f'differently-oriented fields of view (slice orientations '
                    f'differ by up to {max_rotation:.1f} degrees). Susceptibility '
                    'and eddy-current distortions act along the acquisition axes, '
                    'so corrections cannot be applied correctly to a naive '
                    f'concatenation. Either {separate_advice}. {proceed}',
                    involved,
                    scope=multipart_id,
                )
            )
        elif worst['shifted']:
            shims = {record.signature.shim for record in gridded}
            if None in shims or () in shims:
                shim_evidence = (
                    'No ShimSetting is recorded in the sidecars, so whether a '
                    're-shim occurred cannot be verified.'
                )
            elif len(shims) == 1:
                shim_evidence = (
                    'The recorded ShimSetting values match, so a re-shim does '
                    'not appear to have occurred and aligning them is safe.'
                )
            else:
                shim_evidence = (
                    'The recorded ShimSetting values differ, confirming a '
                    're-shim: these series do NOT share susceptibility '
                    'distortions.'
                )
            issues.append(
                warning(
                    'fov-shifted',
                    f"Series stacked in correction unit '{unit.key}' share "
                    f'a grid but their fields of view are offset by up to '
                    f'{max_shift:.1f} mm. The affines can be overwritten to align '
                    'them, but many scanners force a re-shim when the field of '
                    'view is moved, in which case the series no longer share '
                    f'susceptibility distortions. {shim_evidence} To keep them '
                    f'apart instead, {separate_advice}.',
                    involved,
                    scope=multipart_id,
                )
            )

    return issues
