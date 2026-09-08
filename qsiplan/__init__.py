"""BIDS-native grouping of DWI scans for preprocessing.

This package decides, per subject, from BIDS metadata alone:

1. Which DWI files share susceptibility distortions (distortion groups).
2. Which files jointly estimate each fieldmap (``B0FieldIdentifier``).
3. Which fieldmap corrects which DWI file (``B0FieldSource``).
4. Which files are concatenated in the outputs (``MultipartID``).

Curated sidecar metadata is used verbatim; everything the user did not curate
is inferred and tagged with its provenance. The output,
:class:`~.models.DWIGrouping`, describes the *data* - how any given
processing backend arranges that data is the business of adapters and the
previews in :mod:`~.report`.

Run ``qsiplan /path/to/bids`` to print the grouping and
per-selection processing previews for a dataset.

Adding a new estimation method
------------------------------
The (method x selection) behavior matrix is deliberately spelled out as prose
branches rather than a rules table, because the preview text is the product.
The cost is that a new :class:`~.models.CorrectionMethod` touches five places
(SYNB0 is the worked example in each):

1. :class:`~.models.CorrectionMethod` - add the member.
2. ``inference.py`` - produce it: ``_classify_method`` for curated sources
   and/or a step in ``resolve_fieldmapless``; rank it in ``_METHOD_RANK``.
3. ``methods.py`` - which :class:`~.methods.SdcTool` consumes it (the
   capability registries), and ``plan.py`` - its stage sequence per HMC
   method, and whether an infeasible selection is an error or a degradation.
4. ``report.py`` - ``_METHOD_LABELS``, ``MethodGroups``/``_ids_by_kind``,
   a narration branch in each ``_describe_*`` function, and its
   ``_stage_text`` sentence.
5. Tests - a scenario skeleton, inference assertions, plan-stage assertions,
   and regenerated golden reports (``QSIPREP_REGEN_GROUPING_REPORTS=1``).
"""

try:
    from ._version import __version__
except ImportError:  # pragma: no cover - not yet built from a git checkout
    __version__ = '0+unknown'

from .adapters import (
    PreprocUnit,
    assembly_to_sidecar,
    concatenation_scheme,
    to_preproc_units,
    unit_to_sidecar,
)
from .bids import parse_file_entities
from .catalog import Bids2TableCatalog, DatasetCatalog
from .inference import build_grouping
from .interactive import explorer_view, render_explorer_html, render_html, render_report_segment
from .metadata import index_subject
from .methods import (
    HMC_CAPABILITIES,
    SDC_CAPABILITIES,
    HmcMethod,
    MethodSelection,
    SdcTool,
    canonical_selection,
    combined_key,
    parse_combined_key,
    selection_for_config,
)
from .models import (
    ANAT_REFERENCE_CHOICES,
    ANAT_REFERENCES,
    AnatReference,
    ConcatenationGroup,
    CorrectionMethod,
    DistortionGroup,
    DistortionSignature,
    DWIGrouping,
    FieldmapEstimation,
    FileRecord,
    GroupingPolicy,
    Provenance,
)
from .plan import ExecutionPlan, OutputAssembly, PlanStage, ProcessingRun, compile_plan
from .report import describe_processing, full_report, report_text
from .validation import BACKENDS, GroupingError, GroupingIssue, check_backend, raise_for_errors

__all__ = [
    'ANAT_REFERENCE_CHOICES',
    'ANAT_REFERENCES',
    'AnatReference',
    'BACKENDS',
    'Bids2TableCatalog',
    'HMC_CAPABILITIES',
    'SDC_CAPABILITIES',
    'ConcatenationGroup',
    'DistortionGroup',
    'DistortionSignature',
    'DWIGrouping',
    'DatasetCatalog',
    'CorrectionMethod',
    'FieldmapEstimation',
    'FileRecord',
    'GroupingError',
    'GroupingIssue',
    'GroupingPolicy',
    'HmcMethod',
    'MethodSelection',
    'PreprocUnit',
    'Provenance',
    'SdcTool',
    'assembly_to_sidecar',
    'build_dwi_grouping',
    'canonical_selection',
    'check_backend',
    'combined_key',
    'compile_plan',
    'ExecutionPlan',
    'OutputAssembly',
    'PlanStage',
    'ProcessingRun',
    'concatenation_scheme',
    'describe_processing',
    'explorer_view',
    'full_report',
    'index_subject',
    'parse_combined_key',
    'render_explorer_html',
    'render_html',
    'render_report_segment',
    'report_text',
    'selection_for_config',
    'to_preproc_units',
    'unit_to_sidecar',
]


