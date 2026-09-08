"""Read everything the grouping needs from the input files, in one place.

This module is the only part of qsiprep's grouping that reads input data:
JSON sidecars, ``.bval`` files, and NIfTI headers. It converts each dwi/,
fmap/, and anat/ image into a :class:`~.models.FileRecord`, normalizing:

- ``B0FieldIdentifier`` / ``B0FieldSource``: string-or-list to tuple.
- ``IntendedFor``: relative-to-subject paths, ``bids::`` URIs, and (with a
  warning) absolute paths, all resolved to absolute paths. pybids'
  ``layout.get_fieldmap`` is deliberately not used - it only understands
  relative-path IntendedFor and cannot query B0Field* fields at all.
- ``ShimSetting``: list to tuple, so signatures are hashable.
- ``TotalReadoutTime``: rounded to :data:`~.models.READOUT_TOLERANCE` so
  float jitter between sidecars does not split groups.
- b-values: shelled/non-shelled classification and the maximum b-value.
- NIfTI headers: the sampling grid, for field-of-view checks.

Everything read from data (as opposed to metadata) degrades to "undetermined"
when a file is unreadable - test skeletons and docs builds use zero-byte
placeholders - and the downstream checks skip undetermined values.
"""

from __future__ import annotations

import io
import json
import os.path as op
import re
from pathlib import Path

import numpy as np

from .bids import BIDSInheritanceIndex, find_bval, parse_file_entities
from .models import READOUT_TOLERANCE, DistortionSignature, FileRecord, GridInfo
from .validation import GroupingIssue, error, warning

#: fmap/ suffixes that can participate in fieldmap estimation.
FMAP_SUFFIXES = (
    'epi',
    'fieldmap',
    'phasediff',
    'phase1',
    'phase2',
    'magnitude',
    'magnitude1',
    'magnitude2',
)

#: Default b=0 threshold (matches qsiprep's ``--b0-threshold`` default).
#: Diffusion-weighting at or below this is treated as b=0. Callers with a
#: configured threshold pass it explicitly.
B0_THRESHOLD = 100.0
_UNSET = object()


def unique_bvals(bvals, tol: float = 20.0):
    """Cluster b-values within ``tol`` of each other, greedily over sorted values.

    Mirrors dipy's ``unique_bvals_tolerance`` (its only use here) so the
    grouping does not need dipy: each sorted unique value starts a new
    cluster when it exceeds the previous representative by more than ``tol``.
    """
    values = np.unique(np.asarray(bvals, dtype=float).reshape(-1))
    representatives = [values[0]]
    for value in values[1:]:
        if value - representatives[-1] > tol:
            representatives.append(value)
    return np.asarray(representatives)


def read_bvals_bvecs(bval_file: str, bvec_file: str):
    """``(bvals, bvecs)`` arrays from sidecar files, FSL row-form transposed.

    Mirrors the dipy reader this replaced: bvals flatten to ``(N,)`` and the
    3xN bvec table FSL writes becomes ``(N, 3)``. The already-transposed Nx3
    form is accepted too, except that a 3x3 table is read the way FSL wrote
    it, as three rows of three volumes.

    Anything that leaves the gradients unusable - a path that is ``None``
    (no applicable sidecar), a missing or empty file, unparsable text, or
    two files that disagree on the volume count - raises ``ValueError``;
    callers treat unreadable gradients as absent.
    """
    bvals = _load_gradient_table(bval_file, 'bval').reshape(-1)
    bvecs = _load_gradient_table(bvec_file, 'bvec')
    if bvecs.shape[0] == 3 and bvecs.shape[1] == bvals.size:
        bvecs = bvecs.T
    if bvecs.shape != (bvals.size, 3):
        raise ValueError(f'{bvec_file} has shape {bvecs.shape}, expected ({bvals.size}, 3)')
    return bvals, bvecs


def _load_gradient_table(path: str | None, label: str):
    """One ``.bval``/``.bvec`` file as a float array, or ``ValueError``.

    ``np.loadtxt`` reports an empty file with a warning and an empty array
    rather than an error, and the zero-byte placeholders in test skeletons
    and docs builds hit that path constantly, so emptiness is checked here.
    """
    if path is None:
        raise ValueError(f'No {label} file applies to this image.')
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise ValueError(f'{path} could not be read: {exc}') from exc
    if not text.strip():
        raise ValueError(f'{path} is empty.')
    try:
        return np.loadtxt(io.StringIO(text), ndmin=2)
    except ValueError as exc:
        raise ValueError(f'{path} could not be parsed as a gradient table: {exc}') from exc


