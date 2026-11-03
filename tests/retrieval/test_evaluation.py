import copy

import pytest

from knowledge_foundry.retrieval.evaluation import (
    SnapshotEvaluationStore, coverage_metrics, evaluate, evidence_coverage, matches_anchors,
)
from knowledge_foundry.retrieval.service import DocumentAssistant


def outcomes(metrics):
    return [{k: v for k, v in row.items() if not k.endswith('latency_seconds')} for row in metrics]


def test_mixed_evaluation_reports_anchors_multi_document_coverage_latency_and_cost(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from rag_fixtures import config

    first = {'id': 'a', 'source': {'id': 'one'}, 'location': {'page': 1},
             'text': 'First anchor', 'is_context': False}
    second = {'id': 'b', 'source': {'id': 'two'}, 'location': {'page': 2},
              'text': 'Second anchor', 'is_context': False}

    class Store:
        def snapshot(self, *args):
            return {'id': 'snapshot'}

    class Assistant:
        store = Store()

        def __init__(self):
            self.config = config(tmp_path)

        def retrieve(self, corpus, question, **options):
            assert options['mode'] == 'passage-and-claim' and options['method'] == 'vector'
            if question == 'error':
                raise ValueError('fixture failure')
            return {'evidence': [first, second] if question == 'both' else [first],
                    'passage_ids': ['p'], 'query': question, 'warnings': [],
                    'embedding_usage': {'estimated_cost_usd': .01},
                    'query_rewrite': {'estimated_cost_usd': .02},
                    'ranking': {'estimated_cost_usd': .03}}

    case = {'source_id': 'one', 'pages': [1], 'text_anchors': ['First anchor'],
            'grounding': [{'source_id': 'one', 'pages': [1], 'text_anchors': ['First anchor']},
                          {'source_id': 'two', 'pages': [2], 'text_anchors': ['Second anchor']}]}
    cases = [{**case, 'id': question, 'question': question} for question in ['both', 'partial', 'error']]
    original = copy.deepcopy(cases)
    ticks = iter([0., .5, 1., 2.5, 3.])
    monkeypatch.setattr('knowledge_foundry.retrieval.evaluation.time', SimpleNamespace(monotonic=lambda: next(ticks)))
    result = evaluate(Assistant(), 'fixture', cases, modes=('passage-and-claim',), methods=('vector',))
    metrics = result['metrics'][0]
    assert metrics['completed'] == 2 and metrics['cases'] == 3
    assert metrics['text_anchor_hits'] == 2
    assert metrics['retrieved_grounding_coverage'] == .75
    assert metrics['retrieved_all_groundings_hit_rate'] == .5
    assert result['rows'][0]['retrieved_evidence_coverage']['all_groundings_hit']
    assert not result['rows'][1]['retrieved_evidence_coverage']['all_groundings_hit']
    assert metrics['mean_latency_seconds'] == 1. and metrics['p95_latency_seconds'] == 1.5
    assert metrics['costed_cases'] == 2 and metrics['estimated_cost_usd'] == pytest.approx(.12)
    assert result['rows'][0]['estimated_cost_usd'] == pytest.approx(.06)
    assert 'error' in result['rows'][2] and cases == original


def test_evaluation_does_not_report_unknown_costs_as_free():
    from knowledge_foundry.retrieval.evaluation import performance_metrics, retrieval_cost

    assert retrieval_cost({}) == 0.
    assert retrieval_cost({'ranking': {'estimated_cost_usd': None}}) is None
    assert retrieval_cost({'embedding_usage': {'estimated_cost_usd': .01}, 'ranking': {}}) is None
    metrics = performance_metrics([{'latency_seconds': 1., 'estimated_cost_usd': .01},
                                   {'latency_seconds': 2., 'estimated_cost_usd': None}])
    assert metrics['costed_cases'] == 1 and metrics['estimated_cost_usd'] is None
    empty = performance_metrics([])
    assert empty['mean_latency_seconds'] is empty['p95_latency_seconds'] is empty['estimated_cost_usd'] is None


def test_page_overlap_and_resolved_citations_do_not_imply_answer_support(store, published):
    config, snapshot = published
    source = store.snapshot('animals')['identity']['sources'][0]['id']
    cases = [{'id': 'missing', 'question': '', 'source_id': source},
             {'id': 'partial', 'question': 'Dogs rest exercise', 'source_id': source,
              'pages': [1], 'text_anchors': ['Cats fly to Saturn.']}]
    assistant = DocumentAssistant(store, config)
    report = evaluate(assistant, 'animals', cases, methods=('keyword',), check_excerpts=True)
    assert report['metrics'][0]['completed'] == 1
    assert report['metrics'][0]['hits'] == 1
    assert report['metrics'][0]['text_anchor_hits'] == 0
    assert report['metrics'][0]['excerpt_anchor_hits'] == 0
    assert report['rows'][1]['excerpts']['citation_audit']['citation_resolution_rate'] == 1
    row = report['rows'][1]
    assert 'navigation_ids' not in row and 'resolution' not in row
    assert 'evidence_ids' not in row and 'context_decisions' not in row
    assert 'text' not in row['excerpts']
    assert 'warnings' in row['excerpts']
    assert report['trace'] is False
    assert report['configuration'] == config
    assert report['configuration'] is not config and report['cases_sha256']
    traced = evaluate(assistant, 'animals', cases, methods=('keyword',), check_excerpts=True, trace=True)
    assert traced['trace'] is True and outcomes(traced['metrics']) == outcomes(report['metrics'])
    assert traced['rows'][0] == report['rows'][0]
    traced_row = traced['rows'][1]
    assert traced_row['navigation_ids'] and traced_row['resolution']['expansions']
    assert traced_row['evidence_ids'] and traced_row['context_decisions']
    assert traced_row['excerpts']['text']
    assert {k: v for k, v in traced_row['excerpts'].items() if k != 'text'} == row['excerpts']
    unrun = evaluate(assistant, 'animals', cases[1:], methods=('keyword',))
    assert unrun['metrics'][0]['excerpt_anchor_hits'] is None


def test_exact_text_checks_respect_the_expected_source_and_location():
    e = {'id': 'e', 'text': 'Only  after\nexercise.', 'source': {'id': 's'}, 'location': {'page': 3}}
    case = {'source_id': 's', 'pages': [3], 'text_anchors': ['only after exercise']}
    assert matches_anchors([e], case)
    assert not matches_anchors([e], {**case, 'pages': [4]})
    assert not matches_anchors([e], {**case, 'source_id': 'other'})


def test_anchor_typography_equivalence_preserves_words_and_accents():
    e = {'id': 'e', 'text': "L'e\u0301le\u0300ve présente son certificat.",
         'source': {'id': 'school'}, 'location': {'page': 2}}
    case = {'source_id': 'school', 'pages': [2],
            'text_anchors': ['L’élève présente son certificat']}
    assert matches_anchors([e], case)
    assert not matches_anchors([e], {**case, 'text_anchors': ['L’élève présente son permis']})
    assert not matches_anchors([e], {**case, 'text_anchors': ["L'eleve présente son certificat"]})


@pytest.mark.parametrize('trace', [False, True])
@pytest.mark.parametrize('mode', ['passage', 'passage-and-claim'])
def test_cli_can_compare_search_without_ranking_calls(store, published, tmp_path, monkeypatch, trace, mode):
    import json
    from scripts.evaluate_retrieval import main
    config, sid = published
    config['search']['rerank'] = True
    config['answer']['provider'] = 'openai'
    cfg, cases, output = (tmp_path / name for name in ['config.json', 'cases.json', 'report.json'])
    cfg.write_text(json.dumps(config))
    cases.write_text(json.dumps([{'id': 'one', 'question': 'Dogs rest exercise',
                                  'source_id': store.snapshot('animals')['identity']['sources'][0]['id']}]))
    def forbidden(*args):
        raise AssertionError('search comparison must not rank with a model')
    monkeypatch.setenv('KF_DATABASE_URL', store.dsn)
    monkeypatch.setattr('knowledge_foundry.retrieval.ranking.openai_stream', forbidden)
    result = main(['--corpus', 'animals', '--snapshot-id', sid, '--config', str(cfg), '--cases', str(cases),
                   '--methods', 'keyword', '--modes', mode, '--no-rerank', '--check-excerpts', '--output', str(output)]
                  + (['--trace'] if trace else []))
    assert result['metrics'][0]['hits'] == 1 and result['rows'][0]['ranking'] is None
    assert result['trace'] is trace
    assert ('context_decisions' in result['rows'][0]) is trace
    assert ('text' in result['rows'][0]['excerpts']) is trace
    assert output.is_file()


def test_compact_and_trace_reports_preserve_rank_usage_warnings_and_metrics(tmp_path):
    from knowledge_foundry.retrieval.answering import request_payload
    from rag_fixtures import config, projection

    c = config(tmp_path)
    _, evidence, _ = projection()
    seed = next(e for e in evidence if e['text'].startswith('Dogs must'))
    seed.update(is_context=False, label='E1')
    ranking = {'model': 'fixture', 'usage': {'input_tokens': 100, 'output_tokens': 10},
               'estimated_cost_usd': .001, 'request_input_tokens': 120,
               'candidates_received': 2, 'candidates_admitted': 1, 'selected': 1,
               'candidate_ids': ['p1', 'p2'], 'admitted_ids': ['p1'], 'selected_ids': ['p1']}
    result = {'evidence': [seed], 'payload': request_payload(c, 'Dogs rest?', [], [seed]),
              'passage_ids': ['p1'], 'navigation_ids': ['p1', 'p2'],
              'resolution': {'expansions': [{'navigation_id': 'p1', 'passage_ids': ['p1']}]},
              'context_decisions': [{'seed_ids': [seed['id']], 'reason': 'admitted'}],
              'query': 'Dogs rest?', 'warnings': ['ranking budget warning'], 'embedding_usage': None,
              'query_rewrite': None, 'ranking': ranking}
    original = copy.deepcopy(result)

    class Store:
        def snapshot(self, corpus, sid):
            return {'id': 'snapshot'}

        def evidence(self, sid, ids):
            return {seed['id']: seed} if seed['id'] in ids else {}

    class Assistant:
        config, store = c, Store()

        def retrieve(self, *args, **kwargs):
            return result

    cases = [{'id': 'dogs', 'question': 'Dogs rest?', 'source_id': seed['source']['id'],
              'text_anchors': ['Dogs must rest']}]
    compact = evaluate(Assistant(), 'animals', cases, methods=('keyword',), check_excerpts=True)
    traced = evaluate(Assistant(), 'animals', cases, methods=('keyword',), check_excerpts=True, trace=True)
    assert outcomes(compact['metrics']) == outcomes(traced['metrics'])
    assert compact['rows'][0]['text_anchor_hit'] is True
    compact_row, traced_row = compact['rows'][0], traced['rows'][0]
    assert compact_row['warnings'] == traced_row['warnings'] == result['warnings']
    assert compact_row['ranking'] == {k: v for k, v in ranking.items() if not k.endswith('_ids')}
    assert traced_row['ranking'] == ranking
    assert compact_row['excerpts']['citation_audit'] == traced_row['excerpts']['citation_audit']
    assert compact_row['excerpts']['text_anchor_hit'] is True
    assert traced_row['excerpts']['text']
    assert not {'matched_evidence_ids', 'evidence_ids', 'passage_ids', 'navigation_ids',
                'resolution', 'context_decisions'} & compact_row.keys()
    assert result == original


def test_unpublished_evaluation_is_scoped_and_never_publishes():
    class Store:
        def evaluation_snapshot(self, corpus, snapshot_id):
            return {'id': snapshot_id, 'status': 'ready'}
        def publish(self, *args):
            raise AssertionError('evaluation must never publish')
    with pytest.raises(ValueError, match='explicit snapshot'):
        SnapshotEvaluationStore(Store(), 'test', None)
    evaluation_store = SnapshotEvaluationStore(Store(), 'test', 'ready-snapshot')
    assert evaluation_store.snapshot('test', 'ready-snapshot')['status'] == 'ready'
    for corpus, sid in [('test', None), ('other', 'ready-snapshot'), ('test', 'other')]:
        with pytest.raises(ValueError, match='another snapshot'):
            evaluation_store.snapshot(corpus, sid)


def test_explicit_unpublished_read_preserves_active_snapshot(store, published):
    from rag_fixtures import projection
    from knowledge_foundry.retrieval.artifacts import profile
    config, active = published
    records, evidence, identity = projection()
    identity['evaluation_fixture_revision'] = 2
    ready = store.import_snapshot('animals', identity, records, evidence, profile(config['embedding']), 'english')['snapshot_id']
    assert store.evaluation_snapshot('animals', ready)['status'] == 'ready'
    assert store.snapshot('animals')['id'] == active
    with pytest.raises(ValueError, match='published snapshot'):
        store.snapshot('animals', ready)
    with pytest.raises(ValueError, match='absent from this corpus'):
        store.evaluation_snapshot('other-corpus', ready)


def test_resolution_trace_distinguishes_unresolved_records_and_global_truncation(tmp_path):
    from knowledge_foundry.retrieval.pipeline import EvidenceResolver, document
    from rag_fixtures import config, projection

    records, _, _ = projection()
    passages = [r for r in records if r['kind'] == 'passage'][:3]
    claim = next(r for r in records if r['kind'] == 'claim')
    other = copy.deepcopy(claim)
    other['id'] = 'claim:other'
    c = config(tmp_path)
    c['search']['top_k'] = 2

    class Store:
        def supporting_passages(self, sid, evidence, chunks, query, limit, language, sources, docs):
            return passages[:2] if evidence else passages[2:]

    other['evidence_ids'] = []
    resolver = EvidenceResolver(Store(), {'id': 'snapshot', 'identity': {'language': 'english'}}, c)
    result = resolver.run([document(claim), document(other)], 'Dogs rest')
    assert [d.id for d in result['documents']] == [r['id'] for r in passages[:2]]
    assert result['telemetry']['discarded_passage_ids'] == [passages[2]['id']]
    assert result['telemetry']['expansions'][1]['navigation_id'] == other['id']
    assert result['telemetry']['expansions'][1]['passage_ids'] == [passages[2]['id']]

    class MissingStore(Store):
        def supporting_passages(self, *args):
            return []

    missing = EvidenceResolver(MissingStore(), resolver.snapshot, c).run([document(claim)], 'Dogs rest')
    assert missing['documents'] == []
    assert missing['telemetry']['expansions'] == [{'navigation_id': claim['id'], 'passage_ids': []}]
    assert missing['telemetry']['discarded_passage_ids'] == []


def test_grounding_coverage_requires_every_reference_and_anchor():
    first = {'id': 'one', 'source': {'id': 'a'}, 'location': {'page': 2},
             'text': 'First anchor.', 'is_context': False}
    second = {'id': 'two', 'source': {'id': 'b'}, 'location': {'page': 3},
              'text': 'Second anchor.', 'is_context': True}
    case = {'source_id': 'a', 'pages': [2], 'text_anchors': ['First anchor'],
            'grounding': [{'source_id': 'a', 'pages': [2], 'text_anchors': ['First anchor']},
                          {'source_id': 'b', 'pages': [3], 'text_anchors': ['Second anchor']}]}
    partial = evidence_coverage([first], case)
    assert partial['coverage'] == .5 and not partial['all_groundings_hit']
    assert partial['groundings'][1]['missing_text_anchors'] == ['Second anchor']
    assert not partial['groundings'][1]['locator_hit']
    assert evidence_coverage([first, second], case)['all_groundings_hit']
    for wrong in [{**second, 'source': {'id': 'a'}},
                  {**second, 'location': {'page': 4}},
                  {**second, 'text': 'Something else.'}]:
        assert evidence_coverage([first, wrong], case)['coverage'] == .5
    # Two anchors at the same source/page remain separate requirements.
    same_page = copy.deepcopy(case)
    same_page['grounding'][1].update(source_id='a', pages=[2])
    assert not evidence_coverage([first], same_page)['all_groundings_hit']
    assert evidence_coverage([first, {**second, 'source': {'id': 'a'},
                                    'location': {'page': 2}}], same_page)['all_groundings_hit']


def test_grounding_coverage_legacy_optional_locators_and_text_boundaries():
    evidence = [{'id': 'one', 'source': {'id': 's'}, 'location': {}, 'text': "L'e\u0301le\u0300ve"},
                {'id': 'two', 'source': {'id': 's'}, 'location': {}, 'text': 'présente son certificat'}]
    case = {'source_id': 's', 'pages': [], 'text_anchors': ['L’élève', 'présente son certificat']}
    assert evidence_coverage(evidence, case)['all_groundings_hit']
    assert evidence_coverage(evidence, {**case, 'grounding': []}) == evidence_coverage(evidence, case)
    assert not evidence_coverage(evidence, {**case, 'text_anchors': ['élève présente']})['all_groundings_hit']
    assert not evidence_coverage(evidence, {**case, 'text_anchors': ['eleve']})['all_groundings_hit']
    assert not evidence_coverage(evidence, {**case, 'evidence_ids': ['absent']})['all_groundings_hit']
    unanchored = evidence_coverage(evidence, {'source_id': 's'})
    assert unanchored['all_groundings_hit'] and unanchored['groundings'][0]['text_anchor_hit'] is None
    assert evidence_coverage([], {'source_id': 's'})['coverage'] == 0


def test_123q_fixture_all_groundings_are_required_without_running_retrieval():
    import json
    from pathlib import Path

    cases = json.loads((Path(__file__).parents[1] / 'fixtures/retrieval-evaluation-123q.json').read_text())
    multi = [c for c in cases if len(c['grounding']) > 1]
    assert multi
    for case in multi:
        evidence = [{'id': str(i), 'source': {'id': g['source_id']},
                     'location': {'page': g['pages'][0] if g['pages'] else None},
                     'text': g['passage_text']} for i, g in enumerate(case['grounding'])]
        assert evidence_coverage(evidence, case)['all_groundings_hit'], case['id']
        assert not evidence_coverage(evidence[:1], case)['all_groundings_hit'], case['id']


def test_retrieved_and_extractive_coverage_diverge_when_output_budget_omits_evidence(tmp_path):
    from knowledge_foundry.embeddings import token_count
    from knowledge_foundry.retrieval.answering import request_payload
    from rag_fixtures import config, projection

    c = config(tmp_path)
    _, evidence, _ = projection()
    first = copy.deepcopy(next(e for e in evidence if e['text'].startswith('Dogs must')))
    first.update(is_context=False, label='E1', context_ids=[], retrieval_passage='p1', retrieval_rank=0)
    second = copy.deepcopy(first)
    second.update(id='second', label='E2', source={**first['source'], 'id': 'other'},
                  text='Second required evidence. ' * 30, retrieval_passage='p2', retrieval_rank=1)
    selected = [first, second]
    c['budget']['output_tokens'] = token_count(first['text'] + ' [E1]', c['budget']['tokenizer']) + 1

    class Store:
        def snapshot(self, corpus, sid):
            return {'id': 'snapshot'}

        def evidence(self, sid, ids):
            return {e['id']: e for e in selected if e['id'] in ids}

    class Assistant:
        config, store = c, Store()

        def retrieve(self, corpus, question, **kwargs):
            if not question:
                raise ValueError('empty question')
            return {'evidence': selected, 'payload': request_payload(c, question, [], selected),
                    'passage_ids': ['p1', 'p2'], 'query': question, 'warnings': [], 'embedding_usage': None}

    case = {'id': 'multi', 'question': 'Both?', 'source_id': first['source']['id'],
            'text_anchors': ['Dogs must'],
            'grounding': [{'source_id': first['source']['id'], 'text_anchors': ['Dogs must']},
                          {'source_id': 'other', 'text_anchors': ['Second required evidence.']}]}
    cases = [case, {**case, 'id': 'error', 'question': ''}]
    original = copy.deepcopy((cases, selected, c))
    compact = evaluate(Assistant(), 'animals', cases, methods=('keyword',), check_excerpts=True)
    traced = evaluate(Assistant(), 'animals', cases, methods=('keyword',), check_excerpts=True, trace=True)
    assert outcomes(compact['metrics']) == outcomes(traced['metrics'])
    row, metrics = compact['rows'][0], compact['metrics'][0]
    assert row['hit'] and row['text_anchor_hit'] and row['excerpts']['text_anchor_hit']
    assert row['retrieved_evidence_coverage']['all_groundings_hit']
    assert row['excerpts']['evidence_coverage']['coverage'] == .5
    assert row['excerpts']['warnings']
    assert metrics['cases'] == 2 and metrics['completed'] == 1
    assert metrics['retrieved_coverage_cases'] == metrics['extractive_coverage_cases'] == 1
    assert metrics['retrieved_required_groundings'] == metrics['extractive_required_groundings'] == 2
    assert metrics['retrieved_covered_groundings'] == 2 and metrics['extractive_covered_groundings'] == 1
    assert metrics['retrieved_grounding_coverage'] == metrics['retrieved_all_groundings_hit_rate'] == 1
    assert metrics['extractive_grounding_coverage'] == .5
    assert metrics['extractive_all_groundings_hits'] == metrics['extractive_all_groundings_hit_rate'] == 0
    assert row['retrieved_evidence_coverage'] == traced['rows'][0]['retrieved_evidence_coverage']
    assert row['excerpts']['evidence_coverage'] == traced['rows'][0]['excerpts']['evidence_coverage']
    unrun = evaluate(Assistant(), 'animals', cases, methods=('keyword',))['metrics'][0]
    assert unrun['extractive_coverage_cases'] == 0 and unrun['extractive_grounding_coverage'] is None
    failed = evaluate(Assistant(), 'animals', cases[1:], methods=('keyword',), check_excerpts=True)['metrics'][0]
    assert failed['retrieved_coverage_cases'] == 0 and failed['retrieved_all_groundings_hit_rate'] is None
    assert (cases, selected, c) == original


def test_coverage_aggregation_distinguishes_groundings_from_questions():
    evidence = [{'id': 'one', 'source': {'id': 'a'}, 'location': {'page': 1}, 'text': 'Anchor'}]
    single = {'source_id': 'a', 'text_anchors': ['Anchor']}
    multi = {**single, 'grounding': [single, {'source_id': 'b', 'text_anchors': ['Other']}]}
    rows = [{'retrieved_evidence_coverage': evidence_coverage(evidence, c)} for c in [single, multi]]
    metrics = coverage_metrics(rows, 'retrieved')
    assert metrics['retrieved_coverage_cases'] == 2
    assert metrics['retrieved_required_groundings'] == 3
    assert metrics['retrieved_covered_groundings'] == 2
    assert metrics['retrieved_grounding_coverage'] == pytest.approx(2 / 3)
    assert metrics['retrieved_all_groundings_hits'] == 1
    assert metrics['retrieved_all_groundings_hit_rate'] == .5