def build_dwi_grouping(
    layout,
    subject_data,
    separate_all_dwis=False,
    ignore_fieldmaps=False,
    ignore_pepolar_dwis=False,
    ignore_t2w=False,
    ignore_shims=False,
    ignore_fov=False,
    ignore_sdc=False,
    sdc_anat_reference='none',
    force_sdc_anat_reference=False,
    distortion_group_merge='concat',
    b0_threshold=None,
    strict=True,
):
    """Group one subject's DWI scans.

    Parameters
    ----------
    layout : :class:`bids.BIDSLayout`
        Layout of the input dataset; used only for sidecar metadata reads.
    subject_data : dict
        As returned by :func:`qsiprep.utils.bids.collect_data`: must contain
        a ``'dwi'`` key listing the subject's DWI files (``'fmap'``,
        ``'t1w'``, and ``'t2w'`` are optional - they are discovered from the
        layout when absent).
    separate_all_dwis : bool
        Every DWI series becomes its own output. Fieldmap estimation still
        happens at session scope, so single series keep their SDC.
    ignore_fieldmaps : bool
        Do not index ``fmap/``. The reverse phase-encoding DWI heuristic
        still applies.
    ignore_t2w : bool
        Do not index T2w images, so no T2w is available for T2Wreg
        fieldmap-less correction (``--ignore t2w``). T1w indexing is
        unaffected; a fieldmap-less series then falls back to SyNb0/SyN if
        requested, or is left uncorrected.
    ignore_pepolar_dwis : bool
        Never pair DWI series with each other to estimate a PEPOLAR fieldmap:
        drop every PEPOLAR estimation whose sources are all DWIs, inferred or
        curated. The series are still processed, just corrected by a fieldmap
        they are linked to, a fieldmap-less method, or not at all. An estimation
        that also uses a ``fmap/`` EPI (a DWI paired with a fieldmap, not with
        another DWI) is kept.
    ignore_shims : bool
        Treat all ShimSetting values as compatible. Use when data were
        re-shimmed but distortion correction across shims is wanted anyway.
    ignore_fov : bool
        Downgrade the differing-orientation field-of-view error to a warning
        and proceed, accepting misapplied distortion corrections. Grid-size
        mismatches remain errors: they cannot be stacked at all.
    ignore_sdc : bool
        Disable susceptibility distortion correction entirely: no fieldmaps, no
        reverse-PE heuristic, and no fieldmap-less fallback. Every series is
        left uncorrected but is still grouped and concatenated for HMC. Stronger
        than ``ignore_fieldmaps`` (which only skips ``fmap/``).
    sdc_anat_reference : str
        Which anatomical-derived source image may drive fieldmap-less SDC, as
        a FALLBACK for series that no fieldmap reaches: ``'synb0'`` (a
        synthetic b=0 from the T1w), ``'t2w'`` (the real T2w, TORTOISE
        T2Wreg), ``'invt1w'`` (the inverted-contrast T1w - the standalone
        niworkflows SyN-SDC prior), ``'auto'`` (resolved per subject: a T1w
        selects synb0, else a T2w selects t2w, else nothing with a warning;
        ``'invt1w'`` is unreachable via auto and must be requested
        explicitly), or ``'none'`` (default: no anatomical SDC ever).
        Explicit synb0/invt1w error if no T1w exists or a target series lacks
        PhaseEncodingDirection; explicit t2w errors if no T2w exists.
    force_sdc_anat_reference : bool
        Escalate the ``sdc_anat_reference`` method from fallback to OVERRIDE: it
        replaces the fieldmap application for every DWI series. An error when
        ``sdc_anat_reference`` is ``'none'``.
    distortion_group_merge : str or None
        How the corrected results of a final output's correction units are
        combined: ``'concat'`` (default) concatenates them, ``'average'``
        averages matched volumes (opposite-PE duplicate schemes), and
        ``'none'`` keeps every correction unit as its own output (``None``
        is treated as ``'concat'``).
    b0_threshold : float
        Diffusion-weighting at or below this is treated as b=0 when
        classifying sampling schemes (default:
        :data:`~.metadata.B0_THRESHOLD`).
    strict : bool
        Raise :class:`~.validation.GroupingError` if any error-severity issue
        is found. With ``strict=False`` the grouping is returned with its
        ``issues`` intact, which is what reports and previews want.

    Returns
    -------
    :class:`~.models.DWIGrouping`
    """
    records, index_issues = index_subject(
        layout,
        subject_data,
        ignore_fieldmaps=ignore_fieldmaps,
        ignore_t2w=ignore_t2w,
        b0_threshold=b0_threshold,
    )
    subject_id = parse_file_entities(records[0].path)['subject']
    grouping = build_grouping(
        records,
        subject_id=subject_id,
        separate_all_dwis=separate_all_dwis,
        ignore_fieldmaps=ignore_fieldmaps,
        ignore_pepolar_dwis=ignore_pepolar_dwis,
        ignore_t2w=ignore_t2w,
        ignore_shims=ignore_shims,
        ignore_fov=ignore_fov,
        ignore_sdc=ignore_sdc,
        sdc_anat_reference=sdc_anat_reference,
        force_sdc_anat_reference=force_sdc_anat_reference,
        distortion_group_merge=distortion_group_merge,
        extra_issues=index_issues,
    )
    if strict:
        raise_for_errors(grouping)
    return grouping