def evaluate_shells(
    bvals,
    b0_threshold: float | None = None,
    tol: float = 100.0,
    min_shell_dirs: int = 6,
    max_shells: int = 7,
) -> tuple[bool | None, tuple[float, ...]]:
    """Classify one series' b-values as shelled or non-shelled sampling.

    Returns ``(shelled, shell_centres)``. Two conditions must both hold for a
    shelled classification (adapted from the retired
    ``_side_is_shelled`` detector in ``workflows/dwi/diffprep.py``):

    1. **Grid guard** - the non-b=0 values cluster into at most ``max_shells``
       distinct shells. A CS-DSI q-space grid fragments into many clusters
       (real HASC55 has ~18 per phase-encoding direction), while DTI has 1 and
       multi-shell HARDI a handful. This is the decisive test: a grid can
       still pack ``min_shell_dirs`` samples near some radius, so a population
       count alone misclassifies it.
    2. **A populous shell** - at least one cluster holds ``min_shell_dirs``
       or more volumes. Unlike the retired DRBUDDI detector, no upper b-value
       limit applies: a single-shell b=2000 acquisition is shelled for eddy's
       purposes even though it is not tensor-fittable at low b.

    ``shelled`` is ``None`` (undetermined) when there are no diffusion-weighted
    volumes to classify.
    """
    if b0_threshold is None:
        b0_threshold = B0_THRESHOLD
    non_b0 = np.asarray(bvals, dtype=float).reshape(-1)
    non_b0 = non_b0[non_b0 >= b0_threshold]
    if non_b0.size == 0:
        return None, ()
    centres = unique_bvals(non_b0, tol=tol)
    shell_centres = tuple(round(float(centre)) for centre in centres)
    if len(centres) > max_shells:
        return False, shell_centres
    shelled = any(
        int(np.sum(np.abs(non_b0 - centre) <= tol)) >= min_shell_dirs for centre in centres
    )
    return shelled, shell_centres


def _read_gradients(
    nii_file: str, b0_threshold: float | None = None, bval_file=_UNSET
) -> tuple[bool | None, tuple[float, ...], float | None]:
    """(shelled, shell centres, max b-value) from a DWI's sibling .bval file.

    Missing or unreadable b-values (docs builds, test skeletons) leave
    everything undetermined rather than guessing.
    """
    if bval_file is _UNSET:
        bval_file = find_bval(nii_file)
    try:
        bvals = np.loadtxt(bval_file).reshape(-1)
    except (OSError, ValueError):
        return None, (), None
    if bvals.size == 0:
        return None, (), None
    shelled, shells = evaluate_shells(bvals, b0_threshold=b0_threshold)
    return shelled, shells, float(np.max(bvals))


def _read_grid(nii_file: str) -> GridInfo | None:
    """The sampling grid of a NIfTI file, or None when unreadable.

    Test skeletons and docs builds use zero-byte placeholder files; those
    leave the grid undetermined and the field-of-view checks skip.
    """
    import nibabel as nb

    try:
        img = nb.load(nii_file)
        shape = tuple(int(dim) for dim in img.shape[:3])
        zooms = tuple(round(float(zoom), 3) for zoom in img.header.get_zooms()[:3])
        affine = tuple(tuple(float(val) for val in row) for row in np.asarray(img.affine))
    except Exception:  # noqa: BLE001 - nibabel raises a menagerie on bad files
        return None
    return GridInfo(shape=shape, zooms=zooms, affine=affine)


_BIDS_URI = re.compile(r'^bids:[^:]*:(?P<relpath>.+)$')


