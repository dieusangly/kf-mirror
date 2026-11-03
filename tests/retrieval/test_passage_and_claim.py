"""Mixed retrieval uses bounded candidates and the existing evidence pipeline."""
import copy
import json
import threading

import pytest

from knowledge_foundry.retrieval.config import MODES, load_config
from knowledge_foundry.retrieval.pipeline import SnapshotRetriever, balanced_candidates, document
from knowledge_foundry.retrieval.ranking import rank_candidates
from rag_fixtures import config, projection


def candidates(kind, count):
    template = next(r for r in projection()[0] if r['kind'] == kind)
    return [{**copy.deepcopy(template), 'id': f'{kind}:{i}'} for i in range(count)]


@pytest.mark.parametrize('limit', [1, 2, 5, 6])
def test_balanced_allocation_preserves_each_branch_order(limit):
    passages, claims = candidates('passage', 6), candidates('claim', 6)
    merged = balanced_candidates([passages, claims], limit)
    assert [r['id'] for r in merged] == [r['id'] for pair in zip(passages, claims) for r in pair][:limit]
    assert sum(r['kind'] == 'passage' for r in merged) == (limit + 1) // 2
    assert sum(r['kind'] == 'claim' for r in merged) == limit // 2


@pytest.mark.parametrize('sparse', [0, 1])
@pytest.mark.parametrize('reverse', [False, True])
def test_sparse_branches_backfill_without_exceeding_candidate_budget(sparse, reverse):
    branches = [candidates('passage', 6), candidates('claim', sparse)]
    if reverse:
        branches.reverse()
    merged = balanced_candidates(branches, 5)
    assert len(merged) == 5
    assert sum(r['kind'] == 'claim' for r in merged) == sparse
    assert len(balanced_candidates([[], []], 5)) == 0


def test_deduplication_uses_record_identity_and_preserves_shared_evidence():
    passages, claims = candidates('passage', 3), candidates('claim', 3)
    # A duplicate ID consumes no slot, including when repeated across branches.
    branches = [[passages[0], passages[0], *passages[1:]],
                [passages[0], claims[0], claims[0], *claims[1:]]]
    original = copy.deepcopy(branches)
    merged = balanced_candidates(branches, 5)
    assert [r['id'] for r in merged] == ['passage:0', 'claim:0', 'passage:1', 'claim:1', 'passage:2']
    assert branches == original
    # Identical text/evidence from different records must retain its provenance.
    same_evidence = {**passages[0], 'id': 'claim:shared', 'kind': 'claim'}
    assert balanced_candidates([[passages[0]], [same_evidence]], 2) == [passages[0], same_evidence]


@pytest.mark.parametrize('method', ['keyword', 'vector', 'combined'])
def test_branches_search_concurrently_with_identical_scope_and_one_shared_pool(tmp_path, method):
    c = config(tmp_path)
    c['search'].update(candidate_k=5, top_k=2, rerank=True)
    c['answer']['provider'] = 'openai'
    original = copy.deepcopy(c)
    barrier = threading.Barrier(2)
    snapshot, vector, sources, docs = {'id': 'snapshot', 'identity': {}}, [1., 0., 0.], ['s'], ['d']

    class Store:
        def search(self, snap, query, kind, requested_method, search, v, source_ids, document_ids):
            assert snap is snapshot and query == 'Question' and requested_method == method
            assert v is vector and source_ids == sources and document_ids == docs
            assert search['candidate_k'] == search['top_k'] == 5
            barrier.wait(timeout=5)  # A serial implementation cannot pass.
            return candidates(kind, 5)

    result = SnapshotRetriever(Store(), snapshot, c).run('Question', method, 'passage-and-claim', vector, sources, docs)
    assert [d.id for d in result['documents']] == ['passage:0', 'claim:0', 'passage:1', 'claim:1', 'passage:2']
    assert c == original


