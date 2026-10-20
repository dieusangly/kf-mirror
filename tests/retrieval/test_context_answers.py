
import pytest

from knowledge_foundry.retrieval.answering import generate, request_tokens, validate_answer
from knowledge_foundry.retrieval.context import assemble_context
from knowledge_foundry.retrieval.pipeline import document
from rag_fixtures import config, projection


def context(tmp_path, mutate=None):
    records, evidence, _ = projection()
    c = config(tmp_path)
    if mutate:
        mutate(c)
    class Store:
        def evidence(self, sid, ids):
            return {e['id']: e for e in evidence if e['id'] in ids}
    row = next(r for r in records if r['text'].startswith('Dogs must'))
    return assemble_context(Store(), 'snapshot', [document(row), document(row)], 'Must dogs rest?', [], c), c


def test_context_expands_parent_and_exception_and_deduplicates(tmp_path):
    value, c = context(tmp_path)
    assert len(value['evidence']) == 3
    assert len({e['id'] for e in value['evidence']}) == 3
    assert any('Except emergency' in e['text'] for e in value['evidence'])
    assert not any('Unrelated' in e['text'] for e in value['evidence'])
    assert request_tokens(value['payload'], c) <= c['budget']['input_tokens']


def test_budget_excludes_entire_passage_with_its_context(tmp_path):
    value, _ = context(tmp_path, lambda c: c['search'].update(max_context_evidence=1))
    assert not value['evidence']
    assert 'budget' in value['warnings'][0]
    with pytest.raises(ValueError, match='complete request budget'):
        context(tmp_path, lambda c: c['budget'].update(input_tokens=1))


def test_unknown_citations_and_uncited_paragraphs_fail_closed(tmp_path):
    value, _ = context(tmp_path)
    ev = value['evidence']
    for text in ['Dogs rest [E999].', 'Dogs rest.', 'Dogs rest [E1].\n\nExtra unsupported statement.',
                 'Dogs rest [E1] https://example.com', 'Dogs rest [E1] <script>bad</script>']:
        assert validate_answer(text, ev)[2] == 'citation_validation_failed'
    text, citations, status = validate_answer('Dogs rest after exercise [E1].', ev)
    assert status == 'draft'
    assert citations[0]['reference'] == ev[0]['reference']
    assert validate_answer('INSUFFICIENT_EVIDENCE', ev)[2] == 'insufficient'


def test_deterministic_stream_reports_usage_model_and_resolved_citations(tmp_path):
    value, c = context(tmp_path)
    c['answer']['provider'] = 'openai'
    def stream(payload, config):
        yield {'type': 'delta', 'text': 'Dogs rest after exercise [E1].'}
        yield {'type': 'usage', 'usage': {'input_tokens': 100, 'cached_input_tokens': 10, 'cache_write_tokens': 20, 'output_tokens': 10}, 'model': 'fixture-model'}
    result = list(generate(c, value['payload'], value['evidence'], stream))[-1]['answer']
    assert result['status'] == 'draft'
    assert result['telemetry']['model'] == 'fixture-model'
    assert result['telemetry']['estimated_cost_usd'] == pytest.approx(.0000146)
    assert result['citations'][0]['text'] == 'Dogs must rest after exercise. [1]'


def test_no_evidence_never_calls_the_model(tmp_path):
    value, c = context(tmp_path)
    c['answer']['provider'] = 'openai'
    def stream(*args):
        raise AssertionError('model should not be called')
    result = list(generate(c, value['payload'], [], stream))[-1]['answer']
    assert result['status'] == 'insufficient'
