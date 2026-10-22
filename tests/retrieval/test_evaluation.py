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
    unrun = evaluate(assistant, 'animals', cases[1:], methods=('keyword',))
    assert unrun['metrics'][0]['excerpt_anchor_hits'] is None


def test_exact_text_checks_respect_the_expected_source_and_location():
    e = {'id': 'e', 'text': 'Only  after\nexercise.', 'source': {'id': 's'}, 'location': {'page': 3}}
    case = {'source_id': 's', 'pages': [3], 'text_anchors': ['only after exercise']}
    assert matches_anchors([e], case)
    assert not matches_anchors([e], {**case, 'pages': [4]})
    assert not matches_anchors([e], {**case, 'source_id': 'other'})


def test_cli_can_compare_search_without_ranking_calls(store, published, tmp_path, monkeypatch):
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
                   '--methods', 'keyword', '--no-rerank', '--check-excerpts', '--output', str(output)])
    assert result['metrics'][0]['hits'] == 1 and result['rows'][0]['ranking'] is None
    assert output.is_file()
