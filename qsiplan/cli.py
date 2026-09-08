"""Preview how qsiprep would group and process a BIDS dataset's DWI scans.

Usage::

    qsiplan /path/to/bids [--participant-label 01 02] \\
        [--ignore fieldmaps shims fov] [--separate-all-dwis] \\
        [--hmc-method eddy|shoreline|tortoise] [--sdc-method auto|topup|...]

Prints, per subject, the grouping decisions (with curated/inferred
provenance) and a plain-language preview of what the selected processing
methods would do with the data - or, with no method flags, every default
method combination. Nothing is processed and nothing is written.
"""

import argparse
import sys
from functools import partial
from pathlib import Path

from qsiplan import (
    describe_processing,
    index_subject,
    render_explorer_html,
    report_text,
)
from qsiplan.catalog import Bids2TableCatalog
from qsiplan.cli_spec import (
    add_plan_arguments,
    policy_from_namespace,
    scope_from_namespace,
    selection_from_namespace,
)
from qsiplan.explorer import build_for_policy
from qsiplan.report import default_preview_selections
from qsiplan.scope import plan_units


def _path_exists(path, parser):
    """Ensure a given path exists.

    Parameters
    ----------
    path : str or None
        The path to check for existence. If None or the path does not exist, an error is raised.
    parser : argparse.ArgumentParser
        The argument parser instance used to raise an error if the path does not exist.

    Returns
    -------
    pathlib.Path
        The absolute path if it exists.

    Raises
    ------
    argparse.ArgumentError
        If the path does not exist or is None.
    """
    if path is not None:
        path = Path(path)

    if path is None or not path.exists():
        raise parser.error(f'Path does not exist: <{path.resolve()}>.')
    return path.resolve()


def _is_dir(path, parser):
    """Ensure a given path exists and is a directory."""
    path = _path_exists(path, parser)
    if not path.is_dir():
        raise parser.error(
            f'Path should point to a directory (or symlink of directory): <{path.absolute()}>.'
        )
    return str(path)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog='qsiplan',
        description=__doc__.splitlines()[0],
    )
    IsDir = partial(_is_dir, parser=parser)

    parser.add_argument(
        'bids_dir',
        type=IsDir,
        help='Root of the BIDS dataset',
    )
    parser.add_argument(
        '--participant-label',
        nargs='+',
        default=None,
        help='Subject label(s) to preview (without "sub-"). Default: all subjects.',
    )
    parser.add_argument(
        '--session-label',
        nargs='+',
        default=None,
        help='Session label(s) to include (without "ses-"). Default: all sessions. '
        'This is a filter only; --subject-anatomical-reference decides how the '
        'included sessions map to processing.',
    )
    # Every plan-relevant flag - the method axis (--hmc-method,
    # --shoreline-model, --sdc-method) and the grouping-policy axis (--ignore,
    # --force, --separate-all-dwis, --sdc-anat-reference, --distortion-group-merge) -
    # comes from the one spec qsiplan and qsiprep share, so a flag cannot be
    # spelled two ways across the split.
    add_plan_arguments(parser)
    parser.add_argument(
        '--html',
        metavar='PATH',
        help='Also write a self-contained explorer HTML page: the grouping plus '
        'live controls for every grouping-policy and processing-method flag '
        '(the CLI flags pick the initial state). With more than one subject, '
        'the subject label is inserted before the extension.',
    )
    parser.add_argument(
        '--cohort-html',
        metavar='PATH',
        help='Write a static group-level dashboard to PATH: every subject sorted '
        'into workflow equivalence classes, with a data-completeness matrix. Also '
        'writes a sub-<label>.html explorer beside it for each subject, so the '
        "dashboard's drill-down links resolve offline.",
    )
    parser.add_argument(
        '--serve',
        action='store_true',
        help='Serve the explorer live at http://<host>:<port> instead of '
        'writing files: every control change is answered by the real compiler, '
        'and flag combinations beyond the embedded grid work too. The root page '
        'is the group-level cohort dashboard.',
    )
    parser.add_argument(
        '--host',
        default='127.0.0.1',
        help='Interface --serve binds to (default: 127.0.0.1, localhost only). '
        'Use 0.0.0.0 to expose the server to your whole network - the server is '
        'unauthenticated and reads your dataset, so only do this on a trusted network.',
    )
    parser.add_argument(
        '--port',
        type=int,
        default=8765,
        help='Port for --serve (default: 8765).',
    )
    return parser


