"""The two method axes a qsiprep pipeline is selected along.

Head-motion correction (HMC) and susceptibility distortion correction (SDC)
are independent choices: which software corrects motion, and which tool
implements each unit's correction method. The legacy ``'fsl'``/``'tortoise'``/
``'mixed'`` backend strings conflate the two - most visibly for SHORELine,
which shares TORTOISE's DRBUDDI feasibility without ever running DIFFPREP.

:class:`MethodSelection` names the axes separately, and the capability
registries record the per-tool facts that feasibility checks and the plan
compiler consult. Adding a new HMC or SDC tool is a registry entry plus a
workflow builder, not a semantics rewrite.
"""

from __future__ import annotations

import dataclasses
import enum

from .models import ANAT_REFERENCE_CHOICES, CorrectionMethod, GroupingPolicy


class HmcMethod(enum.StrEnum):
    """Which software corrects head motion (and eddy currents).

    Values are the names users can cite: eddy, TOPUP and DRBUDDI have their
    own papers, while TORTOISE's motion/eddy correction (the DIFFPREP
    program) is only described in the TORTOISE papers - so it is selected as
    ``tortoise``, with DIFFPREP named in the prose where precision helps.
    """

    EDDY = 'eddy'
    SHORELINE = 'shoreline'
    TORTOISE = 'tortoise'


class SdcTool(enum.StrEnum):
    """Which software implements a susceptibility distortion correction."""

    TOPUP = 'topup'
    DRBUDDI = 'drbuddi'
    FIELDMAP = 'fieldmap'  # the vendored GRE/phase-difference unwarp path
    T2WREG = 't2wreg'  # TORTOISE registration of b=0 images to a T2w
    SYN = 'syn'  # ANTs SyN registration to a template (classic fieldmap-less)


#: The SHORELine signal models (the legacy ``--hmc-model`` values other than
#: ``eddy``/``tortoise``, lowercased). ``none`` skips model-based target
#: prediction but still runs the SHORELine workflow.
SHORELINE_MODELS = ('3dshore', 'tensor', 'none')


@dataclasses.dataclass(frozen=True)
class HmcCapabilities:
    """Per-HMC-method facts consulted by feasibility checks and the compiler."""

    label: str
    #: eddy's Gaussian-process predictor needs shelled data.
    requires_shelled: bool
    #: The orientation the tool needs its inputs reoriented to.
    native_orientation: str
    #: A PEPOLAR tool whose field estimate this method consumes *during* HMC
    #: (estimation precedes motion correction), or None when SDC runs as its
    #: own stage after HMC.
    integrated_pepolar: SdcTool | None
    #: Whether PEPOLAR units split into one run per opposed blip pair
    #: (the DRBUDDI-per-pair routing).
    decomposes_pepolar_pairs: bool
    #: Whether the method itself can register to a T2w for fieldmap-less SDC.
    consumes_t2w_target: bool
    #: The PEPOLAR tools this method can drive.
    pepolar_tools: frozenset[SdcTool]


@dataclasses.dataclass(frozen=True)
class SdcCapabilities:
    """Per-SDC-tool facts consulted by feasibility checks and the compiler."""

    label: str
    #: The correction methods this tool can implement.
    consumes: frozenset[CorrectionMethod]
    #: Whether the correction derives from measured signal (vs registration).
    measured: bool
    #: Whether estimation needs >= 2 opposed phase-encoding signatures
    #: (TOPUP's single-signature failure mode).
    needs_opposed_signatures: bool
    #: Whether one invocation handles exactly one opposed blip pair.
    single_blip_pair_only: bool
    #: Whether the tool registers to an anatomical target.
    needs_structural_target: bool
    #: Whether the tool can refine an already-corrected series (second stage).
    refinement_capable: bool


