import copy
import json
from dataclasses import replace

import pytest

from knowledge_foundry.retrieval.answering import request_tokens
from knowledge_foundry.retrieval.pipeline import document
from knowledge_foundry.retrieval.ranking import rank_candidates
from knowledge_foundry.retrieval.service import DocumentAssistant
from rag_fixtures import config, projection


def settings(tmp_path):
    c = config(tmp_path)
    c['search']['rerank'] = True
    c['answer']['provider'] = 'openai'
    return c


def test_generic_selection_uses_only_supplied_records_and_records_usage(tmp_path):
    docs = [document(r) for r in projection()[0] if r['kind'] == 'passage']
    expected = next(i for i, d in enumerate(docs) if d.content.startswith('Dogs must'))
    def stream(payload, config):
        data = json.loads(payload['input'])
        assert 'Dogs must' in data['candidates'][expected]['text']
        assert payload['store'] is False and payload['max_output_tokens'] <= config['budget']['output_tokens']
        yield {'type': 'delta', 'text': json.dumps({'selected_indices': [expected]})}
        yield {'type': 'usage', 'usage': {'input_tokens': 100, 'output_tokens': 10}, 'model': 'fixture'}
    selected, telemetry = rank_candidates(docs, 'Must dogs rest?', settings(tmp_path), stream)
    assert [d.id for d in selected] == [docs[expected].id]
    assert telemetry['model'] == 'fixture' and telemetry['estimated_cost_usd'] > 0
    assert telemetry['candidate_ids'] == [d.id for d in docs]
    assert telemetry['admitted_ids'] == [d.id for d in docs]
    assert telemetry['selected_ids'] == [docs[expected].id]


@pytest.mark.parametrize('indices', [[999], [-1], [True], ['0'], [0, 0], [0, 1], None])
def test_unknown_duplicate_or_excess_ranked_identifiers_fail_closed(tmp_path, indices):
    docs = [document(r) for r in projection()[0]]
    def stream(*args):
        yield {'type': 'delta', 'text': json.dumps({'selected_indices': indices})}
    with pytest.raises(ValueError, match='invalid candidate identifiers'):
        rank_candidates(docs, 'Question', settings(tmp_path), stream)


def test_ranker_excludes_whole_oversized_candidates_and_enforces_request_budget(tmp_path):
    docs = [document(r) for r in projection()[0] if r['kind'] == 'passage']
    docs[0] = replace(docs[0], content='oversized ' * 30000)
    def stream(payload, config):
        data = json.loads(payload['input'])
        assert not any('oversized' in r['text'] for r in data['candidates'])
        yield {'type': 'delta', 'text': '{"selected_indices": []}'}
    c = settings(tmp_path)
    selected, telemetry = rank_candidates(docs, 'Question', c, stream)
    assert selected == [] and telemetry['candidates_admitted'] == len(docs) - 1
    c['budget']['input_tokens'] = 1
    with pytest.raises(ValueError, match='complete request budget'):
        rank_candidates(docs, 'Question', c, stream)


def test_offline_baseline_never_calls_ranking_model(tmp_path):
    docs = [document(r) for r in projection()[0]]
    def forbidden(*args):
        raise AssertionError('offline ranking must not call models')
    selected, telemetry = rank_candidates(docs, 'Question', config(tmp_path), forbidden)
    assert selected == docs[:1] and telemetry is None


def test_claim_previews_expose_source_facts_without_changing_candidate_identity(tmp_path):
    records, _, _ = projection()
    claim = document(next(r for r in records if r['kind'] == 'claim'))
    passage = next(r for r in records if r['text'].startswith('Dogs must'))
    original = copy.deepcopy(claim.to_dict())

    def previews(doc):
        assert doc is claim
        return [passage]

    def stream(payload, config):
        row = json.loads(payload['input'])['candidates'][0]
        assert 'Dogs must' not in row['text']
        assert row['supporting_passages'] == [
            {k: passage[k] for k in ('id', 'text', 'source_ids', 'document_ids')}]
        yield {'type': 'delta', 'text': '{"selected_indices": [0]}'}

    selected, telemetry = rank_candidates([claim], 'Must dogs rest?', settings(tmp_path), stream,
                                          passage_preview_loader=previews)
    assert selected[0] is claim and claim.to_dict() == original
    assert telemetry['candidate_ids'] == telemetry['admitted_ids'] == telemetry['selected_ids'] == [claim.id]
    assert telemetry['claim_previews']['passages_added'] == 1
    assert telemetry['claim_previews']['request_input_tokens_added'] > 0


