# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
#
# Copied recent function write_bidsignore
#
# Copyright The NiPreps Developers <nipreps@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# We support and encourage derived works from this project, please read
# about our expectations at
#
#     https://www.nipreps.org/community/licensing/
#
"""
Utilities to handle BIDS inputs
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Fetch some test data

    >>> import os
    >>> from niworkflows import data
    >>> data_root = data.get_bids_examples(variant='BIDS-examples-1-enh-ds054')
    >>> os.chdir(data_root)

"""

import itertools
import os
import re
from pathlib import Path

#: The characters a BIDS entity *label* may contain. The BIDS spec defines a
#: label as "an alphanumeric (and possibly including ``+`` character(s)) value",
#: so ``+`` is legal today (e.g. template/cohort labels like ``MNIInfant+1``).
#: Keep this the single definition of the grammar: widening it later (BIDS may
#: admit more characters over time) is then a one-line change, not a hunt
#: through ad-hoc regexes. It deliberately does *not* validate the surrounding
#: ``key-`` structure - :func:`_parse_bids_name` handles that, grammar-agnostically.
BIDS_LABEL = r'[0-9A-Za-z+]+'


def is_bids_label(value: str) -> bool:
    """Whether ``value`` is a valid BIDS entity label (alphanumeric plus ``+``)."""
    return re.fullmatch(BIDS_LABEL, value) is not None


def _norm(path):
    """Absolute, lexically-normalized path that does *not* follow symlinks.

    ``Path.resolve()`` follows symlinks, which moves a git-annex/datalad data
    file (a symlink into ``.git/annex/objects``) out of its BIDS directory, so
    its JSON/bval/bvec sidecars -- which sit beside the *symlink*, not the
    annex object -- can no longer be found. Lexical normalization with
    :func:`os.path.abspath` keeps the file at its BIDS location while still
    resolving ``.`` and ``..``.
    """
    return Path(os.path.abspath(path))


def find_bids_root(path):
    """Locate the root of the BIDS dataset containing ``path``.

    Parameters
    ----------
    path : :obj:`str` or :obj:`pathlib.Path`
        A file inside a BIDS dataset.

    Returns
    -------
    :obj:`pathlib.Path` or None
        The closest ancestor directory holding a ``dataset_description.json``,
        or ``None`` if ``path`` is not inside a BIDS dataset.
    """
    for parent in _norm(path).parents:
        if (parent / 'dataset_description.json').is_file():
            return parent

    return None


def _parse_bids_name(path):
    """Split a BIDS filename into its entities, suffix and extension.

    Parameters
    ----------
    path : :obj:`str` or :obj:`pathlib.Path`
        A BIDS-style filename. It need not exist.

    Returns
    -------
    entities : :obj:`dict`
        Mapping of entity key to entity value, e.g. ``{'sub': '01', 'part': 'mag'}``.
    suffix : :obj:`str` or None
        The BIDS suffix, e.g. ``'dwi'``. ``None`` if the name carries no suffix.
    extension : :obj:`str`
        Everything from the first period of the filename onward, e.g. ``'.nii.gz'``.
    """
    name = Path(path).name
    stem, _, remainder = name.partition('.')
    extension = f'.{remainder}' if remainder else ''

    entities = {}
    suffix = None
    for chunk in stem.split('_'):
        key, sep, value = chunk.partition('-')
        if sep:
            entities[key] = value
        else:
            # The only chunk without a "-" is the suffix, which comes last.
            suffix = chunk

    return entities, suffix, extension


def parse_file_entities(path):
    """Parse the BIDS entities QSIPlan uses without constructing a layout.

    Entity keys match PyBIDS' public spelling for the common entities so this
    can replace ``bids.layout.parse_file_entities`` at the QSIPlan boundary.
    Unknown entities are retained under their short filename spelling.
    """
    path = Path(path)
    entities, suffix, extension = _parse_bids_name(path)
    names = {
        'sub': 'subject',
        'ses': 'session',
        'acq': 'acquisition',
        'dir': 'direction',
        'rec': 'reconstruction',
    }
    parsed = {names.get(key, key): value for key, value in entities.items()}
    if suffix:
        parsed['suffix'] = suffix
    if extension:
        parsed['extension'] = extension

    parts = path.parts
    for index, part in enumerate(parts):
        if not part.startswith('sub-'):
            continue
        datatype_index = index + 1
        if datatype_index < len(parts) and parts[datatype_index].startswith('ses-'):
            datatype_index += 1
        if datatype_index < len(parts) - 1:
            parsed['datatype'] = parts[datatype_index]
        break
    return parsed


def _inheritance_levels(path, root=None):
    """List the directories that may hold files applicable to ``path``.

    The BIDS inheritance principle lets files sitting in a data file's ancestor
    directories apply to it. Directories are returned shallowest-first, so that
    values from more specific files can overwrite less specific ones.

    Files that are not inside a BIDS dataset -- for instance, images that have
    already been copied into a working directory -- only ever match files
    sitting beside them.
    """
    path = _norm(path)
    root = _norm(root) if root is not None else find_bids_root(path)
    if root is None:
        return [path.parent]
    if root != path.parent and root not in path.parents:
        return [path.parent]

    # ``reversed(path.parents)`` runs shallowest-first and ends at ``path.parent``.
    return [root] + [parent for parent in reversed(path.parents) if root in parent.parents]