HMC_CAPABILITIES: dict[HmcMethod, HmcCapabilities] = {
    HmcMethod.EDDY: HmcCapabilities(
        label='eddy',
        requires_shelled=True,
        native_orientation='LAS',
        integrated_pepolar=SdcTool.TOPUP,
        decomposes_pepolar_pairs=False,
        consumes_t2w_target=False,
        pepolar_tools=frozenset({SdcTool.TOPUP, SdcTool.DRBUDDI}),
    ),
    HmcMethod.SHORELINE: HmcCapabilities(
        label='SHORELine',
        requires_shelled=False,
        native_orientation='LPS',
        integrated_pepolar=None,
        decomposes_pepolar_pairs=True,
        consumes_t2w_target=False,
        pepolar_tools=frozenset({SdcTool.DRBUDDI}),
    ),
    HmcMethod.TORTOISE: HmcCapabilities(
        label='TORTOISE',
        requires_shelled=False,
        native_orientation='LPS',
        integrated_pepolar=None,
        decomposes_pepolar_pairs=True,
        consumes_t2w_target=True,
        pepolar_tools=frozenset({SdcTool.DRBUDDI}),
    ),
}

# SYNB0 appears under both PEPOLAR tools: it is pepolar-by-synthesis - the
# synthetic undistorted b=0 stands in as the opposing blip, and the selected
# PEPOLAR tool consumes it.
SDC_CAPABILITIES: dict[SdcTool, SdcCapabilities] = {
    SdcTool.TOPUP: SdcCapabilities(
        label='TOPUP',
        consumes=frozenset({CorrectionMethod.PEPOLAR, CorrectionMethod.SYNB0}),
        measured=True,
        needs_opposed_signatures=True,
        single_blip_pair_only=False,
        needs_structural_target=False,
        refinement_capable=False,
    ),
    SdcTool.DRBUDDI: SdcCapabilities(
        label='DRBUDDI',
        consumes=frozenset({CorrectionMethod.PEPOLAR, CorrectionMethod.SYNB0}),
        measured=True,
        needs_opposed_signatures=False,
        single_blip_pair_only=True,
        needs_structural_target=False,
        refinement_capable=True,
    ),
    SdcTool.FIELDMAP: SdcCapabilities(
        label='GRE fieldmap',
        consumes=frozenset(
            {
                CorrectionMethod.DIRECT,
                CorrectionMethod.PHASEDIFF,
                CorrectionMethod.PHASES,
            }
        ),
        measured=True,
        needs_opposed_signatures=False,
        single_blip_pair_only=False,
        needs_structural_target=False,
        refinement_capable=False,
    ),
    SdcTool.T2WREG: SdcCapabilities(
        label='T2Wreg',
        consumes=frozenset({CorrectionMethod.T2WREG}),
        measured=False,
        needs_opposed_signatures=False,
        single_blip_pair_only=False,
        needs_structural_target=True,
        refinement_capable=True,
    ),
    SdcTool.SYN: SdcCapabilities(
        label='SyN',
        consumes=frozenset({CorrectionMethod.NIPREPS_SYN}),
        measured=False,
        needs_opposed_signatures=False,
        single_blip_pair_only=False,
        needs_structural_target=True,
        refinement_capable=False,
    ),
}

_HMC_ALIASES = {
    'eddy': HmcMethod.EDDY,
    'shoreline': HmcMethod.SHORELINE,
    'tortoise': HmcMethod.TORTOISE,
    '3dshore': HmcMethod.SHORELINE,
    'tensor': HmcMethod.SHORELINE,
    'none': HmcMethod.SHORELINE,
}

_LEGACY_HMC_MODEL = {'3dshore': '3dSHORE', 'tensor': 'tensor', 'none': 'none'}


