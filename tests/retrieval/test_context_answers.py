
import json

import pytest

from knowledge_foundry.retrieval.answering import generate, request_payload, request_tokens, validate_answer
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
        def rank_evidence(self, sid, query, rows, **options):
            return rows
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


def test_large_span_does_not_discard_a_focused_span_in_the_same_passage(tmp_path):
    import copy
    records, evidence, _ = projection()
    target = next(r for r in records if r['text'].startswith('Dogs must'))
    oversized = copy.deepcopy(evidence[0])
    oversized.update(id='ev:oversized', text='irrelevant ' * 20000, context_ids=[])
    oversized['reference']['element_id'] = 'el:oversized'
    target['evidence_ids'].insert(0, oversized['id'])
    by_id = {e['id']: e for e in [oversized, *evidence]}
    class Store:
        def evidence(self, sid, ids):
            return {k: by_id[k] for k in ids}
        def rank_evidence(self, sid, query, rows, **options):
            return sorted(rows, key=lambda e: e['id'] != 'ev:oversized')
    value = assemble_context(Store(), 'snapshot', [document(target)], 'Must dogs rest?', [], config(tmp_path))
    assert all(e['id'] != oversized['id'] for e in value['evidence'])
    assert any('Dogs must rest' in e['text'] for e in value['evidence'])
    assert any('Except emergency' in e['text'] for e in value['evidence'])
    assert any('input token budget' in w for w in value['warnings'])


def test_list_clauses_and_output_context_are_never_partially_quoted(tmp_path):
    import copy
    records, evidence, _ = projection()
    rows = copy.deepcopy(evidence[:2])
    for e in rows:
        e.update(element_kind='list_item', context_ids=[])
    record = copy.deepcopy(records[0])
    record['evidence_ids'] = [e['id'] for e in rows]
    class Store:
        def evidence(self, sid, ids):
            return {e['id']: e for e in rows if e['id'] in ids}
        def rank_evidence(self, sid, query, values, **options):
            return values
        def list_siblings(self, sid, seeds, limit):
            return {e['id']: rows for e in seeds}
    c = config(tmp_path)
    c['search']['max_context_evidence'] = 1
    result = assemble_context(Store(), 's', [document(record)], 'Dogs rest?', [], c)
    assert not result['evidence']
    c['search']['max_context_evidence'] = 10
    result = assemble_context(Store(), 's', [document(record)], 'Dogs rest?', [], c)
    assert len(result['evidence']) == 2
    c['budget']['output_tokens'] = 15
    answer = list(generate(c, result['payload'], result['evidence']))[-1]['answer']
    assert answer['status'] == 'insufficient'
    assert not answer['citations']
    assert any('output token budget' in w for w in answer['warnings'])


def test_depth_limit_excludes_incomplete_governing_context(tmp_path):
    import copy
    records, evidence, _ = projection()
    row = next(r for r in records if r['text'].startswith('Dogs must'))
    seed = next(e for e in evidence if e['id'] == row['evidence_ids'][0])
    parent = next(e for e in evidence if e['id'] == seed['context_ids'][0])
    deeper = copy.deepcopy(parent)
    deeper.update(id='ev:deeper', text='Only applies to adult dogs.', context_ids=[])
    deeper['reference']['element_id'] = 'el:deeper'
    parent['context_ids'] = [deeper['id']]
    by_id = {e['id']: e for e in [*evidence, deeper]}
    class Store:
        def evidence(self, sid, ids):
            return {k: by_id[k] for k in ids}
        def rank_evidence(self, sid, query, rows, **options):
            return rows
    c = config(tmp_path)
    c['search']['max_context_depth'] = 1
    result = assemble_context(Store(), 's', [document(row)], 'Dogs rest?', [], c)
    assert not result['evidence']
    assert any('depth limit' in w for w in result['warnings'])
    c['search']['max_context_depth'] = 2
    result = assemble_context(Store(), 's', [document(row)], 'Dogs rest?', [], c)
    assert any(e['id'] == deeper['id'] for e in result['evidence'])


def test_headings_are_scope_context_independent_of_question_language(tmp_path):
    value, c = context(tmp_path)
    heading = {**value['evidence'][0], 'id': 'ev:heading', 'element_kind': 'heading', 'label': 'E9', 'text': 'Rest policy'}
    evidence = [heading, *value['evidence']]
    payload = json.loads(request_payload(c, 'How must dogs recover?', [], evidence)['input'])
    assert all(e['label'] != 'E9' for e in payload['evidence'])
    assert payload['governing_headings'][0]['text'] == 'Rest policy'
    assert validate_answer('The policy requires rest [E9].', evidence)[2] == 'citation_validation_failed'
    for question in ['What is the title?', 'Quel est le titre ?', 'Wie lautet der Titel?', 'この見出しは何ですか？']:
        data = json.loads(request_payload(c, question, [], evidence)['input'])
        assert all(e['label'] != 'E9' for e in data['evidence'])
        assert validate_answer('Rest policy [E9].', evidence)[2] == 'citation_validation_failed'
    c['answer']['provider'] = 'openai'
    def no_model(*args):
        raise AssertionError('headings alone must not trigger a factual answer request')
    empty_body = request_payload(c, 'How must dogs recover?', [], [heading])
    assert list(generate(c, empty_body, [heading], no_model))[-1]['answer']['status'] == 'insufficient'
