"""Partition a cohort by the workflow each subject-session produces.

The group-level analog of the single-subject explorer: instead of one grouping,
this reads every subject, compiles the plan under each head-motion method, and
sorts subject-sessions into *equivalence classes* - the distinct pipelines the
data actually produces. In a uniform study hundreds of subjects collapse to a
handful of classes, and the outliers (a missing scan, a stray T2w) are exactly
what a QA pass wants to see.

The spine is one function - :func:`plan_signature`. Two entities share a class
iff their signatures match. The signature is a canonical, anonymized projection
of the *session-sliced* plan: stage roles/tools/methods and estimation fan-in
survive; subject/session ids, file paths and entity labels do not. Slicing the
authoritative subject-level plan by each run's correction-unit session (rather
than rebuilding a per-session grouping) keeps cross-session fieldmaps from
leaking into a single session's signature.
"""

from __future__ import annotations

import hashlib
import json

from .interactive import cohort_page_html
from .metadata import index_subject
from .methods import canonical_selection, selection_for_config
from .plan import compile_plan

#: The head-motion methods the cohort selector offers, each with its default
#: SDC chain. Keyed by the value the page's selector and signatures use.
COHORT_METHODS = [
    ('eddy', 'eddy + TOPUP→DRBUDDI', canonical_selection('mixed')),
    ('shoreline', 'SHORELine + DRBUDDI', selection_for_config('shoreline', 'drbuddi')),
    ('tortoise', 'TORTOISE + DRBUDDI', canonical_selection('tortoise')),
]


def _run_session(grouping, run) -> str | None:
    """The session a plan run belongs to, via its correction unit."""
    unit = grouping.correction_units.get(run.logical_unit)
    return unit.session if unit else None


def _concat_session(grouping):
    """Map each concatenation-group key to its session."""
    return {key: cg.session for key, cg in grouping.concatenation_groups.items()}


def plan_signature(grouping, plan, session: str | None) -> str:
    """Anonymized structural hash of one session's slice of a plan.

    Keeps topology (per-run ordered stage roles/tools/methods, structural-
    target presence, fieldmap fan-in; output strategies; issue codes), drops
    identity (subject/session, file paths, entity labels). ``session`` selects
    the slice; ``None`` signs the whole plan (single-session subjects).
    """
    concat_ses = _concat_session(grouping)
    runs = sorted(
        tuple(
            (
                stage.role.value,
                stage.tool,
                stage.method.value if stage.method else None,
                bool(stage.structural_target),
                len(stage.fieldmap_sources),
            )
            for stage in run.stages
        )
        for run in plan.runs
        if session is None or _run_session(grouping, run) == session
    )
    run_keys = {
        run.key for run in plan.runs if session is None or _run_session(grouping, run) == session
    }
    outputs = sorted(
        (assembly.strategy, len(assembly.input_runs))
        for assembly in plan.outputs
        if session is None or concat_ses.get(assembly.output_group) == session
    )
    issues = sorted(
        (issue.severity, issue.code)
        for issue in plan.issues
        if issue.run is None or issue.run in run_keys
    )
    blob = json.dumps({'runs': runs, 'outputs': outputs, 'issues': issues}, sort_keys=True)
    return hashlib.sha1(blob.encode(), usedforsecurity=False).hexdigest()[:8]


def _session_facts(grouping, plan, session):
    """The per-session data + issue counts an entry carries for one method."""
    concat_ses = _concat_session(grouping)
    run_keys = {
        run.key for run in plan.runs if session is None or _run_session(grouping, run) == session
    }
    outputs = sum(
        1
        for assembly in plan.outputs
        if session is None or concat_ses.get(assembly.output_group) == session
    )
    errors = warnings = 0
    for issue in plan.issues:
        if issue.run is not None and issue.run not in run_keys:
            continue
        if issue.severity == 'error':
            errors += 1
        elif issue.severity == 'warning':
            warnings += 1
    return {
        'sig': plan_signature(grouping, plan, session),
        'outputs': outputs,
        'runs': len(run_keys),
        'errors': errors,
        'warnings': warnings,
    }


def _sessions_of(grouping):
    """Sorted session labels present among a subject's DWI records."""
    sessions = {record.session for record in grouping.files.values() if record.is_dwi}
    return sorted(s for s in sessions if s is not None) or [None]


