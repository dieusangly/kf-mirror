"""Synthetic frozen drafts exercise archive integrity without touching the baseline."""
import hashlib
import json
import zipfile

import pytest

from knowledge_foundry.provenance import ProvenanceArchive
from knowledge_foundry.reasoning.adapter import content_digest
from knowledge_foundry.structural_chunks import stable_hash
from scripts.setup_reasoning_assistant import LABEL, load_draft_batch


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value).encode()


@pytest.fixture
def frozen(tmp_path):
    draft = tmp_path / 'synthetic-run/formalization-v1'
    draft.mkdir(parents=True)
    bundle = {'claims': [{'id': 'synthetic', 'grounding_review_status': 'draft'}]}
    mapping = {'bundle_sha256': stable_hash(bundle), 'mappings': [
        {'id': 'synthetic-mapping', 'facts': [['urn:subject', 'urn:predicate', 'urn:object']],
         'conditions': [], 'exceptions': [], 'rules': []}]}
    mapping['review'] = {'status': 'draft', 'content_sha256': content_digest(mapping)}
    files = {'input-bundle.json': encoded(bundle), 'vocabulary.json': encoded({'terms': []}),
             'batches/batch-001.json': encoded(mapping)}
    for name, data in files.items():
        path = draft / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
    manifest = encoded({'status': 'generation_complete_draft_only',
                        'bundle_stable_hash': stable_hash(bundle),
                        'output_file_sha256': {name: digest(data) for name, data in files.items()}})
    (draft / 'manifest.json').write_bytes(manifest)
    findings = b'{"id":"synthetic-finding","status":"unreviewed"}\n'
    review = encoded({'frozen_manifest_sha256': digest(manifest),
                      'file_sha256': {'findings.jsonl': digest(findings)}})
    original = 'artifacts/synthetic-run/formalization-v1'
    archived = {original + '/manifest.json': manifest,
                original + '-review/review-manifest.json': review,
                original + '-review/findings.jsonl': findings}
    package = draft.with_name('formalization-v2')
    (package / 'provenance').mkdir(parents=True)
    return draft, package, archived, original, digest(findings)


def write_archive(package, files, *, defect=None):
    index = {'archive': 'history.zip', 'repository_prefix': 'repository/', 'files': {
        key: {'archive_member': 'repository/' + key, 'sha256': digest(data), 'bytes': len(data)}
        for key, data in files.items()}}
    findings_key = next(k for k in files if k.endswith('/findings.jsonl'))
    if defect == 'identity':
        index['files'][findings_key]['archive_member'] = 'repository/other/findings.jsonl'
    if defect == 'traversal':
        index['files'][findings_key]['archive_member'] = '../findings.jsonl'
    with zipfile.ZipFile(package / 'provenance/history.zip', 'w') as z:
        for key, data in files.items():
            member = index['files'][key]['archive_member']
            z.writestr(member, b'changed' if defect == 'member-hash' and key == findings_key else data)
        if defect == 'duplicate':
            with pytest.warns(UserWarning, match='Duplicate name'):
                z.writestr('repository/' + findings_key, files[findings_key])
    (package / 'provenance/index.json').write_bytes(encoded(index))
    manifest = {'input_file_sha256': {key: digest(data) for key, data in files.items()},
                'output_file_sha256': {name: digest((package / name).read_bytes()) for name in
                                       ['provenance/history.zip', 'provenance/index.json']}}
    if defect == 'input-pin':
        manifest['input_file_sha256'][findings_key] = '0' * 64
    (package / 'manifest.json').write_bytes(encoded(manifest))
    (package / 'seal.json').write_bytes(encoded({'manifest_sha256': digest(encoded(manifest))}))
    if defect in {'archive-hash', 'index-hash', 'seal'}:
        path = package / {'archive-hash': 'provenance/history.zip',
                          'index-hash': 'provenance/index.json', 'seal': 'manifest.json'}[defect]
        path.write_bytes(path.read_bytes() + b' ')