def _normalize_to_tuple(value) -> tuple[str, ...]:
    """B0FieldIdentifier/B0FieldSource/IntendedFor may be a string or a list."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def build_signature(metadata: dict) -> DistortionSignature:
    """Extract the distortion-relevant parameters from a merged sidecar."""
    readout_time = metadata.get('TotalReadoutTime')
    if readout_time is not None:
        readout_time = round(float(readout_time) / READOUT_TOLERANCE) * READOUT_TOLERANCE
    shim = metadata.get('ShimSetting')
    if shim is not None:
        shim = tuple(shim)
    return DistortionSignature(
        pe_dir=metadata.get('PhaseEncodingDirection'),
        readout_time=readout_time,
        shim=shim,
        parallel_factor=metadata.get('ParallelReductionFactorInPlane'),
        multiband_factor=metadata.get('MultibandAccelerationFactor'),
    )


def resolve_intended_for(
    entries: tuple[str, ...],
    fmap_path: str,
    bids_root: str,
    subject_id: str,
    known_dwi_files: set[str],
    issues: list[GroupingIssue],
    companion_primary: dict[str, str] | None = None,
) -> tuple[str, ...]:
    """Resolve IntendedFor entries to absolute paths of this subject's DWIs.

    Entries that resolve to non-DWI targets (e.g. BOLD runs) are legitimate
    uses of a shared fieldmap and are skipped silently. Entries that resolve
    to nothing on disk produce a warning - they are usually curation typos.
    An entry naming the phase image of a complex-valued acquisition means its
    magnitude (``companion_primary`` maps phase path -> magnitude path).
    """
    resolved = []
    for entry in entries:
        match = _BIDS_URI.match(entry)
        if match:
            candidate = op.join(bids_root, match.group('relpath'))
        elif op.isabs(entry):
            issues.append(
                warning(
                    'intendedfor-absolute-path',
                    f'IntendedFor entry "{entry}" in {op.basename(fmap_path)} is an '
                    'absolute path, which is not portable BIDS. Using it as-is.',
                    (fmap_path,),
                )
            )
            candidate = entry
        else:
            # The BIDS-legal relative form: relative to the subject directory
            candidate = op.join(bids_root, f'sub-{subject_id}', entry)

        candidate = op.abspath(candidate)
        if companion_primary:
            # A phase image names the same acquisition as its magnitude.
            candidate = companion_primary.get(candidate, candidate)
        if candidate in known_dwi_files:
            # Both parts of a complex pair name one magnitude: list it once.
            if candidate not in resolved:
                resolved.append(candidate)
        elif not op.exists(candidate):
            issues.append(
                warning(
                    'intendedfor-missing-target',
                    f'IntendedFor entry "{entry}" in {op.basename(fmap_path)} does not '
                    'match any file in the dataset. Check the path for typos.',
                    (fmap_path,),
                )
            )
        # else: exists but is not one of this subject's DWIs (e.g. a BOLD
        # target of a shared fieldmap) - fine, just not our concern.
    return tuple(resolved)


def _load_metadata(path, inheritance, issues, cache, invalid_sidecars):
    """Load inherited JSON strictly, retaining curation errors as issues."""
    try:
        sidecars = inheritance.find(path, '.json')
    except ValueError as exc:
        issues.append(error('ambiguous-json-inheritance', str(exc), (path,)))
        return {}

    metadata = {}
    for sidecar in sidecars:
        if sidecar in invalid_sidecars:
            continue
        if sidecar in cache:
            metadata.update(cache[sidecar])
            continue
        try:
            value = json.loads(sidecar.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            invalid_sidecars.add(sidecar)
            issues.append(
                error(
                    'invalid-json-sidecar',
                    f'{sidecar} could not be read as JSON: {exc}',
                    (str(sidecar), path),
                )
            )
            continue
        if not isinstance(value, dict):
            invalid_sidecars.add(sidecar)
            issues.append(
                error(
                    'invalid-json-sidecar',
                    f'{sidecar} must contain a JSON object, not {type(value).__name__}.',
                    (str(sidecar), path),
                )
            )
            continue
        cache[sidecar] = value
        metadata.update(value)
    return metadata


def _record_from_file(
    path: str,
    inheritance,
    bids_root: str,
    subject_id: str,
    known_dwi_files: set[str],
    issues: list[GroupingIssue],
    metadata_cache,
    invalid_sidecars,
    b0_threshold: float | None = None,
    companion_of: dict[str, str] | None = None,
    primary_of: dict[str, str] | None = None,
) -> FileRecord:
    path = op.abspath(path)
    metadata = _load_metadata(path, inheritance, issues, metadata_cache, invalid_sidecars)
    entities = parse_file_entities(path)
    datatype = entities.get('datatype') or ('dwi' if path in known_dwi_files else 'fmap')

    intended_for = ()
    if datatype == 'fmap':
        intended_for = resolve_intended_for(
            _normalize_to_tuple(metadata.get('IntendedFor')),
            fmap_path=path,
            bids_root=bids_root,
            subject_id=subject_id,
            known_dwi_files=known_dwi_files,
            issues=issues,
            companion_primary=primary_of,
        )

    shelled, shells, max_bval, grid = (None, (), None, None)
    bval_file = None
    bvec_file = None
    if datatype == 'dwi':
        try:
            bvals = inheritance.find(path, '.bval')
            bvecs = inheritance.find(path, '.bvec')
        except ValueError as exc:
            issues.append(error('ambiguous-gradient-inheritance', str(exc), (path,)))
            bvals, bvecs = (), ()
        bval_file = str(bvals[-1]) if bvals else None
        bvec_file = str(bvecs[-1]) if bvecs else None
        shelled, shells, max_bval = _read_gradients(
            path, b0_threshold=b0_threshold, bval_file=bval_file
        )
        grid = _read_grid(path)

    return FileRecord(
        path=path,
        datatype=datatype,
        suffix=entities.get('suffix', ''),
        session=entities.get('session'),
        signature=build_signature(metadata),
        b0field_identifiers=_normalize_to_tuple(metadata.get('B0FieldIdentifier')),
        b0field_sources=_normalize_to_tuple(metadata.get('B0FieldSource')),
        multipart_id=_normalize_to_tuple(metadata.get('MultipartID')),
        intended_for=intended_for,
        metadata=metadata,
        shelled=shelled,
        shells=shells,
        max_bval=max_bval,
        grid=grid,
        bval_file=bval_file,
        bvec_file=bvec_file,
        part=entities.get('part'),
        phase_path=(companion_of or {}).get(path),
    )


def _collect_datatype_files(layout, subject_data, subject_id, key, datatype, suffixes):
    """Files of one datatype, from subject_data when present, else the layout."""
    if subject_data.get(key) is not None:
        files = [op.abspath(path) for path in subject_data[key]]
    else:
        files = layout.get(
            return_type='file',
            subject=subject_id,
            datatype=datatype,
            extension=['.nii', '.nii.gz'],
        )
        files = [op.abspath(path) for path in files]
    return [path for path in files if parse_file_entities(path).get('suffix') in suffixes]


def _split_parts(paths, issues: list[GroupingIssue]) -> tuple[list[str], dict[str, str]]:
    """Split complex-valued acquisitions into primaries and phase companions.

    Files that differ only in their BIDS ``part-`` entity are one acquisition.
    The magnitude (``part-mag``, or no ``part`` at all) is the **primary**: the
    one file the grouping indexes. A ``part-phase`` sibling is its
    **companion**, carried on the primary's record and never indexed itself, so
    no grouping tier can ever count it as a series. Real/imaginary (or any
    other) parts are not consumed and are dropped with a warning, as is a
    phase image with no magnitude to accompany.

    Returns ``(primaries, companion_of)``: the primaries in input order, and
    ``{primary: phase_path}`` for the primaries that have a companion.
    """
    # (entities minus part) -> {part label: path}
    acquisitions: dict[tuple, dict[str | None, str]] = {}
    for path in paths:
        entities = parse_file_entities(path)
        key = tuple(sorted((k, v) for k, v in entities.items() if k not in ('part', 'extension')))
        acquisitions.setdefault(key, {})[entities.get('part')] = path

    keep: set[str] = set()
    companion_of: dict[str, str] = {}
    for members in acquisitions.values():
        # ``part-mag`` and a part-less image are both magnitudes; a phase
        # companion attaches to the explicitly labelled one when both exist.
        primaries = [members[part] for part in (None, 'mag') if part in members]
        keep.update(primaries)
        phase = members.get('phase')
        if phase is not None:
            if primaries:
                companion_of[primaries[-1]] = phase
            else:
                issues.append(
                    warning(
                        'phase-without-magnitude',
                        f'{op.basename(phase)} is a phase image with no magnitude '
                        '(part-mag) sibling to accompany, so it is ignored.',
                        (phase,),
                    )
                )
        for part, path in members.items():
            if part in (None, 'mag', 'phase'):
                continue
            issues.append(
                warning(
                    'complex-part-unsupported',
                    f'{op.basename(path)} is a part-{part} image of a complex-valued '
                    'acquisition. QSIPrep uses the magnitude (and its phase) only, '
                    'so it is ignored.',
                    (path,),
                )
            )
    return [path for path in paths if path in keep], companion_of


def index_subject(
    layout,
    subject_data: dict,
    ignore_fieldmaps: bool = False,
    ignore_t2w: bool = False,
    b0_threshold: float | None = None,
) -> tuple[list[FileRecord], list[GroupingIssue]]:
    """Build a :class:`~.models.FileRecord` for every relevant image.

    Indexes the subject's dwi/ series, fmap/ files, and the anatomical images
    that can drive fieldmap-less correction (T1w for SyNb0, T2w for T2Wreg).
    Whether anatomical processing actually runs (``--anat-modality none``) is
    a workflow concern applied downstream, not here.

    A complex-valued acquisition (``part-mag`` + ``part-phase``) is indexed
    once, as its magnitude; the phase image is carried on that record's
    ``phase_path`` and never becomes a series of its own. Real/imaginary
    parts, and a phase with no magnitude, are ignored with a warning.

    Parameters
    ----------
    layout : dataset catalog or layout-like object
        Supplies the dataset root and discovers fmap/anat files when
        ``subject_data`` lacks them. QSIPlan resolves sidecars itself in one
        strict BIDS-inheritance pass.
    subject_data : dict
        As produced by :func:`qsiprep.utils.bids.collect_data`: at minimum a
        ``'dwi'`` key listing this subject's DWI files; optional ``'fmap'``,
        ``'t1w'``, and ``'t2w'`` keys.
    ignore_fieldmaps : bool
        Skip fmap/ indexing entirely (``--ignore fieldmaps``). The DWI-based
        PEPOLAR heuristic still applies downstream.
    ignore_t2w : bool
        Skip T2w indexing entirely (``--ignore t2w``), so no T2w is available
        for T2Wreg fieldmap-less correction. T1w indexing is unaffected.
    """
    issues: list[GroupingIssue] = []
    dwi_files = [op.abspath(path) for path in subject_data.get('dwi', [])]
    if not dwi_files:
        raise ValueError('subject_data contains no DWI files to group.')
    # Complex-valued data: only magnitudes are indexed; a phase image rides on
    # its magnitude's record as a companion (see ``_split_parts``).
    dwi_files, companion_of = _split_parts(dwi_files, issues)
    if not dwi_files:
        raise ValueError('subject_data contains no magnitude DWI files to group.')

    known_dwi_files = set(dwi_files)
    bids_root = str(layout.root)
    subject_id = parse_file_entities(dwi_files[0])['subject']

    fmap_files = []
    if not ignore_fieldmaps:
        fmap_files = _collect_datatype_files(
            layout, subject_data, subject_id, 'fmap', 'fmap', FMAP_SUFFIXES
        )
        # A complex-valued epi fieldmap splits the same way, so a phase epi can
        # never enter a PEPOLAR estimation's sources.
        fmap_files, fmap_companions = _split_parts(fmap_files, issues)
        companion_of = {**companion_of, **fmap_companions}

    t2w_files = (
        []
        if ignore_t2w
        else _collect_datatype_files(layout, subject_data, subject_id, 't2w', 'anat', ('T2w',))
    )
    anat_files = sorted(
        _collect_datatype_files(layout, subject_data, subject_id, 't1w', 'anat', ('T1w',))
        + t2w_files
    )

    all_files = sorted(known_dwi_files) + sorted(fmap_files) + anat_files
    primary_of = {phase: primary for primary, phase in companion_of.items()}
    inheritance = BIDSInheritanceIndex(all_files, root=bids_root)
    metadata_cache = {}
    invalid_sidecars = set()
    records = [
        _record_from_file(
            path,
            inheritance,
            bids_root,
            subject_id,
            known_dwi_files,
            issues,
            metadata_cache,
            invalid_sidecars,
            b0_threshold=b0_threshold,
            companion_of=companion_of,
            primary_of=primary_of,
        )
        for path in all_files
    ]
    return records, issues
