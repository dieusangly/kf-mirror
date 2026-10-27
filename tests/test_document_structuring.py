"""Boundary and lifecycle regressions using real local artifacts, without model calls."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from knowledge_foundry.ingestion import ingest, load_ingested
from knowledge_foundry.parsing import parse_collection
from knowledge_foundry.chunking import build_collection, _chunk_document, token_count
from knowledge_foundry.document_structuring import (
    accept_structure, audit_structure, load_validated_structure, validate_frozen,
)
from knowledge_foundry.structure_providers import ProviderResult
from knowledge_foundry.claim_context import prepare_claim_context
from claim_fixtures import fixture_settings


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    return path


@pytest.fixture
def collection(tmp_path):
    entries = []
    for mid, content in [('one', '# Equipment\n\nRequired checks:\n\n- Inspect the sensor.\n- Retain the log.\n'),
                         ('two', '# Records\n\nKeep records for five years.\n')]:
        raw = content.encode()
        (tmp_path / (mid + '.md')).write_bytes(raw)
        entries.append({'manifest_id': mid, 'source_id': 'src:' + mid, 'source_version': 'v1',
                        'title': mid, 'class': 'test', 'media_type': 'text/markdown',
                        'local_path': mid + '.md', 'retrieved_on': '2026-10-27',
                        'publisher_url': 'https://example.org/' + mid, 'size_bytes': len(raw),
                        'sha256': hashlib.sha256(raw).hexdigest()})
    write(tmp_path / 'manifest.json', {'schema_version': '1.0.0', 'sources': entries})
    config = write(tmp_path / 'pipeline.json', {'root': '.', 'manifest_file': 'manifest.json',
                    'manifest_ids': ['one', 'two'], 'ingested_dir': 'ingested',
                    'structuring': {'provider': 'internal', 'options': {'ocr': {'mode': 'off'}}},
                    'chunking': {'max_tokens': 32}})
    artifact = ingest(config, tmp_path / 'ingested')
    return tmp_path, config, artifact


def test_repairs_work_after_originals_and_manifest_are_removed(collection):
    root, config, artifact = collection
    for file in ['one.md', 'two.md', 'manifest.json']:
        (root / file).unlink()
    before = {p.name: p.read_bytes() for p in (root / 'ingested/objects').iterdir()}
    with patch('knowledge_foundry.ingestion.ingest', side_effect=AssertionError('must not reacquire sources')):
        result = parse_collection(config, root / 'parsed')
        assert all(r['status'] == 'parsed' for r in result['outcomes'])
        build_collection(config, root / 'parsed', root / 'chunks')
        changed = json.loads(config.read_text())
        changed['chunking']['max_tokens'] = 64
        write(config, changed)
        build_collection(config, root / 'parsed', root / 'rechunked')
    assert before == {p.name: p.read_bytes() for p in (root / 'ingested/objects').iterdir()}
    assert result['ingested_artifact_id'] == artifact['artifact_id']
    assert audit_structure(json.loads((root / 'chunks/artifact-bundle.json').read_text()),
                           json.loads((root / 'chunks/structure-metadata.json').read_text()))['gate'] == 'pass'


def test_ingestion_reuses_bytes_and_preserves_immutable_snapshots(collection):
    root, config, first = collection
    snapshot = root / 'ingested/snapshots' / (first['artifact_id'].split(':')[1] + '.json')
    original = snapshot.read_bytes()
    objects = {p: p.stat().st_mtime_ns for p in (root / 'ingested/objects').iterdir()}
    assert ingest(config, root / 'ingested') == first
    assert {p: p.stat().st_mtime_ns for p in objects} == objects
    assert snapshot.read_bytes() == original
    value = json.loads(config.read_text()); value['manifest_ids'] = ['two']; write(config, value)
    second = ingest(config, root / 'ingested')
    assert second['artifact_id'] != first['artifact_id']
    assert len(load_ingested(snapshot)['outcomes']) == 2
    object_path = next(iter(objects))
    object_path.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='integrity'):
        load_ingested(snapshot)


def test_subset_reparse_preserves_other_document_and_ingestion(collection):
    root, config, _ = collection
    first = parse_collection(config, root / 'parsed')
    untouched = Path(first['outcomes'][1]['output']) / 'artifact-bundle.json'
    before = untouched.read_bytes()
    second = parse_collection(config, root / 'parsed', ['one'])
    assert [r['manifest_id'] for r in second['outcomes']] == ['one', 'two']
    assert untouched.read_bytes() == before
    build_collection(config, root / 'parsed', root / 'chunks')


def test_chunks_only_provider_skips_partitioning_and_internal_rechunking(collection, monkeypatch):
    root, config, _ = collection
    class Provider:
        version = 'test-provider-v1'
        def structure(self, artifact, options):
            text = Path(artifact['artifact_file']).read_text()
            return ProviderResult([], chunks=[{'text': text, 'kind': 'paragraph',
                'location': {'format': 'markdown', 'heading_path': [], 'line_start': 1, 'line_end': len(text.splitlines())},
                'details': {'confidence': .93, 'layout': {'column': 1}}}], native={'provider': 'test'})
    monkeypatch.setitem(sys.modules, 'test_structure_plugin', SimpleNamespace(Provider=Provider))
    value = json.loads(config.read_text())
    value['structuring'] = {'provider': 'test_structure_plugin:Provider', 'options': {}}
    write(config, value)
    parsed = parse_collection(config, root / 'parsed')
    assert all(r['status'] == 'parsed' for r in parsed['outcomes']), parsed
    build_collection(config, root / 'parsed', root / 'chunks')
    bundle = json.loads((root / 'chunks/artifact-bundle.json').read_text())
    assert len(bundle['chunks']) == 2
    assert all(c['chunker_version'].startswith('provider-') for c in bundle['chunks'])
    final = [json.loads(line) for line in (root / 'chunks/structural-chunks.jsonl').read_text().splitlines()]
    assert len(final) == 2 and all(len(c['original_chunk_ids']) == 1 for c in final)
    meta = json.loads((root / 'chunks/structure-metadata.json').read_text())
    assert meta[0]['elements'][0]['confidence'] == .93
    assert meta[0]['elements'][0]['layout'] == {'column': 1}
    from knowledge_foundry.structural_chunks import stable_hash
    from knowledge_foundry.claim_context import load_preparation
    import shutil
    accept_structure(root / 'chunks', test_only=True)
    models = write(root / 'models.json', fixture_settings())
    vectors = write(root / 'vectors.json', {'embedding_vectors': {stable_hash(c['text']): [1., 0.] for c in final}})
    prepare_claim_context(root / 'chunks', root / 'semantic', models, root / 'cache', vectors)
    assert len(list((root / 'semantic').glob('documents/*/provider.json'))) == 2
    shutil.rmtree(root / 'parsed')
    assert len(load_preparation(root / 'semantic')['structural_chunks']) == 2


def test_complete_table_and_list_can_exceed_packing_target(collection):
    root, config, _ = collection
    (root / 'one.md').write_text('# Controls\n\nRequired checks:\n\n' +
        '\n'.join('- Inspect sensor ' + str(i) + ' and retain the complete audit record.' for i in range(20)) +
        '\n\n| Region | Result |\n|---|---|\n' + '\n'.join(f'| Area {i} | inspected |' for i in range(30)))
    manifest = json.loads((root / 'manifest.json').read_text())
    raw = (root / 'one.md').read_bytes()
    manifest['sources'][0].update(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    write(root / 'manifest.json', manifest); ingest(config, root / 'ingested')
    parse_collection(config, root / 'parsed'); build_collection(config, root / 'parsed', root / 'chunks')
    bundle = json.loads((root / 'chunks/artifact-bundle.json').read_text())
    elements = {e['id']: e for e in bundle['elements']}
    table = next(e for e in elements.values() if e['kind'] == 'table')
    owning = [c for c in bundle['chunks'] if any(r['element_id'] == table['id'] and 'span' in r for r in c['evidence'])]
    assert len(owning) == 1 and 'Area 29' in owning[0]['text']
    lists = [c for c in bundle['chunks'] if 'Inspect sensor 0 ' in c['text']]
    assert len(lists) == 1 and 'Inspect sensor 19 ' in lists[0]['text']
    assert token_count(owning[0]['text']) > 32 and token_count(lists[0]['text']) > 32


def test_acceptance_is_required_and_test_acceptance_cannot_use_live_models(collection):
    root, config, _ = collection
    parse_collection(config, root / 'parsed'); build_collection(config, root / 'parsed', root / 'chunks')
    with pytest.raises(ValueError, match='requires parsing'):
        accept_structure(root / 'chunks')
    cfg = write(root / 'models.json', fixture_settings())
    with pytest.raises(FileNotFoundError):
        prepare_claim_context(root / 'chunks', root / 'rejected', cfg)
    accept_structure(root / 'chunks', test_only=True)
    with pytest.raises(ValueError, match='acceptance'):
        load_validated_structure(root / 'chunks')
    assert load_validated_structure(root / 'chunks', allow_test=True)['acceptance']['status'] == 'test_only'
    with pytest.raises(ValueError, match='acceptance'):
        prepare_claim_context(root / 'chunks', root / 'live-rejected')
    path = root / 'chunks/structure-metadata.json'
    data = json.loads(path.read_text());data[0]['elements'][0]['confidence'] = .1;write(path, data)
    with pytest.raises(ValueError, match='hash mismatch'):
        validate_frozen(root / 'chunks')


def test_source_reviews_accept_exact_snapshot_and_reject_stale_or_pending_reports(collection):
    from scripts.auto_review_parsing import evaluate as audit_parsing
    from scripts.auto_review_chunking import evaluate as review_chunks
    from knowledge_foundry.parsing_review import prepare, seed
    root, config, _ = collection
    parsed, chunks = root / 'parsed', root / 'chunks'
    result = parse_collection(config, parsed)
    build_collection(config, parsed, chunks)
    policy = Path(__file__).resolve().parents[1] / 'config/review-policy.json'
    audit = audit_parsing(parsed, source_root=root)
    pending = prepare(audit, result['outcomes'], root, root / 'manifest.json', policy, count=2)
    parsing_judgments = seed(pending)
    parsing_judgments['reviewer'] = {'kind': 'human', 'name': 'Synthetic fixture reviewer'}
    for row in parsing_judgments['reviews']:
        row.update(status='pass', notes='Synthetic source inspection fixture; no real corpus approval.',
                   inspection={'original_inspected': True, 'layout_inspected': False})
    review_file = write(root / 'parsing-judgments.json', parsing_judgments)
    parsing_report = prepare(audit, result['outcomes'], root, root / 'manifest.json', policy, count=2, review_path=review_file)
    parse_report_path = write(root / 'parsing-review-report.json', parsing_report)
    chunk_pending = review_chunks(chunks, parsed, sample_count=2, manifest_path=root / 'manifest.json')
    chunk_judgments = chunk_pending['review_template']
    chunk_judgments['reviewer'] = {'kind': 'human', 'name': 'Synthetic fixture reviewer'}
    for row in chunk_judgments['reviews']:
        row.update(status='pass', notes='Synthetic chunk inspection fixture.',
                   inspection={'original_inspected': True, 'layout_inspected': False})
    chunk_review_path = write(root / 'chunk-judgments.json', chunk_judgments)
    chunk_report = review_chunks(chunks, parsed, sample_count=2, review_path=chunk_review_path,
                                 manifest_path=root / 'manifest.json')
    chunk_report_path = write(root / 'chunk-review-report.json', chunk_report)
    stale = copy.deepcopy(chunk_report);stale['structuring_snapshot_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='does not match'):
        accept_structure(chunks, parse_report_path, write(root / 'stale.json', stale))
    with pytest.raises(ValueError, match='has not passed'):
        accept_structure(chunks, write(root / 'pending.json', pending), chunk_report_path)
    acceptance = accept_structure(chunks, parse_report_path, chunk_report_path)
    assert acceptance['status'] == 'accepted'
    assert load_validated_structure(chunks)['manifest'] == validate_frozen(chunks)
    (chunks / 'chunking-review.json').write_text('{}')
    with pytest.raises(ValueError, match='review evidence changed'):
        load_validated_structure(chunks)


def test_mixed_provider_collection_can_use_internal_logic_for_one_source(collection, monkeypatch):
    root, config, _ = collection
    class Provider:
        version = 'test-provider-v1'
        def structure(self, artifact, options):
            return ProviderResult([], chunks=[{'text': Path(artifact['artifact_file']).read_text(),
                'location': {'format': 'markdown', 'heading_path': [], 'line_start': 1, 'line_end': 1}}])
    monkeypatch.setitem(sys.modules, 'mixed_structure_plugin', SimpleNamespace(Provider=Provider))
    value = json.loads(config.read_text())
    value['structuring'] = {'provider': 'mixed_structure_plugin:Provider', 'options': {},
                           'source_overrides': {'two': {'provider': 'internal', 'options': {'ocr': {'mode': 'off'}}}}}
    write(config, value)
    parsed = parse_collection(config, root / 'parsed')
    assert [row['parser'] for row in parsed['outcomes']] == ['mixed_structure_plugin:Provider', 'markdown-it-py']
    assert all(row['status'] == 'parsed' for row in parsed['outcomes'])


def test_internal_provider_honors_per_source_options(collection):
    root, config, _ = collection
    (root / 'one.md').write_text('| Sensor | Result |\n|---|---|\n| A | Passed |\n')
    manifest = json.loads((root / 'manifest.json').read_text())
    raw = (root / 'one.md').read_bytes()
    manifest['sources'][0].update(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    write(root / 'manifest.json', manifest);ingest(config, root / 'ingested')
    value = json.loads(config.read_text())
    value['structuring']['source_overrides'] = {'one': {'options': {'markdown': {'tables': False}}}}
    write(config, value)
    report = parse_collection(config, root / 'parsed')
    assert all(row['status'] == 'parsed' for row in report['outcomes'])
    source = Path(report['outcomes'][0]['output'])
    assert all(e['kind'] != 'table' for e in json.loads((source / 'artifact-bundle.json').read_text())['elements'])
    assert json.loads((source / 'parse.json').read_text())['settings']['tables'] is False


def test_rechunking_rejects_a_stale_source_edition_left_in_the_parsed_directory(collection):
    root, config, _ = collection
    parse_collection(config, root / 'parsed')
    (root / 'one.md').write_text('# Revised edition\n\nUse the revised procedure.\n')
    manifest = json.loads((root / 'manifest.json').read_text())
    raw = (root / 'one.md').read_bytes()
    manifest['sources'][0].update(source_version='v2', size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    write(root / 'manifest.json', manifest);ingest(config, root / 'ingested')
    parse_collection(config, root / 'parsed')
    with pytest.raises(ValueError, match='stale document editions'):
        build_collection(config, root / 'parsed', root / 'chunks')


def test_ingestion_fetches_only_missing_originals_and_validates_before_installing(collection):
    import io
    root, config, inventory = collection
    original = (root / 'one.md').read_bytes()
    (root / 'one.md').unlink()
    with patch('knowledge_foundry.ingestion.urlopen', return_value=io.BytesIO(original)) as download:
        result = ingest(config, root / 'ingested', fetch=True)
    assert download.call_count == 1
    assert result['artifact_id'] == inventory['artifact_id']
    assert (root / 'one.md').read_bytes() == original
    (root / 'one.md').unlink()
    with patch('knowledge_foundry.ingestion.urlopen', return_value=io.BytesIO(b'wrong bytes')):
        failed = ingest(config, root / 'other-ingestion', fetch=True)
    assert failed['outcomes'][0]['status'] == 'failed'
    assert failed['outcomes'][1]['status'] == 'ingested'
    assert not (root / 'one.md').exists()
