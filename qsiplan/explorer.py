"""Enumerate the grouping-policy grid behind the interactive explorer page.

A full flag combination factors into two pure stages: a
:class:`~.models.GroupingPolicy` turns indexed records into a grouping, and a
:class:`~.methods.MethodSelection` turns a grouping into an execution plan.
The explorer page makes both axes live. The method axis is small and always
embeddable; the policy axis is a grid of CLI flags whose combinations mostly
collapse for any given dataset (no fieldmaps means ``--ignore fieldmaps``
changes nothing), so this module enumerates the grid once at generation time
and dedupes it by *grouping content*: policies that produce identical
groupings share one signature, one embedded rendering, and one set of
compiled plans.

The records are indexed once, with fieldmaps included -
``ignore_fieldmaps`` combinations are produced by filtering the record list,
never by re-reading the dataset.
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools
import json

from .inference import build_grouping
from .models import DWIGrouping, FileRecord, GroupingPolicy


def canonical_explorer_policy(policy: GroupingPolicy | None = None) -> GroupingPolicy:
    """Canonicalize a policy to the explorer's native axes.

    The anatomical-SDC axis is already a single pair of fields; the one
    cross-field constraint is that ``force_sdc_anat_reference`` selects nothing without
    a method, so it is cleared when ``sdc_anat_reference`` is ``'none'``.
    """
    policy = policy if policy is not None else GroupingPolicy()
    if policy.sdc_anat_reference == 'none' and policy.force_sdc_anat_reference:
        return dataclasses.replace(policy, force_sdc_anat_reference=False)
    return policy


def reachable_policies(base: GroupingPolicy | None = None) -> list[GroupingPolicy]:
    """Every policy the explorer page's controls can select.

    The grid mirrors the CLI's policy flags: the six boolean toggles
    (``--separate-all-dwis`` and the ``--ignore`` values fieldmaps/pepolar-dwis/
    t2w/shims/fov), the anatomical-SDC axis as native ``(sdc_anat_reference,
    force_sdc_anat_reference)`` pairs - every method as a fallback plus every method
    forced, and ``'auto'`` alongside them; ``('none', True)`` is the only
    invalid pair and is excluded. ``'auto'`` is a first-class cell, not an
    alias: it resolves per session (:func:`~.inference.resolve_fieldmapless`),
    so on a subject whose sessions carry different anatomicals its grouping can
    differ from every single concrete method - it content-dedups with one only
    when they coincide. The one field outside the grid (``ignore_sdc``) carries
    ``base``'s value through every combination.
    """
    base = canonical_explorer_policy(base)
    policies = []
    for (
        separate,
        no_fmaps,
        no_pepolar,
        no_t2w,
        no_shims,
        no_fov,
        (sdc_anat_reference, force_sdc_anat_reference),
        merge,
    ) in itertools.product(
        (False, True),
        (False, True),
        (False, True),
        (False, True),
        (False, True),
        (False, True),
        (
            ('none', False),
            ('auto', False),
            ('synb0', False),
            ('t2w', False),
            ('invt1w', False),
            ('auto', True),
            ('synb0', True),
            ('t2w', True),
            ('invt1w', True),
        ),
        ('concat', 'average', 'none'),
    ):
        policies.append(
            dataclasses.replace(
                base,
                separate_all_dwis=separate,
                ignore_fieldmaps=no_fmaps,
                ignore_pepolar_dwis=no_pepolar,
                ignore_t2w=no_t2w,
                ignore_shims=no_shims,
                ignore_fov=no_fov,
                sdc_anat_reference=sdc_anat_reference,
                force_sdc_anat_reference=force_sdc_anat_reference,
                distortion_group_merge=merge,
            )
        )
    return policies


def grouping_signature(grouping: DWIGrouping) -> str:
    """Content hash of a grouping's structure, ignoring the policy that built it.

    Policies sharing a signature produced literally the same grouping. This
    is the explorer's join key: toggles that are no-ops for a dataset
    collapse onto one signature, so renderings are embedded and plans
    compiled once per distinct grouping rather than once per policy. The
    policy field is excluded - it records which flags *asked* for the
    grouping, not what the grouping *is*.
    """
    projection = grouping.to_dict()
    del projection['policy']
    serialized = json.dumps(projection, sort_keys=True, default=str)
    return hashlib.sha1(serialized.encode(), usedforsecurity=False).hexdigest()[:10]


def drop_fieldmaps(records: list[FileRecord], index_issues=()) -> tuple[list[FileRecord], list]:
    """The record list as ``--ignore-fieldmaps`` indexing would have built it.

    Filters out the fmap/ records and the indexing issues that only concern
    them, so one fieldmaps-included index pass serves both settings.
    """
    fmap_paths = {record.path for record in records if record.datatype == 'fmap'}
    kept_records = [record for record in records if record.datatype != 'fmap']
    kept_issues = [
        issue
        for issue in index_issues
        if not (issue.files and all(path in fmap_paths for path in issue.files))
    ]
    return kept_records, kept_issues


def drop_t2w(records: list[FileRecord], index_issues=()) -> tuple[list[FileRecord], list]:
    """The record list as ``--ignore t2w`` indexing would have built it.

    Filters out the T2w anat records (and any indexing issues that only concern
    them), so one T2w-included index pass serves both settings. T1w records are
    kept - only T2Wreg fieldmap-less correction depends on the T2w.
    """
    t2w_paths = {record.path for record in records if record.is_anat and record.suffix == 'T2w'}
    kept_records = [record for record in records if record.path not in t2w_paths]
    kept_issues = [
        issue
        for issue in index_issues
        if not (issue.files and all(path in t2w_paths for path in issue.files))
    ]
    return kept_records, kept_issues


def build_for_policy(
    records: list[FileRecord],
    subject_id: str,
    policy: GroupingPolicy,
    index_issues=(),
) -> DWIGrouping:
    """One grouping under one policy, from a fieldmaps- and T2w-included index."""
    index_issues = list(index_issues)
    if policy.ignore_fieldmaps:
        records, index_issues = drop_fieldmaps(records, index_issues)
    if policy.ignore_t2w:
        records, index_issues = drop_t2w(records, index_issues)
    return build_grouping(
        records,
        subject_id=subject_id,
        **dataclasses.asdict(policy),
        extra_issues=index_issues,
    )


@dataclasses.dataclass(frozen=True)
class PolicyGrid:
    """The deduped policy grid for one subject.

    ``policy_index`` maps every reachable policy's canonical key to its
    grouping signature; ``policy_cli`` maps it to the CLI phrase that selects
    it; ``groupings`` holds one representative grouping per distinct
    signature.
    """

    policy_index: dict[str, str | None]
    policy_cli: dict[str, str]
    groupings: dict[str, DWIGrouping]


def build_live_policy_grid(
    records: list[FileRecord],
    subject_id: str,
    *,
    base: GroupingPolicy | None = None,
    index_issues=(),
) -> PolicyGrid:
    """A live grid with only the initial grouping compiled.

    Unknown signatures are filled by the server as policies are requested.
    Static pages still use :func:`build_policy_grid` for full offline parity.
    """
    base = canonical_explorer_policy(base)
    grouping = build_for_policy(records, subject_id, base, index_issues)
    return live_policy_grid(grouping, base)


def live_policy_grid(grouping: DWIGrouping, base: GroupingPolicy) -> PolicyGrid:
    """Wrap one already-compiled grouping in the live policy index."""
    base = canonical_explorer_policy(base)
    signature = grouping_signature(grouping)
    policies = reachable_policies(base)
    policy_index = {policy.policy_key(): None for policy in policies}
    policy_cli = {policy.policy_key(): policy.cli_phrase() for policy in policies}
    policy_index[base.policy_key()] = signature
    return PolicyGrid(
        policy_index=policy_index,
        policy_cli=policy_cli,
        groupings={signature: grouping},
    )


def build_policy_grid(
    records: list[FileRecord],
    subject_id: str,
    *,
    base: GroupingPolicy | None = None,
    index_issues=(),
) -> PolicyGrid:
    """Build and dedupe a grouping for every reachable policy.

    ``records`` must come from an index pass *with* fieldmaps (see
    :func:`drop_fieldmaps`); ``index_issues`` are that pass's issues.
    """
    policy_index: dict[str, str] = {}
    policy_cli: dict[str, str] = {}
    groupings: dict[str, DWIGrouping] = {}
    for policy in reachable_policies(base):
        grouping = build_for_policy(records, subject_id, policy, index_issues)
        signature = grouping_signature(grouping)
        key = policy.policy_key()
        policy_index[key] = signature
        policy_cli[key] = policy.cli_phrase()
        groupings.setdefault(signature, grouping)
    return PolicyGrid(policy_index=policy_index, policy_cli=policy_cli, groupings=groupings)
