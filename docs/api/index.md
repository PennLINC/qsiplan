# API reference

Everything the `qsiplan` command does is available from Python; the command
is a thin wrapper. The public entry points are re-exported from the
{mod}`qsiplan` package. The usual sequence is:

1. Index a subject: {class}`~qsiplan.catalog.Bids2TableCatalog` and
   {func}`~qsiplan.metadata.index_subject` turn a BIDS directory into file
   records, or build {class}`~qsiplan.models.FileRecord` objects in memory
   as the {doc}`grouping tutorial <../tutorials/grouping>` does.
2. Group: {func}`~qsiplan.inference.build_grouping` returns a
   {class}`~qsiplan.models.DWIGrouping`. Inside qsiprep,
   {func}`qsiplan.build_dwi_grouping` does the same from a PyBIDS layout.
   Its `strict` argument (the default, and what qsiprep uses) raises
   {class}`~qsiplan.validation.GroupingError` on the first error-severity
   issue; with `strict=False` the grouping comes back with every issue
   attached, which is what reports want.
3. Plan: {func}`~qsiplan.plan.compile_plan` turns a grouping and a
   {class}`~qsiplan.methods.MethodSelection` (from
   {func}`~qsiplan.methods.selection_for_config`) into an
   {class}`~qsiplan.plan.ExecutionPlan` of
   {class}`~qsiplan.plan.ProcessingRun` and {class}`~qsiplan.plan.PlanStage`
   objects.
4. Render: {func}`~qsiplan.report.report_text`,
   {func}`~qsiplan.report.describe_processing`,
   {func}`~qsiplan.interactive.render_explorer_html`.

A flag combination is addressed by {func}`~qsiplan.methods.combined_key`,
which composes a {class}`~qsiplan.models.GroupingPolicy` and a method
selection into the key the explorer page and the live server use.

The modules below hold the implementation, grouped by what they own.

```{eval-rst}
.. autosummary::
   :toctree: generated
   :recursive:

   qsiplan
   qsiplan.models
   qsiplan.methods
   qsiplan.plan
   qsiplan.validation
   qsiplan.inference
   qsiplan.adapters
   qsiplan.report
   qsiplan.interactive
   qsiplan.explorer
   qsiplan.cohort
   qsiplan.serve
   qsiplan.catalog
   qsiplan.metadata
   qsiplan.scope
   qsiplan.cli_spec
   qsiplan.viz.pipeline
   qsiplan.viz.qspace
```
