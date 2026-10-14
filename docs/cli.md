# Command line

```bash
qsiplan /path/to/bids [--participant-label 01 02] [--session-label 1] \
    [--hmc-method eddy|shoreline|tortoise] [--sdc-method auto|topup|drbuddi|topup+drbuddi] \
    [--ignore fieldmaps shims fov] [--separate-all-dwis] [--sdc-anat-reference auto] \
    [--html PATH | --cohort-html PATH | --serve]
```

Nothing is processed and nothing in the dataset is written. With no method
flags, the report previews every default method combination; with
`--hmc-method`, it previews that one selection.

## Reading the text report

This is the report for a two-run HCP-style acquisition (AP and PA runs, no
fieldmaps, no curation). It is one of the golden files the test suite checks.

```{literalinclude} ../tests/data/grouping_reports/hcp_style.txt
:language: text
```

The content of the report, from top to bottom:

Grouping header
: `DWI grouping for sub-01`. With `--subject-anatomical-reference
  sessionwise`, each session's report is preceded by
  `=== sub-01_ses-1 (session-wise) ===`.

Outputs and distortion groups
: One `Output "<name>"` block per final output file, with its `MultipartID`
  and provenance tag and the number of series it holds. Inside, one
  `Distortion group` per set of series sharing a distortion signature, then
  the member files (with a `[shelled: b=...]` or
  `[non-shelled q-space sampling]` annotation when the `.bval` was
  readable). `corrected by: <estimation> [provenance]` names the fieldmap
  estimation applied to the group, or `nothing (no fieldmap found)`. An
  `(also eligible: ...)` line lists estimations that could have corrected it
  and lost. A `Borrows for fieldmap estimation` list names series whose b=0
  images feed this output's fieldmap without being concatenated into it.

Fieldmap estimations
: One entry per estimation: its id, provenance, method, source files, and
  the phase-encoding axes covered (`bidirectional` when both polarities are
  present). `(not used)` marks an estimation that corrects nothing;
  `(initializes DRBUDDI/T2Wreg)` marks a GRE fieldmap that only seeds a
  registration.

Notes
: Present when the grouping has issues. Each line is `WARNING [code]:` or
  `ERROR [code]:` followed by the message. The codes are listed in
  {doc}`issue_codes`.

Processing previews
: One `Processing preview:` section per method selection, titled with the
  exact flags that select it. Each output gets a numbered list of what the
  selected tools do with it, and any feasibility issue for that selection.

## HTML outputs

`--html PATH`
: Also write a self-contained explorer page: the grouping plus live controls
  for every grouping-policy and method flag. The CLI flags pick the initial
  state. With more than one subject, or with `sessionwise` scope, the unit
  label is inserted before the extension (`grouping_sub-01.html`).

`--cohort-html PATH`
: Write a static group-level dashboard: every subject-session sorted into
  workflow equivalence classes, with a data-completeness matrix. A
  `sub-<label>.html` explorer is written beside it for each subject (one per
  session under `sessionwise`), so the dashboard's drill-down links resolve
  offline.

`--serve`
: Serve the explorer live at `http://<host>:<port>` instead of writing
  files. Every control change is answered by the real compiler, so flag
  combinations beyond the embedded grid work too. The root page is the cohort
  dashboard; `/sub-<label>` opens a subject. `--serve` cannot be combined with
  `--html` or `--cohort-html`. `--host` defaults to `127.0.0.1`. Any other
  value, such as `0.0.0.0`, exposes an unauthenticated server that reads your
  dataset to whoever can reach that interface; only do this on a trusted
  network. `--port` defaults to 8765.

## Exit status

0
: The run completed. Warnings do not change the status.

1
: At least one planning unit's grouping has an error-severity issue, or no
  subjects were found. The report is still printed in full. Feasibility
  errors in a processing preview (a method that cannot process the data) do
  not change the status; the grouping report is the contract.

An invalid flag combination (`--shoreline-model` without
`--hmc-method shoreline`, `--serve` with `--html`) exits with a message and a
non-zero status before any dataset is read.

The plan options take the same spellings as qsiprep, with one exception:
qsiprep has no `--shoreline-model`. It takes the SHORELine model from the
`"model"` key of `--shoreline-config`, and qsiplan's flag stands in for that
key. The preview titles therefore show `--shoreline-model` where a qsiprep
command would carry a config file.

## Options

```{eval-rst}
.. argparse::
   :module: qsiplan.cli
   :func: _build_parser
   :prog: qsiplan
```
