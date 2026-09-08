"""Fast, subject-scoped BIDS catalog and inheritance indexing."""

import json
import os
from pathlib import Path

from qsiplan import index_subject
from qsiplan import metadata as metadata_module
from qsiplan.bids import BIDSInheritanceIndex
from qsiplan.catalog import Bids2TableCatalog


def _dataset(tmp_path, subjects=('01',), runs=1):
    root = tmp_path / 'bids'
    root.mkdir()
    (root / 'dataset_description.json').write_text(
        json.dumps({'Name': 'catalog-test', 'BIDSVersion': '1.10.0'})
    )
    (root / 'dwi.json').write_text(json.dumps({'TotalReadoutTime': 0.05}))
    for subject in subjects:
        for session in ('01', '02'):
            dwi = root / f'sub-{subject}' / f'ses-{session}' / 'dwi'
            fmap = dwi.parent / 'fmap'
            anat = dwi.parent / 'anat'
            dwi.mkdir(parents=True)
            fmap.mkdir()
            anat.mkdir()
            for run in range(1, runs + 1):
                stem = f'sub-{subject}_ses-{session}_run-{run:03d}_dwi'
                (dwi / f'{stem}.nii.gz').touch()
                (dwi / f'{stem}.bval').write_text('0 1000\n')
                (dwi / f'{stem}.bvec').write_text('0 1\n0 0\n0 0\n')
                (dwi / f'{stem}.json').write_text(json.dumps({'PhaseEncodingDirection': 'j-'}))
            fmap_stem = f'sub-{subject}_ses-{session}_dir-PA_epi'
            (fmap / f'{fmap_stem}.nii.gz').touch()
            (fmap / f'{fmap_stem}.json').write_text(json.dumps({'PhaseEncodingDirection': 'j'}))
            (anat / f'sub-{subject}_ses-{session}_T1w.nii.gz').touch()
    return root


def test_catalog_indexes_only_requested_subject_and_session(tmp_path):
    root = _dataset(tmp_path, subjects=('01', '02'))
    catalog = Bids2TableCatalog(root)
    assert catalog.subjects() == ['01', '02']

    data = catalog.subject_data('02', session_filter=('01',))
    assert len(data['dwi']) == 1
    assert len(data['fmap']) == 1
    assert len(data['t1w']) == 1
    assert data['t2w'] == []
    assert all('/sub-02/ses-01/' in path for paths in data.values() for path in paths)

    streamed = dict(catalog.iter_subject_data(['02', '01'], session_filter=('02',), batch_size=1))
    assert list(streamed) == ['02', '01']
    assert all(len(data['dwi']) == 1 for data in streamed.values())


def test_catalog_enumeration_accepts_plus_in_labels(tmp_path):
    """BIDS labels may contain '+' (e.g. template/cohort labels), so the
    directory scan must keep them - while still rejecting non-label names."""
    root = tmp_path / 'bids'
    (root / 'sub-01+2' / 'ses-pre+post' / 'dwi').mkdir(parents=True)
    (root / 'sub-01+2' / 'ses-2').mkdir()
    (root / 'sub-bad_x').mkdir()  # '_' is not a legal label char -> excluded
    catalog = Bids2TableCatalog(root)
    assert catalog.subjects() == ['01+2']  # the '+' label kept, 'bad_x' dropped
    assert catalog.sessions('01+2') == ['2', 'pre+post']


def test_catalog_metadata_inheritance_is_strict(tmp_path):
    root = _dataset(tmp_path)
    catalog = Bids2TableCatalog(root)
    data = catalog.subject_data('01', session_filter=('01',))
    records, issues = index_subject(catalog, data)
    dwi = next(record for record in records if record.is_dwi)
    assert dwi.signature.readout_time == 0.05
    assert dwi.signature.pe_dir == 'j-'
    assert not issues

    sidecar = next((root / 'sub-01/ses-01/dwi').glob('*.json'))
    sidecar.write_text('{not valid JSON')
    _records, issues = index_subject(catalog, data)
    assert any(issue.code == 'invalid-json-sidecar' for issue in issues)


def test_annex_symlinked_data_files_still_resolve_sidecars(tmp_path):
    """Datalad/git-annex data files are symlinks into ``.git/annex/objects``.

    Sidecars (``.json``/``.bval``/``.bvec``) sit beside the *symlink*, not the
    annex object, so inheritance resolution must not follow the symlink out of
    the BIDS tree. Regression test for datalad-cloned datasets reading as
    "PE unknown".
    """
    root = _dataset(tmp_path)
    # Emulate git-annex: move each data NIfTI into an object store outside the
    # BIDS hierarchy and replace it in place with a relative symlink, exactly
    # as `datalad get` leaves them.
    store = tmp_path / 'annex_objects'
    store.mkdir()
    for index, nifti in enumerate(sorted(root.rglob('*.nii.gz'))):
        target = store / f'OBJ-{index}.nii.gz'
        target.write_bytes(nifti.read_bytes())
        nifti.unlink()
        nifti.symlink_to(os.path.relpath(target, nifti.parent))

    dwi_symlink = next((root / 'sub-01/ses-01/dwi').glob('*_dwi.nii.gz'))
    assert dwi_symlink.is_symlink()
    assert dwi_symlink.resolve().parent == store  # resolve() escapes the BIDS tree

    catalog = Bids2TableCatalog(root)
    data = catalog.subject_data('01', session_filter=('01',))
    records, issues = index_subject(catalog, data)
    dwi = next(record for record in records if record.is_dwi)
    assert dwi.signature.pe_dir == 'j-'  # from the sidecar beside the symlink
    assert dwi.signature.readout_time == 0.05  # inherited from root/dwi.json
    assert dwi.max_bval == 1000.0  # from the sibling .bval
    assert not issues


def test_catalog_root_controls_inheritance_when_description_is_missing(tmp_path):
    root = _dataset(tmp_path)
    (root / 'dataset_description.json').unlink()
    catalog = Bids2TableCatalog(root)
    data = catalog.subject_data('01', session_filter=('01',))
    records, _issues = index_subject(catalog, data)
    dwi = next(record for record in records if record.is_dwi)
    assert dwi.signature.readout_time == 0.05


def test_inheritance_directories_are_scanned_once(tmp_path, monkeypatch):
    root = _dataset(tmp_path, runs=100)
    targets = sorted((root / 'sub-01/ses-01/dwi').glob('*.nii.gz'))
    original = Path.iterdir
    calls = 0

    def counted(path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(Path, 'iterdir', counted)
    index = BIDSInheritanceIndex(targets)
    scans_after_build = calls
    for target in targets:
        assert index.find(target, '.bval')
        assert index.find(target, '.bvec')

    assert scans_after_build == 4  # dataset, subject, session, datatype
    assert calls == scans_after_build


def test_known_missing_gradient_does_not_fall_back_to_directory_scan(monkeypatch):
    def unexpected(_path):
        raise AssertionError('find_bval should not be called for an indexed missing sidecar')

    monkeypatch.setattr(metadata_module, 'find_bval', unexpected)
    assert metadata_module._read_gradients('/missing.nii.gz', bval_file=None) == (None, (), None)
