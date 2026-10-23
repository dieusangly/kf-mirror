import copy
import json

import pytest

from knowledge_foundry.retrieval.artifacts import load_artifacts, project
from knowledge_foundry.retrieval.config import ROOT
from knowledge_foundry.retrieval.indexing import index_artifacts
from rag_fixtures import artifacts, bundle, config


def test_projection_preserves_original_lineage_and_scoped_context():
    a = artifacts()
    frozen = copy.deepcopy(a)
    rows, evidence = project(a)
    by_id = {e['id']: e for e in evidence}
    passage = next(r for r in rows if r['text'].startswith('Dogs must'))
    ev = by_id[passage['evidence_ids'][0]]
    assert ev['reference'] == a['bundle']['chunks'][1]['evidence'][0]
    assert ev['original_chunk_ids'] == [a['bundle']['chunks'][1]['id']]
    context = [by_id[k]['text'] for k in ev['context_ids']]
    assert 'Rest requirements apply only to tired dogs.' in context
    assert '1 Except emergency rescue dogs while on duty.' in context
    assert all('Unrelated' not in text for text in context)
    assert a == frozen


def test_explicit_artifact_validation_and_build_selection(tmp_path):
    path = tmp_path / 'artifact-bundle.json'
    b = bundle()
    path.write_text(json.dumps(b))
    loaded = load_artifacts(artifact_path=path, build_id=b['builds'][0]['id'])
    assert loaded['bundle'] == b
    with pytest.raises(ValueError, match='absent'):
        load_artifacts(artifact_path=path, build_id='missing')
    b['elements'][0]['text'] = 'tampered'
    path.write_text(json.dumps(b))
    with pytest.raises(ValueError, match='revision mismatch'):
        load_artifacts(artifact_path=path)


def test_rejects_partial_or_hash_mismatched_claim_branch(tmp_path):
    b = bundle()
    path = tmp_path / 'artifact-bundle.json'
    path.write_text(json.dumps(b))
    claims = tmp_path / 'claims'
    claims.mkdir()
    (claims / 'build-manifest.json').write_text(json.dumps({'stage': 'topic-classification', 'outputs': []}))
    (claims / 'report.json').write_text(json.dumps({'status': 'partial'}))
    with pytest.raises(ValueError, match='incomplete'):
        load_artifacts(artifact_path=path, claims_dir=claims)
    manifest = {'stage': 'topic-classification', 'outputs': [{'path': '../artifact-bundle.json', 'sha256': 'bad'}]}
    (claims / 'build-manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='hash mismatch'):
        load_artifacts(artifact_path=path, claims_dir=claims)


def test_saved_vectors_only_reused_for_exact_profile_and_text(tmp_path):
    from knowledge_foundry.retrieval.artifacts import profile
    from knowledge_foundry.structural_chunks import stable_hash
    a = artifacts()
    c = config(tmp_path)
    r = a['structural'][0]
    row = {**profile(c['embedding']), 'structural_chunk_id': r['id'], 'vector': [1, 0, 0],
           'input_sha256': stable_hash(r['text']), 'vector_sha256': stable_hash([1, 0, 0])}
    a['embeddings'] = [row]
    assert project(a, c['embedding'])[0][0]['vector'] == [1, 0, 0]
    for key, value in [('model', 'different'), ('dimensions', 2), ('preprocessing_version', 'v2')]:
        changed = {**c['embedding'], key: value}
        assert project(a, changed)[0][0]['vector'] is None
    a['embeddings'][0]['input_sha256'] = 'bad'
    with pytest.raises(ValueError, match='text/hash'):
        project(a, c['embedding'])


def test_existing_contract_examples_can_be_imported_without_claim_generation(tmp_path):
    loaded = load_artifacts(artifact_path=ROOT / 'examples/artifact-bundle-pdf.json')
    rows, evidence = project(loaded)
    assert {r['kind'] for r in rows} == {'passage', 'claim'}
    assert all(e['source']['version'] for e in evidence)


def test_saved_summary_vectors_reuse_text_and_profile_across_rounds(tmp_path):
    from knowledge_foundry.retrieval.artifacts import profile
    from knowledge_foundry.structural_chunks import stable_hash
    a, c = artifacts(), config(tmp_path)
    summary = a['summaries'][0]
    row = {**profile(c['embedding']), 'summary_id': summary['id'], 'vector': [1, 0, 0],
           'input_sha256': stable_hash(summary['text']), 'vector_sha256': stable_hash([1, 0, 0])}
    a['summary_embeddings'] = [{**row, 'round': 2}, {**row, 'round': 3}]
    records, _ = project(a, c['embedding'])
    assert next(r for r in records if r['kind'] == 'summary')['vector'] == [1, 0, 0]
    assert all(r['vector'] is None for r in records if r['kind'] != 'summary')
    assert next(r for r in project(a, {**c['embedding'], 'model': 'other'})[0] if r['kind'] == 'summary')['vector'] is None
    a['summary_embeddings'][1].update(vector=[0, 1, 0], vector_sha256=stable_hash([0, 1, 0]))
    with pytest.raises(ValueError, match='conflicting saved vectors'):
        project(a, c['embedding'])


def test_import_does_not_publish_implicitly(tmp_path):
    class Store:
        def import_snapshot(self, *args):
            return {'snapshot_id': 'new'}
    result = index_artifacts(Store(), 'fixture', config(tmp_path), artifact_path=ROOT / 'examples/artifact-bundle-pdf.json')
    assert result['snapshot_id'] == 'new'
    assert result['embedding_usage']['requests'] == 0


def test_summary_branch_requires_identical_frozen_preparation(tmp_path):
    import shutil
    from claim_fixtures import fixture_settings, prepare_fixture
    from knowledge_foundry.claim_context import load_preparation
    from knowledge_foundry.summaries import generate_summaries
    settings = fixture_settings()
    chunked = tmp_path / 'chunked'
    chunked.mkdir()
    (chunked / 'artifact-bundle.json').write_text(json.dumps(bundle()))
    cfg = tmp_path / 'preparation.json'
    cfg.write_text(json.dumps(settings))
    prepared_dir = prepare_fixture(chunked, cfg)
    prepared = load_preparation(prepared_dir)
    summary_config = json.loads((ROOT / 'config/summaries.json').read_text())
    summary_config['steps']['summary_generation']['provider'] = 'fixture'
    summary_config['steps']['embeddings'].update(provider='fixture', dimensions=2, expected_dimensions=2)
    summary_config['steps']['summary_clusters']['max_levels'] = 1
    summary_config['pricing']['providers']['fixture'] = copy.deepcopy(summary_config['pricing']['providers']['openai'])
    summary_cfg = tmp_path / 'summary.json'
    summary_cfg.write_text(json.dumps(summary_config))
    fixture = tmp_path / 'summary-fixture.json'
    fixture.write_text(json.dumps({'summaries': {c['id']: {'text': 'A draft navigational summary.'} for c in prepared['clusters']}}))
    output = tmp_path / 'summaries'
    generate_summaries(prepared_dir, output, summary_cfg, tmp_path / 'summary-cache', fixture)
    loaded = load_artifacts(preparation_dir=prepared_dir, summaries_dir=output)
    assert loaded['summaries']
    other = tmp_path / 'other-preparation'
    shutil.copytree(prepared_dir, other)
    manifest = other / 'build-manifest.json'
    manifest.write_text(json.dumps(json.loads(manifest.read_text()), indent=3))
    with pytest.raises(ValueError, match='identical selected preparation'):
        load_artifacts(preparation_dir=other, summaries_dir=output)