@pytest.mark.parametrize('candidate_k,top_k', [(1, 5), (5, 2)])
def test_no_rerank_respects_both_candidate_and_top_k_limits(tmp_path, candidate_k, top_k):
    c = config(tmp_path)
    c['search'].update(candidate_k=candidate_k, top_k=top_k)

    class Store:
        def search(self, snapshot, query, kind, *args):
            return candidates(kind, 5)

    result = SnapshotRetriever(Store(), {'identity': {}}, c).run('Question', 'keyword', 'passage-and-claim')
    assert len(result['documents']) == min(candidate_k, top_k)
    selected, telemetry = rank_candidates(result['documents'], 'Question', c,
                                          lambda *args: pytest.fail('ranking disabled'))
    assert selected == result['documents'] and telemetry is None


def test_one_shared_ranking_call_obeys_candidate_token_and_selection_budgets(tmp_path):
    from knowledge_foundry.retrieval.answering import request_tokens

    c = config(tmp_path)
    c['search'].update(candidate_k=4, top_k=2, rerank=True)
    c['answer']['provider'] = 'openai'
    branches = [candidates('passage', 5), candidates('claim', 5)]
    branches[0][0]['text'] = 'oversized ' * 30000
    docs = [document(r) for r in balanced_candidates(branches, c['search']['candidate_k'])]
    calls = []

    def stream(payload, settings):
        calls.append(payload)
        rows = json.loads(payload['input'])['candidates']
        assert {r['kind'] for r in rows} == {'passage', 'claim'}
        assert len(rows) == 3 and all('oversized' not in r['text'] for r in rows)
        assert json.loads(payload['input'])['maximum_results'] == 2
        assert request_tokens(payload, settings) <= settings['budget']['input_tokens']
        assert payload['max_output_tokens'] <= settings['budget']['output_tokens']
        yield {'type': 'delta', 'text': '{"selected_indices": [0, 1]}'}

    selected, telemetry = rank_candidates(docs, 'Question', c, stream)
    assert len(calls) == 1 and len(selected) == 2
    assert telemetry['candidates_received'] == 4 and telemetry['candidates_admitted'] == 3
    assert telemetry['selected_ids'] == ['claim:0', 'passage:1']
    c['budget']['input_tokens'] = 1
    with pytest.raises(ValueError, match='complete request budget'):
        rank_candidates(docs, 'Question', c, stream)
    assert len(calls) == 1
    c['budget']['input_tokens'] = config(tmp_path)['budget']['input_tokens']
    with pytest.raises(ValueError, match='no complete search candidate fits'):
        rank_candidates(docs[:1], 'Question', c, stream)
    assert len(calls) == 1
    c['budget']['output_tokens'] = 1
    with pytest.raises(ValueError, match='ranking response exceeds output budget'):
        rank_candidates(docs[1:], 'Question', c, stream)
    assert len(calls) == 2


@pytest.mark.parametrize('mode', ['passage', 'claim', 'summary'])
@pytest.mark.parametrize('rerank', [False, True])
def test_existing_modes_keep_single_search_and_original_bounds(tmp_path, mode, rerank):
    c = config(tmp_path)
    c['search']['rerank'] = rerank
    c['answer']['provider'] = 'openai'
    original = copy.deepcopy(c)
    rows = candidates(mode, 2)
    calls = []

    class Store:
        def search(self, snapshot, query, kind, method, search, *args):
            calls.append(kind)
            assert kind == mode and method == 'combined'
            assert search['top_k'] == (search['candidate_k'] if rerank else c['search']['top_k'])
            return rows

    result = SnapshotRetriever(Store(), {'identity': {}}, c).run('Question', 'combined', mode)
    assert calls == [mode] and [d.id for d in result['documents']] == [r['id'] for r in rows]
    assert c == original


@pytest.mark.parametrize('mode', MODES)
def test_configuration_accepts_all_modes(tmp_path, mode):
    c = config(tmp_path)
    c['search']['mode'] = mode
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(c))
    assert load_config(path) == c


