"""Corpus integrity and scope checks; fixtures never approve pilot quality."""
import hashlib
import json
from pathlib import Path

import pytest

from knowledge_foundry.corpus import readiness


def write(path, value):
    path.write_text(json.dumps(value))


@pytest.fixture
def corpus(tmp_path):
    sources = []
    for mid, language in [('EN', 'en'), ('FR', 'fr')]:
        path = tmp_path / (mid + '.md')
        path.write_text('# Synthetic source\n\nScope mechanics only.\n')
        sources.append({'manifest_id': mid, 'source_id': 'src:fixture-' + language,
                        'title': 'Fixture ' + language, 'class': 'fixture', 'source_version': 'v1',
                        'publisher_url': 'https://example.org/' + language, 'language': language,
                        'local_path': path.name, 'retrieved_on': '2026-10-22', 'media_type': 'text/markdown',
                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size_bytes': path.stat().st_size})
    manifest = {'schema_version': '1.0.0', 'sources': sources}
    config = {'root': '.', 'manifest_file': 'manifest.json', 'manifest_ids': ['EN', 'FR']}
    write(tmp_path / 'manifest.json', manifest)
    write(tmp_path / 'pipeline.json', config)
    return tmp_path, manifest, config


def test_saved_corpus_and_development_cases_have_consistent_scope():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'data/manifest.json').read_text())
    result = readiness(root / 'config/pipeline.json')
    assert result['ready']
    assert result['registered_files'] == result['selected_files'] == result['active_files'] == 13
    entries = {e['manifest_id']: e for e in manifest['sources']}
    for entry in entries.values():
        assert Path(entry['local_path']).name.startswith(entry['manifest_id'] + '-')
    en, fr = entries['R24EN'], entries['R24FR']
    assert en['source_id'] != fr['source_id'] and en['work_id'] == fr['work_id']
    assert en['language_variant_of'] == fr['source_id'] and fr['language_variant_of'] == en['source_id']
    assert entries['P']['source_version'] == '2018-body'
    assert entries['P']['completeness'] == 'complete_annex_pairing'
    assert entries['P']['requires_source_ids'] == [entries['PANN24']['source_id']]
    assert not entries['P'].get('accepted_missing_dependencies')
    assert not result['accepted_limitations']
    cases = json.loads((root / 'tests/fixtures/retrieval-expanded-development.json').read_text())
    assert {c['source_id'] for c in cases} == {e['source_id'] for e in entries.values()}
    assert all(c['label_status'] in {'development_unreviewed', 'source_reviewed'} for c in cases)
    reviewed = {c['id'] for c in cases if c['label_status'] == 'source_reviewed'}
    if reviewed:
        config = json.loads((root / 'config/workflow.json').read_text())
        acceptance = json.loads((root / config['retrieval_acceptance']).read_text())
        assert acceptance['status'] == 'reviewed' and acceptance['reviewer']
        assert acceptance['manifest_sha256'] == hashlib.sha256((root / 'data/manifest.json').read_bytes()).hexdigest()
        assert acceptance['cases_sha256'] == hashlib.sha256((root / config['retrieval_cases']).read_bytes()).hexdigest()
        assert reviewed <= set(acceptance['case_ids'])


@pytest.mark.parametrize('mutation', ['missing_file', 'changed_bytes', 'unknown_selection', 'unselected_source'])
def test_invalid_inputs_block_readiness(corpus, mutation):
    root, _, config = corpus
    if mutation == 'missing_file':
        (root / 'EN.md').unlink()
    elif mutation == 'changed_bytes':
        (root / 'EN.md').write_text('# Tampered source')
    else:
        config['manifest_ids'] = ['EN', 'missing'] if mutation == 'unknown_selection' else ['EN']
        write(root / 'pipeline.json', config)
    assert not readiness(root / 'pipeline.json')['ready']


def test_historical_body_requires_explicit_matching_scope_decision(corpus):
    root, manifest, _ = corpus
    entry = manifest['sources'][0]
    entry.update(historical=True, completeness='incomplete_annex_pairing', requires_source_ids=['src:annex'])
    write(root / 'manifest.json', manifest)
    assert not readiness(root / 'pipeline.json')['ready']
    decision = {'source_id': 'src:annex', 'decision': 'historical_body_only', 'authority': 'explicit_user_instruction'}
    entry['accepted_missing_dependencies'] = [decision]
    write(root / 'manifest.json', manifest)
    accepted = readiness(root / 'pipeline.json')
    assert accepted['ready'] and accepted['accepted_limitations']
    for field, invalid in [('historical', False), ('authority', 'inferred'), ('source_id', 'src:another-annex')]:
        target = entry if field == 'historical' else decision
        previous = target[field]
        target[field] = invalid
        write(root / 'manifest.json', manifest)
        assert not readiness(root / 'pipeline.json')['ready']
        target[field] = previous


@pytest.mark.parametrize('defect', ['one_way', 'same_language', 'different_work'])
def test_language_counterparts_must_remain_reciprocal(corpus, defect):
    root, manifest, _ = corpus
    en, fr = manifest['sources']
    en.update(work_id='fixture-work', language_variant_of=fr['source_id'])
    fr.update(work_id='fixture-work', language_variant_of=en['source_id'])
    write(root / 'manifest.json', manifest)
    assert readiness(root / 'pipeline.json')['ready']
    if defect == 'one_way':
        fr.pop('language_variant_of')
    elif defect == 'same_language':
        fr['language'] = en['language']
    else:
        fr['work_id'] = 'another-work'
    write(root / 'manifest.json', manifest)
    assert not readiness(root / 'pipeline.json')['ready']


def test_mandatory_parsing_review_locations_retain_known_defect_pages():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / 'config/workflow.json').read_text())
    locations = json.loads((root / config['parsing_review_required_pages']).read_text())
    pairs = {(row['source_id'], row['page']) for row in locations}
    assert len(pairs) == len(locations)
    manifest = json.loads((root / 'data/manifest.json').read_text())
    assert {sid for sid, _ in pairs} <= {e['source_id'] for e in manifest['sources']}
    # Known defects on retained sources remain mandatory even when future fixes
    # remove warnings; excluded AMF sources no longer belong to the active scope.
    assert ('src:fr-colb-national-risk-assessment-2023', 50) in pairs
    assert ('src:tracfin-threat-report-tome-3', 75) in pairs
    assert {('src:acpr-identification-2021', p) for p in [40, 46, 47]} <= pairs
    assert ('src:acpr-dgt-asset-freezing-2026', 35) in pairs
    assert ('src:acpr-ppe-annex-2024', 4) in pairs
    assert all(type(page) is int and page > 0 for _, page in pairs)
