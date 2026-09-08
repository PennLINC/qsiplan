"""Two-axis session scoping, mirroring the nipreps (fMRIPrep) session model.

fMRIPrep separates two orthogonal axes that decide how a subject's sessions map
to processing; qsiplan copies them so a plan matches what qsiprep would run:

- the **filter** (``--session-label``) chooses which sessions are considered at
  all. It is applied uniformly, in the catalog (:mod:`.catalog`), to every
  datatype - narrowing to one session narrows its anatomicals too.
- the **model** (``--subject-anatomical-reference``) chooses how the in-scope
  sessions are processed. ``sessionwise`` isolates each session into its own
  planning unit; ``first-lex`` / ``unbiased`` process the subject as a whole,
  so all in-scope sessions are visible together and per-session anatomical
  discovery can reach across sessions (the whole-subject reference).

A **session-less** anatomical or fieldmap sits at the subject level (no
``ses-`` entity) and applies to every session, so it is in scope for every unit
under every model - it is not "another session's" data. BIDS does not require
every image to carry a session, and the reference validator (bids-validator
3.0.1) accepts such a subject-level image alongside sessioned data. The
genuinely cross-session reach (one session's DWIs registered against another
session's anatomical) happens only in the whole-subject models, where
:func:`.inference.resolve_fieldmapless` warns when it fires.

For qsiplan's *grouping*, ``first-lex`` and ``unbiased`` are identical - both
mean "discover subject-wide, pick per session"; their difference (a single
reference vs an unbiased template) is a qsiprep *execution* concern carried
through for parity. Only ``sessionwise`` changes what qsiplan produces.
"""

from __future__ import annotations

import dataclasses
from typing import Literal

#: The anatomical-reference models ``--subject-anatomical-reference`` accepts,
#: copied verbatim from fMRIPrep's parser so the two tools cannot drift.
AnatModel = Literal['first-lex', 'unbiased', 'sessionwise']
ANAT_MODELS: tuple[str, ...] = ('first-lex', 'unbiased', 'sessionwise')
DEFAULT_ANAT_MODEL: AnatModel = 'first-lex'


@dataclasses.dataclass(frozen=True)
class SessionScope:
    """The two session axes for one run, as selected on the command line."""

    session_filter: tuple[str, ...] | None = None  # --session-label; None = all
    model: AnatModel = DEFAULT_ANAT_MODEL  # --subject-anatomical-reference

    @property
    def sessionwise(self) -> bool:
        """Whether each session is isolated into its own planning unit."""
        return self.model == 'sessionwise'


@dataclasses.dataclass(frozen=True)
class ProcessingUnit:
    """One planning unit: a subject and the sessions in scope for it.

    A whole-subject unit carries every in-scope session (``sessions`` is the
    full tuple, or ``None`` when the subject has no session entity at all); a
    sessionwise unit carries exactly one.
    """

    subject: str
    sessions: tuple[str, ...] | None
    sessionwise: bool = False

    @property
    def label(self) -> str:
        """The unit's page/report id: ``sub-01`` or ``sub-01_ses-02``."""
        if self.sessionwise and self.sessions:
            return f'sub-{self.subject}_ses-{self.sessions[0]}'
        return f'sub-{self.subject}'

    @property
    def session(self) -> str | None:
        """The lone session of a sessionwise unit (for display), else ``None``.

        A whole-subject unit spans its sessions internally, so it has no single
        session to name in a page title.
        """
        return self.sessions[0] if self.sessionwise and self.sessions else None


def _session_slice(records, session):
    """One session's records plus the subject's shared session-less anat/fmap.

    A session-less anatomical or fieldmap applies to every session, so it joins
    each sessionwise unit; a session-less DWI (unusual) is not carried across.
    """
    return [
        record
        for record in records
        if record.session == session or (record.session is None and not record.is_dwi)
    ]


def plan_units(subject: str, records, model: str = DEFAULT_ANAT_MODEL):
    """Partition one subject's in-scope records into planning units.

    ``records`` are already narrowed by the session *filter* (in the catalog);
    this applies the *model*. ``sessionwise`` yields one unit per DWI session -
    each carrying that session's records plus the subject's shared session-less
    anat/fmap - and every other model yields a single whole-subject unit with
    all in-scope sessions visible together. Returns ``(unit, unit_records)``
    pairs, in session order.
    """
    dwi_sessions = sorted(
        {record.session for record in records if record.is_dwi and record.session is not None}
    )
    if model == 'sessionwise' and dwi_sessions:
        return [
            (
                ProcessingUnit(subject, (session,), sessionwise=True),
                _session_slice(records, session),
            )
            for session in dwi_sessions
        ]
    return [(ProcessingUnit(subject, tuple(dwi_sessions) or None), list(records))]