def _per_unit_path(path: str, unit, multi: bool) -> str:
    """Insert the unit label (``sub-01`` / ``sub-01_ses-02``) when writing many."""
    if not multi:
        return path
    base, dot, ext = path.rpartition('.')
    stem = base if dot else path
    suffix = f'.{ext}' if dot else ''
    return f'{stem}_{unit.label}{suffix}'


def _selections(args):
    """The method selections to preview, from the parsed arguments.

    One selection when ``--hmc-method`` names it (the shared spec's bridge),
    otherwise every default combination.
    """
    try:
        selection = selection_from_namespace(args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    if selection is not None:
        return [selection]
    return list(default_preview_selections())


def main(argv=None):
    args = _build_parser().parse_args(argv)
    selections = _selections(args)

    catalog = Bids2TableCatalog(args.bids_dir)
    subjects = args.participant_label or catalog.subjects()
    if not subjects:
        print(f'No subjects found in {args.bids_dir}', file=sys.stderr)
        return 1

    policy = policy_from_namespace(args)
    scope = scope_from_namespace(args)
    initial_selection = selections[0] if args.hmc_method else None

    if args.serve:
        if args.html or args.cohort_html:
            raise SystemExit('--serve cannot be combined with --html or --cohort-html')
        from qsiplan.serve import ExplorerApp, run_server

        app = ExplorerApp(
            catalog,
            subjects,
            scope=scope,
            base_policy=policy,
            initial_selection=initial_selection,
        )
        return run_server(app, host=args.host, port=args.port)

    if args.cohort_html:
        from qsiplan.cohort import render_cohort_html

        out = Path(args.cohort_html)
        out.write_text(
            render_cohort_html(
                catalog,
                subjects,
                scope=scope,
                policy=policy,
                live=False,
                initial_method=args.hmc_method,
            )
        )
        print(f'wrote {out}')
        # Sibling per-unit explorer pages so the dashboard's static drill-down
        # links resolve without a server: sub-<label>.html subject-wide, one
        # sub-<label>_ses-<ses>.html per session under sessionwise.
        for subject, subject_data in catalog.iter_subject_data(subjects, scope.session_filter):
            if not subject_data['dwi']:
                continue
            records, index_issues = index_subject(catalog, subject_data)
            for unit, unit_records in plan_units(subject, records, scope.model):
                sibling = out.parent / f'{unit.label}.html'
                sibling.write_text(
                    render_explorer_html(
                        unit_records,
                        subject,
                        session=unit.session,
                        index_issues=index_issues,
                        initial_policy=policy,
                        initial_selection=initial_selection,
                    )
                )
                print(f'  {unit.label}: wrote {sibling.name}')
        return 0

    # More than one output page means the filename must carry the unit label:
    # several subjects, or sessionwise (one page per session of a subject).
    multi = len(subjects) > 1 or scope.sessionwise
    exit_code = 0
    for subject, subject_data in catalog.iter_subject_data(subjects, scope.session_filter):
        if not subject_data['dwi']:
            print(f'sub-{subject}: no DWI files found, skipping.\n')
            continue

        # One fieldmaps-included index pass per subject; plan_units then applies
        # the anatomical-reference model (subject-wide, or one unit per session).
        records, index_issues = index_subject(catalog, subject_data)
        for unit, unit_records in plan_units(subject, records, scope.model):
            grouping = build_for_policy(unit_records, subject, policy, index_issues)
            if unit.sessionwise:
                print(f'=== {unit.label} (session-wise) ===')
            print(report_text(grouping))
            for selection in selections:
                print(describe_processing(grouping, selection))
            if args.html:
                path = _per_unit_path(args.html, unit, multi)
                page = render_explorer_html(
                    unit_records,
                    subject,
                    session=unit.session,
                    index_issues=index_issues,
                    initial_policy=policy,
                    initial_selection=initial_selection,
                )
                with open(path, 'w') as fobj:
                    fobj.write(page)
                print(f'{unit.label}: wrote {path}')
            if grouping.errors:
                exit_code = 1

    return exit_code


if __name__ == '__main__':
    sys.exit(main())
