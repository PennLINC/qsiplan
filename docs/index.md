# qsiplan

It's a good idea to qsiplan before you qsiprep.

qsiplan plans and explains [qsiprep](https://github.com/PennLINC/qsiprep)'s
diffusion-MRI preprocessing from BIDS metadata and configuration flags. It
groups a subject's DWI scans the way qsiprep will (distortion groups, fieldmap
estimations, correction units, outputs), validates the data and curation
(missing phase-encoding metadata, IntendedFor conflicts, shim inconsistencies, b-value
mismatches), compiles the execution plan for any combination of head-motion
and susceptibility-correction methods, and renders it all as text or as a
self-contained interactive HTML page. Nothing is processed and nothing in the
dataset is written.

## Quickstart

```bash
pip install qsiplan
qsiplan /path/to/bids
qsiplan /path/to/bids --html grouping.html
```

The first command prints the grouping report and a processing preview for
every default method combination. The second also writes an explorer page
with live controls for every grouping and method flag.

```{toctree}
:maxdepth: 2

installation
concepts
cli
issue_codes
methods
tutorials/grouping
api/index
changelog
contributing
```
