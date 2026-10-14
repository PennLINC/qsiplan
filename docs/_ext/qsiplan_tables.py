"""Sphinx directives that render qsiplan's registries as tables.

Every table is built from the live package at build time (issue codes,
capability registries, anatomical references, plan options), so the docs
cannot drift from the code and nothing generated is committed.
"""

from __future__ import annotations

import dataclasses
import enum
import importlib
import inspect
import re

from docutils import nodes
from docutils.parsers.rst import Directive
from sphinx.errors import ExtensionError

_FIELD_COMMENT = re.compile(r'^\s*#:\s?(.*)$')
_FIELD_LINE = re.compile(r'^\s*(\w+)\s*:')


def _registry(module_name: str, attribute: str):
    """Fetch ``module_name.attribute`` or raise a Sphinx error naming it."""
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ExtensionError(
            f'qsiplan_tables: cannot import {module_name!r} for {attribute}: {exc}'
        ) from exc
    try:
        return getattr(module, attribute)
    except AttributeError as exc:
        raise ExtensionError(
            f'qsiplan_tables: {module_name} has no attribute {attribute!r}'
        ) from exc


def _field_comments(cls) -> dict[str, str]:
    """Map dataclass field names to the ``#:`` comments above them in the source."""
    try:
        source = inspect.getsource(cls)
    except (OSError, TypeError):
        return {}
    comments: dict[str, str] = {}
    pending: list[str] = []
    for line in source.splitlines():
        comment = _FIELD_COMMENT.match(line)
        if comment:
            pending.append(comment.group(1).strip())
            continue
        field = _FIELD_LINE.match(line)
        if field and pending:
            comments[field.group(1)] = ' '.join(pending)
        pending = []
    return comments


def _label(value) -> str:
    """Render one registry value as table text."""
    if value is None:
        return '-'
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    if isinstance(value, (frozenset, set, tuple, list)):
        if not value:
            return '-'
        return ', '.join(sorted(_member_label(item) for item in value))
    if isinstance(value, enum.Enum):
        return str(value.value)
    return str(value)


def _member_label(item) -> str:
    """A frozenset member: the tool's registry label when it has one.

    The check is on type, not membership: ``CorrectionMethod`` and ``SdcTool``
    are both ``StrEnum`` and share spellings (``'fieldmap'``, ``'syn'``).
    """
    sdc_tool = _registry('qsiplan.methods', 'SdcTool')
    if isinstance(item, sdc_tool):
        return _registry('qsiplan.methods', 'SDC_CAPABILITIES')[item].label
    if isinstance(item, enum.Enum):
        return str(item.value)
    return str(item)


def _cell(content) -> nodes.entry:
    entry = nodes.entry()
    paragraph = nodes.paragraph()
    if isinstance(content, nodes.Node):
        paragraph += content
    else:
        paragraph += nodes.Text(str(content))
    entry += paragraph
    return entry


def _table(headers: list[str], rows: list[list]) -> nodes.table:
    table = nodes.table()
    tgroup = nodes.tgroup(cols=len(headers))
    table += tgroup
    for _ in headers:
        tgroup += nodes.colspec(colwidth=1)
    thead = nodes.thead()
    tgroup += thead
    header_row = nodes.row()
    for header in headers:
        header_row += _cell(header)
    thead += header_row
    tbody = nodes.tbody()
    tgroup += tbody
    for row in rows:
        row_node = nodes.row()
        for content in row:
            row_node += _cell(content)
        tbody += row_node
    return table


def _definition_list(items: list[tuple[str, str]], state, lineno) -> nodes.definition_list:
    """A definition list whose definitions are parsed as inline reST."""
    dlist = nodes.definition_list()
    for term, definition in items:
        item = nodes.definition_list_item()
        term_node = nodes.term()
        term_node += nodes.literal('', term)
        item += term_node
        definition_node = nodes.definition()
        paragraph = nodes.paragraph()
        textnodes, messages = state.inline_text(definition, lineno)
        paragraph.extend(textnodes)
        definition_node += paragraph
        definition_node.extend(messages)
        item += definition_node
        dlist += item
    return dlist


