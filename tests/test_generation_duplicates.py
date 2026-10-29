"""Exact duplicate claims retain every producing cluster and its accounting."""
import json
from unittest.mock import patch

import pytest

from claim_fixtures import bundle, fixture_settings, prepare_claim_context
from knowledge_foundry import claim_generation_and_classification as claims
from knowledge_foundry.claim_source_context import source_context_index
from knowledge_foundry.structural_chunks import assemble_structural_chunks, stable_hash
from scripts.render_knowledge_graph import build_graph


@pytest.fixture(params=[False, True], ids=['separate-spans', 'shared-span-owners'])
def overlapping(tmp_path, request):
    chunked = tmp_path / 'chunked'
    chunked.mkdir()
    groups = [[0], [1], [0, 1]] if request.param else [[0], [1]]
    source = bundle([('paragraph', 'Identify customers.', 1),
                     ('paragraph', 'Verify their identity.', 1)], groups)
    (chunked / 'artifact-bundle.json').write_text(json.dumps(source))
    config = fixture_settings()
    config['steps']['claim_generation']['retries'] = 0
    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps(config))
    structural, _ = assemble_structural_chunks(source)
    fixture = tmp_path / 'fixture.json'
    fixture.write_text(json.dumps({'embedding_vectors': {
        stable_hash(row['text']): [float(i == j) for j in range(2)]
        for i, row in enumerate(structural)}}))
    context = tmp_path / 'context'
    prepare_claim_context(chunked, context, config_path, tmp_path / 'cache', fixture)
    clusters = json.loads((context / 'semantic-clusters.json').read_text())
    assert len(clusters) == 2
    elements = {e['id']: e for e in source['elements']}
    chunks = {c['id']: c for c in source['chunks']}
    structural = claims.load_preparation(context, config)['structural_chunks']
    structural = {c['id']: c for c in structural}
    responses = {}
    for cluster in clusters:
        choices = claims._evidence_choices(
            [chunks[cid] for cid in cluster['original_chunk_ids']], elements,
            [structural[sid] for sid in cluster['structural_chunk_ids']], source_context_index(source))
        # Evidence order is identical despite primary/support labels changing.
        labels = sorted(choices, key=lambda label: elements[choices[label]['reference']['element_id']]['ordinal'])
        assert len(labels) == 2 and any(label.startswith('E') for label in labels)
        responses[tuple(cluster['original_chunk_ids'])] = {
            'claims': [{'text': 'Identify customers and verify their identity.', 'evidence': labels}] * 2,
            'coverage': [{'evidence_id': label, 'status': 'represented', 'claim_indices': [0, 1],
                          'reason': 'Both requirements are cited.'}
                         for label in labels if label.startswith('E')]}

    def run(output, fail=False, **kwargs):
        calls = []

        class Provider(claims.FixtureProvider):
            def candidates(self, ids, *_args):
                calls.append(ids)
                if fail and ids == clusters[1]['original_chunk_ids']:
                    raise RuntimeError('temporary provider failure')
                return responses[tuple(ids)]

        with patch.object(claims, '_provider', side_effect=lambda *_: Provider({})):
            report = claims.generate_claims(None, output, config_path, context_dir=context, **kwargs)
        return report, calls

    return run, clusters, config_path, source, structural


def test_overlapping_duplicates_are_deterministic_and_classifiable(overlapping, tmp_path):
    run, clusters, config, source, structural = overlapping
    serial, parallel = tmp_path / 'serial', tmp_path / 'parallel'
    for directory, workers in ((serial, 1), (parallel, 10)):
        report, _ = run(directory, workers=workers)
        assert report['status'] == 'complete', report['errors']
        assert report['candidates'] == 1
        claims._candidate_manifest(directory)
        mapping = json.loads((directory / 'candidate-clusters.json').read_text())
        assert len(mapping) == 2
        assert {r['semantic_cluster_id'] for r in mapping} == {c['id'] for c in clusters}
        audit = json.loads((directory / 'evidence-accounting.json').read_text())
        assert all(len(row['candidate_ids']) == 1 for c in audit['clusters'] for row in c['passages'])
    for name in ('claim-candidates.jsonl', 'candidate-clusters.json', 'evidence-accounting.json'):
        assert (serial / name).read_bytes() == (parallel / name).read_bytes()
    fixture = tmp_path / 'classification.json'
    text = 'Identify customers and verify their identity.'
    # Null assignments also exercise complete candidate lineage validation.
    fixture.write_text(json.dumps({'classifications': {text: None}}))
    classified = tmp_path / 'classified'
    claims.classify_claims(parallel, classified, config, fixture_path=fixture)
    candidates = claims._read_jsonl(parallel / 'claim-candidates.jsonl')
    assert set(candidates[0]['chunk_ids']) == {c['id'] for c in source['chunks']}
    graph = build_graph(source, claim_generation={'structural_chunks': list(structural.values()),
                        'semantic_clusters': clusters, 'candidate_clusters': mapping, 'candidates': candidates})
    edges = {(e['source'], e['target'], e['relation']) for e in graph['edges']}
    assert all((candidates[0]['id'], c['id'], 'generated from') in edges for c in clusters)


def test_recovery_and_interrupted_resume_preserve_shared_candidates(overlapping, tmp_path):
    run, _, _, _, _ = overlapping
    partial, recovered = tmp_path / 'partial', tmp_path / 'recovered'
    report, _ = run(partial, fail=True, workers=10)
    assert report['status'] == 'partial'
    baseline = {p.relative_to(partial): p.read_bytes() for p in partial.rglob('*') if p.is_file()}
    with patch.object(claims, '_write_json', side_effect=RuntimeError('interrupted export')):
        with pytest.raises(RuntimeError, match='interrupted export'):
            run(recovered, retry_failed_from=partial, workers=10)
    report, calls = run(recovered, retry_failed_from=partial, resume=True, workers=10)
    assert calls == []
    assert report['status'] == 'complete'
    assert report['candidates'] == 1
    claims._candidate_manifest(recovered)
    assert len(json.loads((recovered / 'candidate-clusters.json').read_text())) == 2
    assert baseline == {p.relative_to(partial): p.read_bytes() for p in partial.rglob('*') if p.is_file()}


def test_only_identical_candidate_records_merge():
    first = {'id': 'candidate:a', 'text': 'A', 'evidence': ['location:a']}
    elsewhere = {**first, 'id': 'candidate:b', 'evidence': ['location:b']}
    reworded = {**first, 'id': 'candidate:c', 'text': 'B'}
    assert claims._unique_candidates([first, first, elsewhere, reworded]) == [first, elsewhere, reworded]
    with pytest.raises(ValueError, match='conflicting records'):
        claims._unique_candidates([first, {**first, 'text': 'Conflicting content'}])


def test_chunk_owner_union_is_order_independent_and_preserves_inputs():
    first = {'id': 'candidate:a', 'text': 'A', 'evidence': ['location:a'],
             'chunk_ids': ['chunk:b', 'chunk:a'], 'generation': {'version': 'test'}}
    other = {**first, 'chunk_ids': ['chunk:c']}
    expected = [{**first, 'chunk_ids': ['chunk:a', 'chunk:b', 'chunk:c']}]
    assert claims._unique_candidates([first, other]) == expected
    assert claims._unique_candidates([other, first]) == expected
    assert first['chunk_ids'] == ['chunk:b', 'chunk:a']
    assert other['chunk_ids'] == ['chunk:c']
    with pytest.raises(ValueError, match='conflicting records'):
        claims._unique_candidates([first, {**other, 'generation': {'version': 'different'}}])
