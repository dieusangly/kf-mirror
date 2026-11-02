import copy

import pytest

from knowledge_foundry.retrieval.evaluation import evaluate, matches_anchors
from knowledge_foundry.retrieval.service import DocumentAssistant


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
    assert traced['trace'] is True and traced['metrics'] == report['metrics']
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
def test_cli_can_compare_search_without_ranking_calls(store, published, tmp_path, monkeypatch, trace):
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
                   '--methods', 'keyword', '--no-rerank', '--check-excerpts', '--output', str(output)]
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
    assert compact['metrics'] == traced['metrics']
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
    from knowledge_foundry.retrieval.evaluation import SnapshotEvaluationStore
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