class IssueCodesDirective(Directive):
    """One row per registered issue code, in registry order."""

    def run(self):
        codes = _registry('qsiplan.validation', 'ISSUE_CODES')
        rows = [
            [
                nodes.literal('', code),
                ', '.join(sorted(spec.severities)),
                spec.description,
            ]
            for code, spec in codes.items()
        ]
        return [_table(['Code', 'Severity', 'Meaning'], rows)]


class CapabilitiesDirective(Directive):
    """Rows per method/tool; columns per capability field; field glossary below."""

    required_arguments = 1

    def run(self):
        axis = self.arguments[0].lower()
        if axis == 'hmc':
            registry = _registry('qsiplan.methods', 'HMC_CAPABILITIES')
            first = 'Method'
        elif axis == 'sdc':
            registry = _registry('qsiplan.methods', 'SDC_CAPABILITIES')
            first = 'Tool'
        else:
            raise self.error(f"qsiplan-capabilities takes 'hmc' or 'sdc', not {axis!r}")
        if not registry:
            raise ExtensionError(f'qsiplan_tables: the {axis} capability registry is empty')
        cls = type(next(iter(registry.values())))
        fields = [f for f in dataclasses.fields(cls) if f.name != 'label']
        headers = [first] + [f.name for f in fields]
        rows = [
            [nodes.strong('', caps.label)] + [_label(getattr(caps, f.name)) for f in fields]
            for caps in registry.values()
        ]
        comments = _field_comments(cls)
        glossary = [(f.name, comments.get(f.name, '-')) for f in fields]
        return [_table(headers, rows), _definition_list(glossary, self.state, self.lineno)]


class AnatReferencesDirective(Directive):
    """One row per ``--sdc-anat-reference`` value in the ANAT_REFERENCES table."""

    def run(self):
        references = _registry('qsiplan.models', 'ANAT_REFERENCES')
        rows = []
        for reference in references.values():
            codes = [reference.missing_anat_issue[0]]
            if reference.pedir_issue is not None:
                codes.append(reference.pedir_issue[0])
            rows.append(
                [
                    nodes.literal('', reference.name),
                    _label(reference.method),
                    reference.source_suffix,
                    f'auto+{reference.id_stem}',
                    _label(reference.auto_rank),
                    _label(reference.pedir_issue is not None),
                    ', '.join(codes),
                    reference.description,
                ]
            )
        headers = [
            'Value',
            'Method',
            'Anatomical image',
            'Estimation id',
            'Auto rank',
            'Needs PhaseEncodingDirection',
            'Issue codes',
            'Description',
        ]
        return [_table(headers, rows)]


class PlanOptionsDirective(Directive):
    """One row per plan-relevant CLI option in PLAN_OPTIONS."""

    def run(self):
        options = _registry('qsiplan.cli_spec', 'PLAN_OPTIONS')
        rows = []
        for option in options:
            notes = [
                marker for marker in ('extendable', 'planned') if getattr(option, marker, False)
            ]
            choices = option.owned_choices()
            rows.append(
                [
                    nodes.literal('', option.flag),
                    _label(option.kind),
                    ', '.join(choices) if choices else '-',
                    _label(option.default),
                    _label(option.axis),
                    ', '.join(notes) if notes else '-',
                ]
            )
        return [_table(['Flag', 'Kind', 'Choices', 'Default', 'Axis', 'Notes'], rows)]


def setup(app):
    app.add_directive('qsiplan-issue-codes', IssueCodesDirective)
    app.add_directive('qsiplan-capabilities', CapabilitiesDirective)
    app.add_directive('qsiplan-anat-references', AnatReferencesDirective)
    app.add_directive('qsiplan-plan-options', PlanOptionsDirective)
    return {'parallel_read_safe': True, 'parallel_write_safe': True}