@pytest.mark.parametrize('missing', ['passage', 'claim'])
@pytest.mark.parametrize('method', ['vector', 'combined'])
def test_mixed_vector_mode_requires_coverage_for_each_branch(tmp_path, missing, method):
    from knowledge_foundry.retrieval.service import DocumentAssistant

    class Store:
        def snapshot(self, *args):
            return {'id': 'snapshot'}

        def vector_coverage(self, *args):
            return {kind: {'records': 1, 'vectors': int(kind != missing)} for kind in ('passage', 'claim')}

    with pytest.raises(ValueError, match='has no vectors'):
        DocumentAssistant(Store(), config(tmp_path)).retrieve('animals', 'Question',
            mode='passage-and-claim', method=method)


def test_branch_failure_propagates_instead_of_returning_partial_results(tmp_path):
    class Store:
        def search(self, snapshot, query, kind, *args):
            if kind == 'claim':
                raise ValueError('claim search failed')
            return candidates(kind, 2)

    with pytest.raises(ValueError, match='claim search failed'):
        SnapshotRetriever(Store(), {}, config(tmp_path)).run('Question', 'vector', 'passage-and-claim')


@pytest.mark.parametrize('method', ['keyword', 'vector', 'combined'])
def test_mixed_service_preserves_resolution_context_and_exact_citations(store, published, method):
    from knowledge_foundry.retrieval.answering import generate
    from knowledge_foundry.retrieval.evaluation import audit_answer
    from knowledge_foundry.retrieval.service import DocumentAssistant
    from knowledge_foundry.structural_chunks import stable_hash

    c, sid = published
    c['search'].update(candidate_k=4, top_k=2, rerank=True, minimum_vector_similarity=-1.)
    c['answer']['provider'] = 'openai'
    question = 'Dogs rest exercise Canine recovery proposition'
    calls = []

    def stream(payload, settings):
        calls.append(payload)
        rows = json.loads(payload['input'])['candidates']
        assert {r['kind'] for r in rows} == {'passage', 'claim'}
        chosen = [next(r['index'] for r in rows if r['kind'] == 'claim'),
                  next(r['index'] for r in rows if r['text'].startswith('Dogs must'))]
        yield {'type': 'delta', 'text': json.dumps({'selected_indices': chosen})}

    assistant = DocumentAssistant(store, c, ranking_stream=stream,
        embedding_fixture={'embedding_vectors': {stable_hash(question): [1., 0., 0.]}})
    result = assistant.retrieve('animals', question, mode='passage-and-claim', method=method)
    assert len(calls) == 1
    assert len(result['navigation_ids']) <= c['search']['candidate_k']
    assert len(result['passage_ids']) == 1  # Claim and direct passage converge.
    assert len(result['resolution']['expansions']) == 2
    assert any('Except emergency' in e['text'] for e in result['evidence'])
    assert len({e['id'] for e in result['evidence']}) == len(result['evidence'])
    assert all('proposition' not in e['text'] for e in json.loads(result['payload']['input'])['evidence'])
    excerpt_config = copy.deepcopy(c)
    excerpt_config['answer']['provider'] = 'extractive'
    answer = list(generate(excerpt_config, result['payload'], result['evidence']))[-1]['answer']
    answer['snapshot_id'] = sid
    assert audit_answer(answer, store)['citation_resolution_rate'] == 1
    for filters in ({'source_ids': ['missing']}, {'document_ids': ['missing']}):
        assert assistant.retrieve('animals', question, mode='passage-and-claim', method=method, **filters)['evidence'] == []
    assert len(calls) == 1  # No ranking call for empty filtered candidates.


def test_query_cli_accepts_mixed_mode(store, published, tmp_path, monkeypatch):
    from scripts.query import main

    c, sid = published
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(c))
    monkeypatch.setattr('scripts.query.PostgresStore', lambda: store)
    result = main(['Dogs rest exercise', '--corpus', 'animals', '--snapshot-id', sid,
                   '--config', str(path), '--mode', 'passage-and-claim', '--retrieve-only'])
    assert result['mode'] == 'passage-and-claim' and result['evidence']