def test_absent_loose_review_loads_exact_archived_findings(frozen):
    draft, package, files, original, findings_hash = frozen
    write_archive(package, files)
    assert not draft.with_name('formalization-v1-review').exists()
    result = load_draft_batch(draft, 'batch-001.json')
    assert result['experiment']['findings_sha256'] == findings_hash
    assert result['experiment']['findings_file'].endswith('history.zip!/repository/' + original + '-review/findings.jsonl')
    assert result['experiment']['label'] == LABEL
    assert result['trust'] == 'experimental_unreviewed'
    assert result['full_mapping']['review']['status'] == 'draft'


@pytest.mark.parametrize('defect, message', [
    ('member-hash', 'member hash mismatch'), ('identity', 'member identity mismatch'),
    ('traversal', 'member identity mismatch'), ('duplicate', 'duplicate provenance'),
    ('input-pin', 'historical input hash mismatch'), ('archive-hash', 'archive hash mismatch'),
    ('index-hash', 'index hash mismatch'), ('seal', 'manifest seal mismatch'),
])
def test_corrupt_or_mismatched_archive_rejected(frozen, defect, message):
    draft, package, files, _, _ = frozen
    write_archive(package, files, defect=defect)
    with pytest.raises(ValueError, match=message):
        load_draft_batch(draft, 'batch-001.json')


@pytest.mark.parametrize('defect', ['draft', 'review-draft', 'review-findings', 'missing-findings'])
def test_archive_must_bind_this_draft_and_its_review(frozen, defect):
    draft, package, files, original, _ = frozen
    review_key = original + '-review/review-manifest.json'
    if defect == 'draft':
        files[original + '/manifest.json'] += b' '
    elif defect == 'missing-findings':
        del files[original + '-review/findings.jsonl']
        # Keep a different indexed findings file to test absence of the requested identity.
        files['artifacts/other/findings.jsonl'] = b'{}'
    else:
        review = json.loads(files[review_key])
        if defect == 'review-draft':
            review['frozen_manifest_sha256'] = '0' * 64
        else:
            review['file_sha256']['findings.jsonl'] = '0' * 64
        files[review_key] = encoded(review)
    write_archive(package, files)
    with pytest.raises((ValueError, KeyError)):
        load_draft_batch(draft, 'batch-001.json')


def test_legacy_loose_review_and_failure_without_required_provenance(frozen):
    draft, _, files, original, findings_hash = frozen
    review_dir = draft.with_name('formalization-v1-review')
    review_dir.mkdir()
    for name in ['review-manifest.json', 'findings.jsonl']:
        (review_dir / name).write_bytes(files[original + '-review/' + name])
    result = load_draft_batch(draft, 'batch-001.json')
    assert result['experiment']['findings_sha256'] == findings_hash
    assert result['experiment']['label'] == LABEL
    assert result['experiment']['findings_file'] == str(review_dir / 'findings.jsonl')
    (review_dir / 'findings.jsonl').write_bytes(b'changed')
    with pytest.raises(ValueError, match='findings hash mismatch'):
        load_draft_batch(draft, 'batch-001.json')
    (review_dir / 'findings.jsonl').unlink()
    with pytest.raises(FileNotFoundError):
        load_draft_batch(draft, 'batch-001.json')


def test_no_review_and_no_archive_fails_closed(frozen):
    with pytest.raises(FileNotFoundError):
        load_draft_batch(frozen[0], 'batch-001.json')


@pytest.mark.parametrize('name', ['../findings.jsonl', '/findings.jsonl', 'a/../b', 'a\\b', 'a//b'])
def test_reader_rejects_noncanonical_repository_paths(frozen, name):
    _, package, files, _, _ = frozen
    write_archive(package, files)
    with ProvenanceArchive(package) as archive, pytest.raises(ValueError, match='repository path'):
        archive.read(name)
