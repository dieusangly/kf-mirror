import json

import pytest

from knowledge_foundry.retrieval.query import rewrite_query
from rag_fixtures import config


def fixture_stream(query, uses_history, calls=None, intent='evidence_question'):
    def stream(payload, config):
        if calls is not None:
            calls.append(payload)
        yield {'type': 'delta', 'text': json.dumps({'query': query, 'uses_history': uses_history, 'intent': intent})}
        yield {'type': 'usage', 'usage': {'input_tokens': 20, 'output_tokens': 10}, 'model': 'fixture'}
    return stream


@pytest.mark.parametrize(('question', 'query'), [
    ('Et leur durée ?', 'Quelle est la durée des permis de transport ?'),
    ('How long do they last?', 'How long do transport permits last?'),
    ('Und wie lange gelten sie?', 'Wie lange gelten Transportgenehmigungen?'),
    ('それは何年間有効ですか？', '運送許可証は何年間有効ですか？'),
])
def test_followup_rewriting_is_not_a_language_or_domain_pattern(tmp_path, question, query):
    c = config(tmp_path)
    calls = []
    result = rewrite_query(c, question, ['Transport permit rules'], fixture_stream(query, True, calls))
    assert result['query'] == query and result['history'] == ['Transport permit rules']
    assert not result['warnings']
    assert result['telemetry']['model'] == 'fixture'
    assert result['telemetry']['estimated_cost_usd'] > 0
    assert calls[0]['store'] is False and calls[0]['truncation'] == 'disabled'
    assert calls[0]['text']['format']['strict'] is True


def test_independent_question_ignores_previous_topics_and_rewrite_edits(tmp_path):
    result = rewrite_query(config(tmp_path), 'How do orchids grow?', ['Transport permits'],
                           fixture_stream('Changed question', False))
    assert result['query'] == 'How do orchids grow?' and result['history'] == []


def test_first_question_and_offline_fallback_never_make_model_calls(tmp_path):
    c = config(tmp_path)
    def forbidden(*args):
        raise AssertionError('no call expected')
    assert rewrite_query(c, 'Hello', [], forbidden)['query'] == 'Hello'
    result = rewrite_query(c, 'What about their duration?', ['Transport permits'])
    assert result['query'] == 'What about their duration?' and not result['history']
    assert 'standalone' in result['warnings'][0]


@pytest.mark.parametrize('output', ['not JSON', '[]', '{}', '{"query":1,"uses_history":true}',
                                     '{"query":"","uses_history":true}'])
def test_invalid_rewrite_does_not_contaminate_retrieval(tmp_path, output):
    def stream(*args):
        yield {'type': 'delta', 'text': output}
    result = rewrite_query(config(tmp_path), 'Current question', ['Older topic'], stream)
    assert result['query'] == 'Current question' and not result['history']
    assert 'failed' in result['warnings'][0]


def test_rewriter_enforces_complete_request_and_output_budgets(tmp_path):
    c = config(tmp_path)
    c['budget']['input_tokens'] = 1
    with pytest.raises(ValueError, match='complete request budget'):
        rewrite_query(c, 'How long?', ['Permits'], fixture_stream('Permit duration?', True))
    c['budget']['input_tokens'] = 12000
    c['budget']['max_question_tokens'] = 10
    result = rewrite_query(c, 'How long?', ['Permits'], fixture_stream('word ' * 100, True))
    assert result['warnings'] and not result['history']


def test_inventory_route_on_first_question_uses_structured_intent(tmp_path):
    calls = []
    result = rewrite_query(config(tmp_path), 'このコーパスには何の文書がありますか？', [],
                           fixture_stream('ignored', False, calls, 'document_inventory'), route_inventory=True)
    assert result['intent'] == 'document_inventory'
    assert len(calls) == 1 and not result['history']


def test_offline_inventory_routing_does_not_call_a_provider(tmp_path):
    result = rewrite_query(config(tmp_path), 'List documents', [], route_inventory=True)
    assert result['intent'] == 'evidence_question' and result['telemetry'] is None


def test_first_question_cannot_acquire_invented_history(tmp_path):
    result = rewrite_query(config(tmp_path), 'Current question', [],
                           fixture_stream('Invented context', True), route_inventory=True)
    assert result['query'] == 'Current question' and not result['history']
    assert result['warnings'] and result['intent'] == 'evidence_question'
