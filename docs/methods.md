# Methods

This page is the reference for how the command-line flags map onto the plan:
which flags change the grouping, which pick the software, and what each
combination of head-motion method and distortion-correction tool runs. It
does not describe the algorithms.

## How the flags are organized

A full flag combination factors into three independent choices.

Grouping policy
: `--separate-all-dwis`, `--ignore`, `--force`, `--sdc-anat-reference`, and
  `--distortion-group-merge`. Every one of these changes what the grouping
  contains.

Method selection
: `--hmc-method` (which software corrects head motion), `--sdc-method` (the PEPOLAR tool chain), and the
  SHORELine signal model (`--shoreline-model` here, the `"model"` key of
  `--shoreline-config` in qsiprep). It picks the software; it never changes
  the grouping.

Session scope
: `--session-label` filters which
  sessions are considered at all. `--subject-anatomical-reference` decides how
  the in-scope sessions map to processing: `first-lex` and `unbiased` plan the
  subject as a whole, so anatomical discovery reaches across sessions;
  `sessionwise` isolates each session into its own plan.

Two capability registries record the per-tool facts that the feasibility
checks and the plan compiler consult; the rest of this page lists them and
the stage sequence each combination runs.

## HMC methods

`--hmc-method` selects one of these. `--shoreline-model` picks the SHORELine
signal model (`3dshore`, `tensor`, or `none`) and is only valid with
`--hmc-method shoreline`. It is a qsiplan flag only: qsiprep reads the model
from the `"model"` key of its `--shoreline-config` JSON file, so give
qsiplan the value that file sets. The other plan options below are spelled
the same way on both command lines.

```{eval-rst}
.. qsiplan-capabilities:: hmc
```

## SDC tools

`--sdc-method` selects the PEPOLAR tool chain: `topup`, `drbuddi`, or
`topup+drbuddi` (TOPUP with DRBUDDI refinement). The other tools are chosen
per unit by the grouping: a GRE estimation routes to the fieldmap tool, an
`anat_reg` estimation to T2Wreg, a `syn` estimation to SyN.

```{eval-rst}
.. qsiplan-capabilities:: sdc
```

### `--sdc-method auto`

`auto` (the default) resolves to the HMC method's preferred PEPOLAR tool:
TOPUP with `eddy`, DRBUDDI otherwise. Mismatched explicit
combinations, such as `tortoise` with `topup`, are accepted by the parser;
feasibility is the compiler's job and is reported in the preview.

## Anatomical SDC references

`--sdc-anat-reference` picks which anatomical-derived image may drive
fieldmap-less SDC, as a fallback for series no fieldmap reaches (see
{doc}`concepts`). `none` and `auto` are handled around this table; `auto`
walks the rows by rank, lowest first, and takes the first whose anatomical
image the session has.

```{eval-rst}
.. qsiplan-anat-references::
```

## Plan options

The plan-relevant flags, from the one list in the code that defines them.
qsiprep checks its own parser against the same list in a contract test, so the two
cannot drift apart. `extendable` marks a split-owned list
to which qsiprep adds choices that mean nothing to grouping (`--ignore
phase`); `planned` marks a flag not yet exposed by every consumer.

```{eval-rst}
.. qsiplan-plan-options::
```

## What each combination runs

Compiling the plan turns a grouping and a selection into one processing run
per correction unit (or per split of one), each an ordered sequence of
stages: `estimate`, `hmc`, `hmc-with-field`, `estimate+apply`, or
`refine`. Stage order is the point: eddy+TOPUP estimates
the field first and consumes it during motion correction, while DIFFPREP and
SHORELine correct motion first and estimate afterwards. All SDC warps except
the integrated TOPUP-into-eddy field are composed into the final resampling,
so there is no separate apply stage.

Two splits happen before the stages are assigned. An HMC method with
`decomposes_pepolar_pairs` (TORTOISE, SHORELine) splits a PEPOLAR unit into
one run per matched blip-up/blip-down pair. A unit whose series are encoded
more than one way, corrected by a `single_encoding` tool (GRE fieldmap,
T2Wreg, SyN), splits into one run per encoding.

eddy + TOPUP
: PEPOLAR unit: TOPUP `estimate`, then eddy `hmc-with-field`. SyNb0 unit:
  the same two stages, with the synthetic b=0 joining TOPUP's inputs as a
  zero-readout group. GRE unit: eddy `hmc`, then fieldmap `estimate+apply`.
  SyN (`invt1w`) unit: eddy `hmc`, then SyN `estimate+apply`. T2Wreg unit or
  uncorrected unit: eddy `hmc` only; a T2Wreg estimation on this path raises
  `anat-sdc-unsupported`. TOPUP needs at least two distortion signatures
  (`topup-single-signature`).

eddy + DRBUDDI
: PEPOLAR unit: eddy `hmc`, then DRBUDDI `estimate+apply`. eddy keeps the
  unit pooled, so the estimation must be exactly one matched blip pair
  (`drbuddi-only-infeasible` otherwise). SyNb0 unit: eddy `hmc` only, since
  DRBUDDI does not consume the synthetic b=0. GRE, SyN, T2Wreg, and
  uncorrected units: as under eddy + TOPUP.

eddy + TOPUP then DRBUDDI
: PEPOLAR unit: TOPUP `estimate`, eddy `hmc-with-field`, then DRBUDDI
  `refine`. The refinement runs only when the unit is one matched blip pair
  and has reverse phase-encoded DWI series in both polarities; a lone reverse
  b=0 was already consumed by TOPUP (`drbuddi-refinement-not-useful`,
  `drbuddi-refinement-multigroup`), and the run stays single-stage. Other
  units: as under eddy + TOPUP; a GRE estimation notes `mixed-non-pepolar`.

TORTOISE + DRBUDDI
: PEPOLAR unit, one run per blip pair: DIFFPREP `hmc`, then DRBUDDI
  `estimate+apply`, registering to the T2w when the subject has one. A blip
  group with no opposing pair falls back to T2Wreg against the T2w, or is left
  uncorrected (`drbuddi-no-opposing-pair`). GRE unit: DIFFPREP `hmc`, then
  fieldmap `estimate+apply`. SyN unit: DIFFPREP `hmc`, then SyN
  `estimate+apply`. T2Wreg, SyNb0, and uncorrected units: DIFFPREP `hmc`,
  then T2Wreg `estimate+apply` against the structural target (the SyNb0
  synthetic b=0 when requested, else the T2w); with neither, `hmc` only.

SHORELine + DRBUDDI
: PEPOLAR unit, one run per blip pair: SHORELine `hmc`, then DRBUDDI
  `estimate+apply`. GRE and SyN units: as under TORTOISE. T2Wreg, SyNb0, and
  uncorrected units: SHORELine `hmc` only; SHORELine has no registration
  fallback, so an unpaired blip group is left uncorrected.

eddy requires shelled q-space sampling (`eddy-requires-shelled`); SHORELine
and TORTOISE accept non-shelled data and warn when one output mixes both
(`mixed-shelled-nonshelled`).
