# Issue codes

Every problem or notable decision found while grouping is reported as an
issue with a stable machine-readable code, a severity, and a message. The text report lists them under `Notes:`
as `ERROR [code]: ...` or `WARNING [code]: ...`; the HTML pages list them in
their notes section.

## Severities

error
: The grouping cannot be processed as it stands. qsiprep aborts on an
  error-severity issue. The `qsiplan` command still prints the full report
  and previews, and exits with status 1.

warning
: The grouping proceeds. The message says what was decided and which
  sidecar field or flag would change it.

A few codes are decided at emit time and list both severities: an anatomical
SDC method that was demanded (curated, or forced from the command line) is an
error where an inferred one is a warning, and `--ignore fov` downgrades
`fov-oblique` from an error to a warning.

## Feasibility issues

Feasibility issues are separate from grouping issues. They are found when
the plan is compiled for one method selection, and answer whether that
selection could process the grouping (for example `eddy-requires-shelled` or
`topup-single-signature`). They appear in the processing previews, not in
the grouping report, and do not change the `qsiplan` exit status.

## Registry

This table is generated from the registry of codes in the code. A conformance test checks that every emitted code is registered and
every registered code is emitted, with matching severities.

```{eval-rst}
.. qsiplan-issue-codes::
```