def build_cohort_data(catalog, subjects, *, scope=None, policy=None, initial_method=None):
    """Compute the full embed for the cohort dashboard.

    For every subject: index once, build the grouping under ``policy``, and
    compile the plan under each :data:`COHORT_METHODS` selection. Emits one
    session-level and one subject-level entity per subject-session / subject,
    each carrying per-method signatures and issue counts, plus the data facts
    (scan count, T2w presence) the completeness matrix needs.

    Honors both scope axes. Under ``sessionwise`` each session is compiled as
    its own isolated unit (so its signature reflects session-scoped anatomy and
    it drills into its own page); otherwise the subject is compiled whole and
    its per-session rows are slices of that one plan.
    """
    from .catalog import collect_subject_data
    from .explorer import build_for_policy
    from .models import GroupingPolicy
    from .scope import SessionScope, plan_units

    scope = scope or SessionScope()
    policy = policy if policy is not None else GroupingPolicy()
    session_entities = []
    subject_entities = []

    for subject in subjects:
        subject_data = collect_subject_data(catalog, subject, scope.session_filter)
        if not subject_data['dwi']:
            continue
        records, index_issues = index_subject(catalog, subject_data)

        subj_methods = {key: [] for key, _l, _s in COHORT_METHODS}
        subj_sessions = []
        subj_scans = 0
        subj_has_t2w = False
        subject_href = None  # the unit a subject-level row drills into

        for unit, unit_records in plan_units(subject, records, scope.model):
            unit_key = unit.label[len('sub-') :]
            if subject_href is None:
                subject_href = unit_key
            grouping = build_for_policy(unit_records, subject, policy, index_issues)
            plans = {key: compile_plan(grouping, sel) for key, _label, sel in COHORT_METHODS}
            has_t2w = bool(grouping.anat_files('T2w'))
            subj_has_t2w = subj_has_t2w or has_t2w
            subj_scans += sum(1 for record in grouping.files.values() if record.is_dwi)

            for session in _sessions_of(grouping):
                scans = sum(
                    1
                    for record in grouping.files.values()
                    if record.is_dwi and record.session == session
                )
                by_method = {
                    key: _session_facts(grouping, plans[key], session)
                    for key, _label, _sel in COHORT_METHODS
                }
                session_entities.append(
                    {
                        'subject': subject,
                        'session': session,
                        'label': f'{subject}/{session}' if session else subject,
                        'href': unit_key,
                        'scans': scans,
                        't2w': has_t2w,
                        'byMethod': by_method,
                    }
                )
                for key in subj_methods:
                    subj_methods[key].append(by_method[key])
                if session is not None:
                    subj_sessions.append(session)

        # A subject's signature is the multiset of its sessions' signatures.
        subj_by_method = {}
        for key, _label, _sel in COHORT_METHODS:
            facts = subj_methods[key]
            combined = '+'.join(sorted(f['sig'] for f in facts))
            subj_by_method[key] = {
                'sig': hashlib.sha1(combined.encode(), usedforsecurity=False).hexdigest()[:8],
                'outputs': sum(f['outputs'] for f in facts),
                'runs': sum(f['runs'] for f in facts),
                'errors': sum(f['errors'] for f in facts),
                'warnings': sum(f['warnings'] for f in facts),
            }
        subject_entities.append(
            {
                'subject': subject,
                'label': subject,
                'href': subject_href,
                'sessions': subj_sessions,
                'scans': subj_scans,
                't2w': subj_has_t2w,
                'byMethod': subj_by_method,
            }
        )

    default_gran = 'session' if any(e['sessions'] for e in subject_entities) else 'subject'
    method_keys = [k for k, _l, _s in COHORT_METHODS]
    default_method = initial_method if initial_method in method_keys else method_keys[0]
    methods = [{'key': k, 'label': lbl, 'cli': sel.cli_phrase()} for k, lbl, sel in COHORT_METHODS]
    return {
        'methods': methods,
        'session': session_entities,
        'subject': subject_entities,
        'defaultGranularity': default_gran,
        'defaultMethod': default_method,
    }


def render_cohort_html(
    catalog, subjects, *, scope=None, policy=None, live=False, initial_method=None
) -> str:
    """The standalone cohort dashboard for a dataset.

    ``live`` picks the per-subject drill-down target: the served explorer
    (``/sub-<label>``) when hosted, else the sibling per-subject HTML files.
    ``initial_method`` (an HMC value like ``'shoreline'``) picks the method the
    page opens on; defaults to the first :data:`COHORT_METHODS` entry.
    """
    data = build_cohort_data(
        catalog, subjects, scope=scope, policy=policy, initial_method=initial_method
    )
    data['summary'] = {
        'subjects': len(data['subject']),
        'sessions': len(data['session']),
        'multiSession': sum(1 for e in data['subject'] if e['sessions']),
    }
    return cohort_page_html(data, live=live)