def find_associated_files(path, extension):
    """Find the files that apply to ``path`` under the BIDS inheritance principle.

    A file applies to ``path`` when it has the same suffix, the requested
    extension, and a set of entities that is a subset of ``path``'s entities
    with identical values. For example, ``sub-01_dwi.bval`` applies to both
    ``sub-01_part-mag_dwi.nii.gz`` and ``sub-01_part-phase_dwi.nii.gz``, while
    ``sub-01_part-mag_dwi.bval`` applies to neither of the other two.

    Parameters
    ----------
    path : :obj:`str` or :obj:`pathlib.Path`
        The data file whose associated files are wanted.
    extension : :obj:`str`
        The extension to look for, including the leading period, e.g. ``'.json'``.

    Returns
    -------
    :obj:`list` of :obj:`pathlib.Path`
        Applicable files ordered from the dataset root down to ``path``'s own
        directory, so the last element is the most specific one.

    Raises
    ------
    ValueError
        If more than one applicable file is found in a single directory, which
        the BIDS specification forbids.
    """
    target_entities, target_suffix, _ = _parse_bids_name(path)

    associated_files = []
    for level in _inheritance_levels(path):
        if not level.is_dir():
            continue

        matches = []
        for candidate in sorted(level.iterdir()):
            if not candidate.is_file():
                continue

            entities, suffix, candidate_extension = _parse_bids_name(candidate)
            if candidate_extension != extension or suffix != target_suffix:
                continue

            if all(target_entities.get(key) == value for key, value in entities.items()):
                matches.append(candidate)

        if len(matches) > 1:
            raise ValueError(
                f'Multiple {extension} files in {level} apply to {path}: '
                f'{", ".join(match.name for match in matches)}. '
                'The BIDS inheritance principle allows at most one per directory.'
            )

        associated_files.extend(matches)

    return associated_files


class BIDSInheritanceIndex:
    """Resolve associated files for many targets with one directory scan.

    The former per-file implementation rescanned a large ``dwi/`` directory
    for every image. This index scans each inheritance level once and resolves
    candidate entity subsets through dictionary lookups.
    """

    def __init__(self, targets, extensions=('.json', '.bval', '.bvec'), root=None):
        self._extensions = frozenset(extensions)
        self._root = _norm(root) if root is not None else None
        self._levels = {
            str(_norm(target)): tuple(_inheritance_levels(target, self._root))
            for target in targets
        }
        unique_levels = {level for levels in self._levels.values() for level in levels}
        self._candidates = {}
        for level in unique_levels:
            if not level.is_dir():
                continue
            for candidate in level.iterdir():
                if not candidate.is_file():
                    continue
                entities, suffix, extension = _parse_bids_name(candidate)
                if extension not in self._extensions or not suffix:
                    continue
                key = (level, extension, suffix, frozenset(entities.items()))
                self._candidates.setdefault(key, []).append(candidate)

    def find(self, path, extension):
        """Applicable files, shallowest to most specific, for ``path``."""
        path = str(_norm(path))
        target_entities, target_suffix, _ = _parse_bids_name(path)
        entity_items = tuple(target_entities.items())
        entity_subsets = tuple(
            frozenset(subset)
            for size in range(len(entity_items) + 1)
            for subset in itertools.combinations(entity_items, size)
        )
        associated = []
        levels = self._levels.get(path)
        if levels is None:
            levels = _inheritance_levels(path, self._root)
        for level in levels:
            matches = []
            for subset in entity_subsets:
                matches.extend(self._candidates.get((level, extension, target_suffix, subset), ()))
            if len(matches) > 1:
                names = ', '.join(sorted(match.name for match in matches))
                raise ValueError(
                    f'Multiple {extension} files in {level} apply to {path}: {names}. '
                    'The BIDS inheritance principle allows at most one per directory.'
                )
            associated.extend(matches)
        return associated


def find_bval(path):
    """Find the b-value file that applies to a BIDS file.

    Parameters
    ----------
    path : :obj:`str` or :obj:`pathlib.Path`
        The data file whose b-values are wanted.

    Returns
    -------
    :obj:`str` or None
        Path to the most specific applicable ``.bval`` file, or ``None`` if
        there is not one.
    """
    bval_files = find_associated_files(path, '.bval')

    return str(bval_files[-1]) if bval_files else None


def find_bvec(path):
    """Find the b-vector file that applies to a BIDS file.

    Parameters
    ----------
    path : :obj:`str` or :obj:`pathlib.Path`
        The data file whose b-vectors are wanted.

    Returns
    -------
    :obj:`str` or None
        Path to the most specific applicable ``.bvec`` file, or ``None`` if
        there is not one.
    """
    bvec_files = find_associated_files(path, '.bvec')

    return str(bvec_files[-1]) if bvec_files else None