@dataclasses.dataclass(frozen=True)
class MethodSelection:
    """The user's pipeline choice: one HMC method plus SDC tool preferences.

    ``pepolar_tools`` is the ordered tool chain for PEPOLAR (and SYNB0)
    corrections - ``(TOPUP,)``, ``(DRBUDDI,)``, or ``(TOPUP, DRBUDDI)`` for
    TOPUP with DRBUDDI refinement. Data-driven methods (GRE fieldmaps, SyN,
    T2Wreg) are chosen per unit by the grouping, not here; the fieldmap-less
    flags that enable them live on :class:`~.models.GroupingPolicy` - they
    change the grouping itself, so they are policy, not selection.
    """

    hmc: HmcMethod
    pepolar_tools: tuple[SdcTool, ...]
    shoreline_model: str | None = None

    def __post_init__(self):
        if (self.shoreline_model is not None) != (self.hmc is HmcMethod.SHORELINE):
            raise ValueError(
                'shoreline_model is required with SHORELine and invalid otherwise; '
                f'got hmc={self.hmc.value!r}, shoreline_model={self.shoreline_model!r}'
            )
        if self.shoreline_model is not None and self.shoreline_model not in SHORELINE_MODELS:
            raise ValueError(
                f'Unknown SHORELine model {self.shoreline_model!r}; '
                f'expected one of {SHORELINE_MODELS}'
            )
        if len(set(self.pepolar_tools)) != len(self.pepolar_tools):
            raise ValueError(f'Duplicate PEPOLAR tools: {self.pepolar_tools}')
        for tool in self.pepolar_tools:
            if CorrectionMethod.PEPOLAR not in SDC_CAPABILITIES[tool].consumes:
                raise ValueError(f'{tool.value!r} is not a PEPOLAR tool')

    @property
    def legacy_backend(self) -> str:
        """The :data:`~.validation.BACKENDS` name this selection previews as."""
        if self.hmc is HmcMethod.EDDY:
            return 'mixed' if SdcTool.DRBUDDI in self.pepolar_tools else 'fsl'
        return 'tortoise'

    @property
    def legacy_hmc_model(self) -> str:
        """The legacy ``--hmc-model`` value equivalent to this selection."""
        if self.hmc is HmcMethod.EDDY:
            return 'eddy'
        if self.hmc is HmcMethod.TORTOISE:
            return 'tortoise'
        return _LEGACY_HMC_MODEL[self.shoreline_model]

    @property
    def legacy_pepolar_method(self) -> str:
        """The legacy ``--pepolar-method`` value equivalent to this selection."""
        return '+'.join(SDC_CAPABILITIES[tool].label for tool in self.pepolar_tools)

    def label(self) -> str:
        """Display name, e.g. ``'eddy + TOPUP→DRBUDDI'``."""
        tools = '→'.join(SDC_CAPABILITIES[tool].label for tool in self.pepolar_tools)
        return f'{HMC_CAPABILITIES[self.hmc].label} + {tools}'

    def cli_phrase(self) -> str:
        """The qsiprep flags that select this, e.g. ``'--hmc-method eddy --sdc-method topup'``."""
        parts = [f'--hmc-method {self.hmc.value}']
        if self.hmc is HmcMethod.SHORELINE and self.shoreline_model != '3dshore':
            parts.append(f'--shoreline-model {self.shoreline_model}')
        parts.append(f'--sdc-method {"+".join(tool.value for tool in self.pepolar_tools)}')
        return ' '.join(parts)

    def key_parts(self) -> list[str]:
        """The ``name=value`` parts of this selection's canonical key."""
        parts = [f'hmc-method={self.hmc.value}']
        if self.hmc is HmcMethod.SHORELINE:
            parts.append(f'shoreline-model={self.shoreline_model}')
        parts.append(f'sdc-method={"+".join(tool.value for tool in self.pepolar_tools)}')
        return parts

    def combination_key(self) -> str:
        """The canonical serialization of this flag combination.

        The single spelling shared by every plan provider: it keys the
        embedded payload index in the interactive page today and is the
        query string a live ``/plan`` endpoint would take tomorrow.
        Parts are sorted so any producer spells a combination one way.
        """
        return '&'.join(sorted(self.key_parts()))


def combined_key(policy, selection: MethodSelection) -> str:
    """The canonical key for one (grouping policy, method selection) pair.

    The flat address of a full flag combination: sorted, non-default-only
    ``name=value`` parts from both axes joined with ``&`` - the embedded
    explorer index's key today, a live ``/plan`` endpoint's query string
    tomorrow. ``policy`` is a :class:`~.models.GroupingPolicy`.
    """
    return '&'.join(sorted(policy.key_parts() + selection.key_parts()))


