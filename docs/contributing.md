# Contributing

## Development

Set up the pinned lint/format hooks once. They run automatically on every
commit and use the exact tool versions CI does, so nothing passes locally and
then fails in CI:

```bash
pip install -e '.[tests,dev]'
pre-commit install
```

To lint the whole tree the way CI does (or before a first commit):

```bash
pre-commit run --all-files
```

The pinned versions live in a single place, `.pre-commit-config.yaml`; bump a
`rev` there and both local hooks and CI follow.

## Running the tests

```bash
pip install -e '.[tests]'
pytest
```

## Regenerating the golden reports

The text reports under `tests/data/grouping_reports/` are golden files: the
tests render each scenario skeleton and compare the output byte for byte.
When a change to the report or preview prose is intended, regenerate them
and review the diff:

```bash
QSIPREP_REGEN_GROUPING_REPORTS=1 pytest tests/test_grouping_report.py
```

## Building the docs

```bash
pip install -e '.[docs]'
sphinx-build -W -b html docs docs/_build/html
```

`-W` turns warnings into errors, which is how Read the Docs builds every push
and how the release workflow checks the tagged commit before publishing.  The
tutorial notebook is executed during the build, so a change that breaks its code
cells fails the build.

## Extending the method matrix

The package docstring is the authoritative checklist for a new
{class}`~qsiplan.models.CorrectionMethod`; it is rendered here so the two
cannot drift.

```{eval-rst}
.. automodule:: qsiplan
   :no-members:
   :no-index:
```