def test_claim_previews_use_spare_budget_without_evicting_candidates_or_cutting_passages(tmp_path):
    records, _, _ = projection()
    claim = document(next(r for r in records if r['kind'] == 'claim'))
    docs = [replace(claim, id=f'claim:{i}') for i in range(2)]
    docs.append(replace(claim, id='claim:oversized', content='irrelevant ' * 20000))
    passage = next(r for r in records if r['text'].startswith('Dogs must'))
    baseline = []

    def capture(payload, config):
        baseline.append(dict(payload))
        yield {'type': 'delta', 'text': '{"selected_indices": [0]}'}

    c = settings(tmp_path)
    c['search']['top_k'] = 2
    _, before = rank_candidates(docs, 'Must dogs rest?', c, capture)
    c['budget']['input_tokens'] = request_tokens(baseline[0], c) + 512
    calls = []

    def previews(doc):
        calls.append(doc.id)
        return [{**passage, 'id': 'oversized', 'text': 'irrelevant ' * 20000}, passage,
                {**passage, 'id': 'beyond-limit', 'text': 'Must not be included.'}]

    def stream(payload, config):
        assert request_tokens(payload, config) <= config['budget']['input_tokens']
        rows = json.loads(payload['input'])['candidates']
        assert len(rows) == len(before['admitted_ids']) == 2
        for row in rows:
            assert row['supporting_passages'] == [
                {k: passage[k] for k in ('id', 'text', 'source_ids', 'document_ids')}]
        yield {'type': 'delta', 'text': '{"selected_indices": [0]}'}

    _, after = rank_candidates(docs, 'Must dogs rest?', c, stream, passage_preview_loader=previews)
    assert calls == [d.id for d in docs[:2]]
    assert after['admitted_ids'] == before['admitted_ids']
    assert after['claim_previews']['passages_added'] == 2
    assert after['claim_previews']['passages_omitted'] == 2

    # At the exact baseline budget, previews must leave the entire request unchanged.
    c['budget']['input_tokens'] = request_tokens(baseline[0], c)
    captured = []

    def no_room(payload, config):
        captured.append(dict(payload))
        yield {'type': 'delta', 'text': '{"selected_indices": [0]}'}

    _, full = rank_candidates(docs, 'Must dogs rest?', c, no_room, passage_preview_loader=previews)
    assert captured == baseline
    assert full['admitted_ids'] == before['admitted_ids']
    assert full['claim_previews']['passages_added'] == 0
    assert full['claim_previews']['request_input_tokens_added'] == 0


@pytest.mark.parametrize('kind', ['passage', 'summary'])
def test_nonclaim_ranking_keeps_the_same_payload_and_never_loads_previews(tmp_path, kind):
    docs = [document(r) for r in projection()[0] if r['kind'] == kind]
    requests = []

    def stream(payload, config):
        requests.append(dict(payload))
        yield {'type': 'delta', 'text': '{"selected_indices": [0]}'}

    def forbidden(*args):
        raise AssertionError('only claim candidates may load previews')

    _, before = rank_candidates(docs, 'Question', settings(tmp_path), stream)
    _, after = rank_candidates(docs, 'Question', settings(tmp_path), stream, passage_preview_loader=forbidden)
    assert requests[0] == requests[1]
    assert before['admitted_ids'] == after['admitted_ids']
    assert 'claim_previews' not in after


def test_disabled_reranking_never_loads_claim_previews(tmp_path):
    docs = [document(r) for r in projection()[0] if r['kind'] == 'claim']

    def forbidden(*args):
        raise AssertionError('disabled re-ranking must not load previews or call a model')

    selected, telemetry = rank_candidates(docs, 'Question', config(tmp_path), forbidden,
                                          passage_preview_loader=forbidden)
    assert selected == docs[:1] and telemetry is None


def test_claim_preview_pipeline_uses_the_pinned_snapshot_provenance_and_scope(tmp_path):
    records, evidence, identity = projection()
    claim = next(r for r in records if r['kind'] == 'claim')
    passage = next(r for r in records if r['text'].startswith('Dogs must'))
    source_ids, document_ids = claim['source_ids'], claim['document_ids']
    snapshot = {'id': 'pinned', 'identity': {**identity, 'language': 'english'}}
    calls = []

    class Store:
        def snapshot(self, corpus, sid):
            assert sid == snapshot['id']
            return snapshot

        def search(self, snap, query, kind, method, search, vector, sources, documents):
            assert snap is snapshot and kind == 'claim' and method == 'keyword'
            assert sources == source_ids and documents == document_ids
            return [claim]

        def supporting_passages(self, sid, evidence_ids, chunks, query, limit, language, sources, documents):
            calls.append((sid, evidence_ids, chunks, query, limit, language, sources, documents))
            return [passage]

        def evidence(self, sid, ids):
            assert sid == snapshot['id']
            return {e['id']: copy.deepcopy(e) for e in evidence if e['id'] in ids}

        def rank_evidence(self, sid, query, rows):
            return rows

    def stream(payload, config):
        candidate = json.loads(payload['input'])['candidates'][0]
        assert candidate['supporting_passages'][0]['id'] == passage['id']
        yield {'type': 'delta', 'text': '{"selected_indices": [0]}'}

    assistant = DocumentAssistant(Store(), settings(tmp_path), ranking_stream=stream)
    result = assistant.retrieve('animals', 'Must dogs rest?', snapshot_id='pinned', mode='claim',
                                source_ids=source_ids, document_ids=document_ids)
    expected = ('pinned', claim['evidence_ids'], claim['metadata']['original']['chunk_ids'],
                'Must dogs rest?', 1, 'english', source_ids, document_ids)
    # Preview and final resolution share the same scope and provenance.
    assert calls == [expected, expected]
    assert result['navigation_ids'] == result['ranking']['selected_ids'] == [claim['id']]
    assert result['passage_ids'] == [passage['id']]
    assert any('Dogs must' in e['text'] for e in result['evidence'])
