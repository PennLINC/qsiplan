# Concepts

qsiplan describes a subject's DWI data in BIDS vocabulary, with four kinds of
group. Outputs say which series end up in one preprocessed file, distortion
groups which series share a distortion, and fieldmap estimations which files
a field is estimated from. Correction units combine the three: they are what
one head-motion run works on. Curated sidecar values are used verbatim;
everything else is inferred and tagged with its provenance. The grouping
itself knows nothing about eddy, TOPUP or DRBUDDI; how a method arranges
that data is decided afterwards, when the plan is compiled.

## Output (MultipartID)

The purpose of a qsiplan run is to get the outputs you want. An output is
one preprocessed DWI file. It corresponds to a `MultipartID`: the series that share a
`MultipartID` are processed together and concatenated in qsiprep's
derivatives. When no series of the subject has a `MultipartID`, the corrected
series of a session form one output. Once any series has one, nothing is
guessed about concatenation: a series without a `MultipartID` is an output on
its own. With `--separate-all-dwis`, every series is an output on its own.

How to name outputs, and how to send one series to several outputs, are in
section 4 of {doc}`tutorials/grouping`.

Everything below is about getting the right series, corrected the right
way, into each output.

## Distortion group

A distortion group is the set of DWI files that share one susceptibility
distortion and one correction. Files are bucketed by their distortion signature
(`PhaseEncodingDirection`, `TotalReadoutTime`, `ShimSetting`), and a group is
also split wherever its files are corrected by different fieldmaps or written to
different output files, so it never spans two of either. The report labels a
group by its shared BIDS entities and signature, for example `sub-01_dir-AP (PE
j-, TRT 0.05s)`.

## Fieldmap estimation (B0FieldIdentifier)

A fieldmap estimation is the set of files combined to estimate one fieldmap.
It corresponds to a `B0FieldIdentifier`. Each estimation has a method naming
what the field is estimated from:

- `pepolar`: EPI images with opposite phase encoding, from `fmap/` or from
  the DWI series themselves;
- `fieldmap`, `phasediff`, `two_phases`: a GRE fieldmap, as a field in Hz,
  a phase difference, or two phase images;
- `anat_reg`, `syn`, `synb0`: no fieldmap at all. The field is inferred by
  registering the distorted b=0 to an undistorted reference image; the three
  values name the reference (see the anatomical SDC section below).

Which estimation corrects which DWI file is the grouping's `application`
map, the effective `B0FieldSource`. Inferred identifiers always start with
the reserved `auto+` prefix, so they cannot collide with curated names.

## Correction unit

A correction unit is what one head-motion run works on: the distortion groups
that are concatenated, motion corrected together, and unwarped with one fieldmap
estimation.

The rule is that, within one output, the distortion groups corrected by the
same estimation form one unit. Whatever separates estimations separates
units too.

- A `dir-AP` and a `dir-PA` series corrected by one PEPOLAR estimation are
  one unit. The two directions are motion corrected together and unwarped
  with the same field.
- Two runs corrected by different estimations, for example two curated GRE
  fieldmaps, are two units even when they ultimately are concatenated into
  the same output.
- A group that no fieldmap reaches is a unit on its own.

A unit's fieldmap may be estimated with b=0 images from series in another
output. The report marks these with `Borrows for fieldmap estimation`, and
section 4 of {doc}`tutorials/grouping` shows an example.

## Denoising order

Each DWI series is denoised on its own, before it is concatenated with
anything. qsiprep's `--denoise-after-combining` option, which concatenated
first and denoised the result, is deprecated, and qsiplan no longer
describes it.

To denoise several series as one, concatenate them yourself before running
qsiprep, writing one series with one sidecar and merged `.bval`/`.bvec`
files in the same volume order. Do this only for series that share an
acquisition:

- the same `PhaseEncodingDirection`, `TotalReadoutTime` and `ShimSetting`,
  since one sidecar cannot describe two distortions. Never merge a `dir-AP`
  series with its `dir-PA` partner;
- the same voxel grid and orientation, ideally with no repositioning
  between the series;
- the same intensity scaling. Denoising assumes the noise level is the same
  across volumes, and runs scaled differently by the scanner break that.

Head motion between the series is not corrected before denoising, so the
more the head moved between them, the less concatenating first helps.
The merged file is a single series to qsiplan: drop any `MultipartID` the
parts carried and rerun qsiplan to see the new plan.

## Provenance tags

Every decision carries a provenance tag, printed in brackets in the text
report:

`curated`
: An explicit `B0FieldIdentifier`, `B0FieldSource`, or `MultipartID` in a sidecar.

`intendedfor`
: Translated from a fieldmap's `IntendedFor`. Honored, but deprecated: use
  the `B0Field*` fields for new curation.

`cli-override`
: Requested by a command-line flag, such as an explicit `--sdc-anat-reference`.

`inferred`
: A heuristic. The reverse-PE pairing runs only for subjects with no
  fieldmap linkage at all; once any fieldmap in the subject is linked, by
  `B0Field*` or by an epi fieldmap's `IntendedFor`, no pairing is guessed
  in any of its sessions.

## Anatomical SDC reference: fallback or override

Fieldmap-less correction has two parts: a *reference image* that shows
where the anatomy should be, and a *tool* that registers the distorted b=0
to it and reads the field off the registration. `--sdc-anat-reference`
picks which undistorted anatomical image to use (or estimate).

| `--sdc-anat-reference` | Reference image | Method | Tool |
|---|---|---|---|
| `t2w` | the subject's T2w | `anat_reg` | TORTOISE T2Wreg (with `--hmc-method tortoise`) |
| `invt1w` | the T1w with its intensity inverted | `syn` | nipreps-style SyN, on any head-motion method |
| `synb0` | a synthetic distortion-free b=0 generated from the T1w with SynB0-DISCO | `synb0` | the synthetic image stands in for a reverse phase-encoded acquisition: TOPUP with `eddy`, T2Wreg with TORTOISE |

`none` (the default) disables anatomical SDC. `auto` resolves per session: a T1w
selects `synb0`, otherwise a T2w selects `t2w`, otherwise nothing, with a
warning. `invt1w` is never picked by `auto` and is not allowed in T2Wreg.  A
reference the selected head-motion method cannot use is reported as
`anat-sdc-unsupported`; the {doc}`methods` page lists what each combination
runs.

The selected method is a fallback. It corrects only series that no fieldmap
reaches; a real fieldmap always outranks it. `--force sdc-anat-reference`
escalates it to an override that replaces the fieldmap application for every
DWI series. Forcing with `--sdc-anat-reference none` is an error.