def parse_combined_key(key: str):
    """Invert :func:`combined_key`: the ``(policy, selection)`` a key names.

    The live endpoint's request parser. Accepts the parts in any order (the
    canonical spelling is sorted, but a query string should not have to be)
    and omits behave as defaults, exactly like the CLI. Unknown names or
    malformed values raise :class:`ValueError` - the server's 400.
    """
    policy_fields = {
        field.name.replace('_', '-'): field for field in dataclasses.fields(GroupingPolicy)
    }

    policy_kwargs = {}
    hmc = sdc = model = None
    for part in filter(None, key.split('&')):
        name, eq, value = part.partition('=')
        if not eq or not value:
            raise ValueError(f'Malformed key part: {part!r}')
        if name == 'hmc-method':
            hmc = value
        elif name == 'shoreline-model':
            model = value
        elif name == 'sdc-method':
            sdc = value
        elif name in policy_fields:
            field = policy_fields[name]
            if field.default is False:
                if value != '1':
                    raise ValueError(f'{name} takes only the value 1, not {value!r}')
                policy_kwargs[field.name] = True
            else:
                if name == 'distortion-group-merge' and value not in ('concat', 'average', 'none'):
                    raise ValueError(f'Unknown {name} value: {value!r}')
                if name == 'sdc-anat-reference' and value not in ANAT_REFERENCE_CHOICES:
                    raise ValueError(f'Unknown {name} value: {value!r}')
                policy_kwargs[field.name] = value
        else:
            raise ValueError(f'Unknown key part: {part!r}')

    if hmc is None or sdc is None:
        raise ValueError('A combined key must include hmc-method and sdc-method.')
    if model is not None and hmc != 'shoreline':
        raise ValueError(f'shoreline-model is invalid with hmc-method={hmc}.')
    selection = selection_for_config(model or hmc, sdc)
    return GroupingPolicy(**policy_kwargs), selection


def selection_for_config(
    hmc_model: str,
    pepolar_method: str | None,
) -> MethodSelection:
    """Build a :class:`MethodSelection` from CLI/config vocabulary.

    Accepts both the legacy values (``--hmc-model`` ``eddy``/``tortoise``/
    ``3dSHORE``/``tensor``/``none``; ``--pepolar-method`` ``TOPUP``/
    ``DRBUDDI``/``TOPUP+DRBUDDI``) and the axis values (``shoreline``/
    lowercase tools; ``auto``). ``auto`` (or ``None``) resolves
    to the HMC method's preferred PEPOLAR tool. Mismatched combinations
    (e.g. ``tortoise`` with an explicit ``topup``) are accepted here;
    feasibility is the compiler's job.
    """
    hmc_key = (hmc_model or '').lower()
    if hmc_key not in _HMC_ALIASES:
        raise ValueError(f'Unknown hmc method: {hmc_model!r}')
    hmc = _HMC_ALIASES[hmc_key]

    shoreline_model = None
    if hmc is HmcMethod.SHORELINE:
        shoreline_model = hmc_key if hmc_key in SHORELINE_MODELS else '3dshore'

    pepolar_key = (pepolar_method or 'auto').lower()
    if pepolar_key == 'auto':
        pepolar_tools = (SdcTool.TOPUP,) if hmc is HmcMethod.EDDY else (SdcTool.DRBUDDI,)
    else:
        try:
            pepolar_tools = tuple(SdcTool(part) for part in pepolar_key.split('+'))
        except ValueError:
            raise ValueError(f'Unknown pepolar method: {pepolar_method!r}') from None

    return MethodSelection(
        hmc=hmc,
        pepolar_tools=pepolar_tools,
        shoreline_model=shoreline_model,
    )


def reachable_selections() -> list[MethodSelection]:
    """Every selectable flag combination, for interactive previews.

    The dropdown controls only offer combinations the parser accepts, so
    this is the full payload space a static page needs to embed: each HMC
    method with its capable PEPOLAR tool chains (TOPUP with optional DRBUDDI
    refinement for eddy; DRBUDDI for the others), and every SHORELine signal
    model.
    """
    selections = []
    for sdc in ('topup', 'drbuddi', 'topup+drbuddi'):
        selections.append(selection_for_config('eddy', sdc))
    for model in SHORELINE_MODELS:
        selections.append(
            MethodSelection(
                hmc=HmcMethod.SHORELINE,
                pepolar_tools=(SdcTool.DRBUDDI,),
                shoreline_model=model,
            )
        )
    selections.append(selection_for_config('tortoise', 'drbuddi'))
    return selections


#: The MethodSelection each legacy backend name previews as.
_CANONICAL = {
    'fsl': ('eddy', 'TOPUP'),
    'mixed': ('eddy', 'TOPUP+DRBUDDI'),
    'tortoise': ('tortoise', 'DRBUDDI'),
}


def canonical_selection(backend: str) -> MethodSelection:
    """The representative :class:`MethodSelection` for a legacy backend name."""
    if backend not in _CANONICAL:
        raise ValueError(f'Unknown backend: {backend!r}')
    return selection_for_config(*_CANONICAL[backend])
