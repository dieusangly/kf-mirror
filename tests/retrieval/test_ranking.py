import json
from dataclasses import replace

import pytest

from knowledge_foundry.retrieval.pipeline import document
from knowledge_foundry.retrieval.ranking import rank_candidates
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
