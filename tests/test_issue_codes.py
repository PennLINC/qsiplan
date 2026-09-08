"""The issue-code registry is the single list of codes the package can emit.

Codes are free-form strings at their emit sites, so nothing structural stops a
typo from quietly minting a new code, or a registered code from going dead.
These tests close that loop by scanning the package's emitters: every code
emitted is registered, every registered code is emitted somewhere, and each
emitter's severity is one the registry allows. The registry is deliberately
not enforced at emit time - ``error``/``warning`` are public and a consumer
may emit codes of its own - so CI, not the runtime, is the gate.
"""

import ast
import pathlib
import re

import pytest

from qsiplan.models import ANAT_REFERENCES
from qsiplan.validation import ISSUE_CODES, IssueSpec, describe_issue, error, warning

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / 'qsiplan'


def _emitted_codes() -> dict[str, set[str]]:
    """code -> severities, from every emit site in the package.

    Constant-string calls to ``error``/``warning`` carry their severity;
    ``make_issue`` sites pick it at runtime and may use either. The
    anatomical-reference codes are emitted with a variable (from the registry
    tuples), so they are read from ANAT_REFERENCES directly - always errors.
    """
    emitted: dict[str, set[str]] = {}
    for source in sorted(PACKAGE.glob('*.py')):
        tree = ast.parse(source.read_text(), filename=str(source))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id not in ('error', 'warning', 'make_issue') or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                severities = (
                    {'error', 'warning'} if node.func.id == 'make_issue' else {node.func.id}
                )
                emitted.setdefault(first.value, set()).update(severities)
    for reference in ANAT_REFERENCES.values():
        emitted.setdefault(reference.missing_anat_issue[0], set()).add('error')
        if reference.pedir_issue is not None:
            emitted.setdefault(reference.pedir_issue[0], set()).add('error')
    return emitted


def test_every_emitted_code_is_registered():
    unregistered = sorted(set(_emitted_codes()) - set(ISSUE_CODES))
    assert not unregistered, f'emitted but not in ISSUE_CODES: {unregistered}'


def test_every_registered_code_is_emitted():
    dead = sorted(set(ISSUE_CODES) - set(_emitted_codes()))
    assert not dead, f'registered but never emitted: {dead}'


def test_emit_severities_match_the_registry():
    for code, severities in _emitted_codes().items():
        allowed = ISSUE_CODES[code].severities
        assert severities <= allowed, f'{code}: emitted at {severities}, registry allows {allowed}'


def test_registry_entries_are_well_formed():
    for code, spec in ISSUE_CODES.items():
        assert isinstance(spec, IssueSpec)
        assert re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)+', code), code  # stable kebab-case ids
        assert spec.description.strip(), code
        assert spec.description.endswith('.'), code
        assert spec.severities, code
        assert spec.severities <= {'error', 'warning'}, code


def test_describe_issue_explains_registered_codes_and_tolerates_unknown():
    assert describe_issue('no-sdc') == ISSUE_CODES['no-sdc'].description
    assert describe_issue('some-consumer-code') == 'some-consumer-code'


@pytest.mark.parametrize('make', [error, warning])
def test_helpers_still_accept_unregistered_codes_for_consumers(make):
    # The gate is this test suite, not the runtime: a downstream package may
    # emit its own codes through the public helpers.
    issue = make('downstream-only-code', 'a consumer message')
    assert issue.code == 'downstream-only-code'


def test_html_notes_carry_the_registered_meaning(tmp_path):
    # The explorer explains each code on hover with its registered meaning.
    from grouping_scenarios import load_scenario

    from qsiplan.interactive import _esc, _issue_notes

    # missing_pedir always carries a grouping-level 'missing-pedir' warning.
    grouping = load_scenario('missing_pedir', tmp_path, strict=False)
    assert grouping.issues, 'scenario should carry at least one issue'
    html = ''.join(_issue_notes(grouping))
    for issue in grouping.issues:
        assert f'title="{_esc(ISSUE_CODES[issue.code].description)}"' in html
