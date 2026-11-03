"""Evaluation scheduling and recovery use synthetic evidence without model calls."""
import copy
import json
import threading

import pytest

from knowledge_foundry.retrieval.answering import request_payload
from knowledge_foundry.retrieval.checkpoint import EvaluationCheckpoint
from knowledge_foundry.retrieval.evaluation import evaluate
from rag_fixtures import config, projection


@pytest.fixture
def setup(tmp_path):
    c = config(tmp_path)
    seed = copy.deepcopy(next(e for e in projection()[1] if e['text'].startswith('Dogs must')))
    seed.update(label='E1', is_context=False, context_ids=[])
    cases = [{'id': str(i), 'question': f'Question {i}', 'source_id': seed['source']['id'],
              'text_anchors': ['Dogs must rest']} for i in range(4)]

    class Store:
        sid = 'snapshot'

        def snapshot(self, corpus, sid):
            return {'id': self.sid}

        def evidence(self, sid, ids):
            return {seed['id']: copy.deepcopy(seed)} if seed['id'] in ids else {}

    class Assistant:
        def __init__(self, hook=None):
            self.config, self.store, self.calls, self.hook = copy.deepcopy(c), Store(), [], hook

        def retrieve(self, corpus, question, **options):
            self.calls.append((question, copy.deepcopy(options)))
            if self.hook:
                self.hook(question, options)
            return {'evidence': [copy.deepcopy(seed)], 'passage_ids': ['p'], 'query': question,
                    'payload': request_payload(self.config, question, [], [seed]),
                    'warnings': [], 'embedding_usage': None}

    return Assistant, cases


def outcomes(report):
    def without_latency(value):
        if isinstance(value, dict):
            return {k: without_latency(v) for k, v in value.items() if not k.endswith('latency_seconds')}
        if isinstance(value, list):
            return [without_latency(v) for v in value]
        return value

    result = without_latency(report)
    for key in ('workers', 'resumed_cases'):
        result.pop(key)
    return result


def test_real_parallel_retrieval_keeps_rank_usage_evidence_and_citations_isolated(store, published):
    from knowledge_foundry.embeddings import EmbeddingAdapter
    from knowledge_foundry.retrieval.service import DocumentAssistant
    from knowledge_foundry.structural_chunks import stable_hash

    c, sid = published
    c['search'].update(candidate_k=4, top_k=2, rerank=True, minimum_vector_similarity=-1.)
    c['answer']['provider'] = 'openai'
    source = store.snapshot('animals')['identity']['sources'][0]['id']
    cases = [{'id': str(i), 'question': question, 'source_id': source, 'text_anchors': ['Dogs must rest']}
             for i, question in enumerate(['Dogs rest exercise', 'Canine recovery proposition'])]
    fixture = {'embedding_vectors': {stable_hash(case['question']): [1., 0., 0.] for case in cases}}
    EmbeddingAdapter(c['embedding'], c['cache_dir'], fixture).embed(
        [{'id': case['id'], 'text': case['question']} for case in cases])

    def rank(payload, config):
        value = json.loads(payload['input'])
        yield {'type': 'delta', 'text': json.dumps({'selected_indices': [r['index'] for r in value['candidates'][:2]]})}
        yield {'type': 'usage', 'usage': {'input_tokens': len(value['question']), 'output_tokens': 5}}

    assistant = DocumentAssistant(store, c, embedding_fixture=fixture, ranking_stream=rank)
    options = dict(snapshot_id=sid, modes=('passage', 'claim', 'summary', 'passage-and-claim'),
                   methods=('keyword', 'vector', 'combined'), trace=True, check_excerpts=True)
    serial = evaluate(assistant, 'animals', cases, **options)
    parallel = evaluate(assistant, 'animals', cases, workers=4, **options)
    assert outcomes(parallel) == outcomes(serial)
    assert all('error' not in row for row in parallel['rows'])
    for row in parallel['rows']:
        if row['ranking']:
            assert row['ranking']['usage']['input_tokens'] == len(row['query'])
        if row['excerpts']['citation_audit']['citations']:
            assert row['excerpts']['citation_audit']['citation_resolution_rate'] == 1


@pytest.mark.parametrize('trace', [False, True])
def test_parallel_cases_overlap_with_bounded_workers_and_keep_serial_results(setup, trace):
    Assistant, cases = setup
    barrier, lock = threading.Barrier(2), threading.Lock()
    active = peak = 0

    def hook(question, options):
        nonlocal active, peak
        assert options['snapshot_id'] == 'snapshot'
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=5)
        with lock:
            active -= 1

    options = dict(methods=('keyword', 'vector', 'combined'), modes=('passage', 'passage-and-claim'),
                   trace=trace, check_excerpts=True)
    serial = evaluate(Assistant(), 'animals', cases, **options)
    parallel = evaluate(Assistant(hook), 'animals', cases, workers=2, **options)
    assert peak == 2
    assert outcomes(parallel) == outcomes(serial)
    assert [r['id'] for r in parallel['rows']] == ['0', '1', '2', '3'] * 6


def test_checkpoint_survives_interruption_skips_successes_and_retries_errors(setup, tmp_path):
    Assistant, cases = setup
    path = tmp_path / 'checkpoint'

    def fail(question, options):
        if question == cases[1]['question']:
            raise ValueError('temporary failure')

    def interrupt(done, total, row):
        if done == 3:
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        evaluate(Assistant(fail), 'animals', cases, methods=('keyword',),
                 checkpoint_path=path, on_progress=interrupt)
    assert len(list(path.glob('case-*.json'))) == 3
    resumed, progress = Assistant(), []
    report = evaluate(resumed, 'animals', cases, methods=('keyword',), workers=2,
                      checkpoint_path=path, resume=True, on_progress=lambda *event: progress.append(event))
    assert report['resumed_cases'] == 2 and report['workers'] == 2
    assert {q for q, _ in resumed.calls} == {cases[i]['question'] for i in (1, 3)}
    assert progress[0] == (2, 4, None) and progress[-1][0:2] == (4, 4)
    assert outcomes(report) == outcomes(evaluate(Assistant(), 'animals', cases, methods=('keyword',)))
    complete = Assistant(lambda *args: pytest.fail('finished cases must not run again'))
    reused = evaluate(complete, 'animals', cases, methods=('keyword',), checkpoint_path=path, resume=True)
    assert reused['resumed_cases'] == 4 and reused['rows'] == report['rows']
    assert reused['metrics'] == report['metrics']


@pytest.mark.parametrize('change', ['config', 'cases', 'snapshot', 'corpus', 'methods', 'modes',
                                    'trace', 'check_excerpts', 'sequential'])
def test_resume_rejects_changed_inputs_before_new_retrieval(setup, tmp_path, change):
    Assistant, cases = setup
    path = tmp_path / 'checkpoint'
    evaluate(Assistant(), 'animals', cases, methods=('keyword',), checkpoint_path=path)
    assistant = Assistant(lambda *args: pytest.fail('mismatched checkpoints must not retrieve'))
    corpus, options = 'animals', {'methods': ('keyword',)}
    if change == 'config':
        assistant.config['search']['top_k'] += 1
    elif change == 'cases':
        cases = [{**cases[0], 'question': 'Changed question'}, *cases[1:]]
    elif change == 'snapshot':
        assistant.store.sid = 'different'
    elif change == 'corpus':
        corpus = 'other'
    elif change in {'methods', 'modes'}:
        options[change] = ('combined',) if change == 'methods' else ('claim',)
    else:
        options[change] = True
    with pytest.raises(ValueError, match='inputs or settings changed'):
        evaluate(assistant, corpus, cases, checkpoint_path=path, resume=True, **options)


@pytest.mark.parametrize('damage', ['row', 'truncated', 'list', 'case', 'index'])
def test_resume_rejects_corrupt_or_misplaced_results(setup, tmp_path, damage):
    from knowledge_foundry.structural_chunks import stable_hash

    Assistant, cases = setup
    path = tmp_path / 'checkpoint'
    evaluate(Assistant(), 'animals', cases, methods=('keyword',), checkpoint_path=path)
    result = path / 'case-000000.json'
    value = json.loads(result.read_text())
    if damage == 'row':
        value['row']['query'] = 'changed'
    elif damage == 'case':
        value['row']['id'] = 'different'
        value['sha256'] = stable_hash(value['row'])
    elif damage == 'index':
        result = path / 'case-000999.json'
        value['index'] = 999
    result.write_text('{' if damage == 'truncated' else '[]' if damage == 'list' else json.dumps(value))
    with pytest.raises(ValueError):
        evaluate(Assistant(lambda *args: pytest.fail('corrupt results must fail before retrieval')),
                 'animals', cases, methods=('keyword',), checkpoint_path=path, resume=True)


def test_checkpoint_requires_explicit_resume_and_exclusive_writer(setup, tmp_path):
    Assistant, cases = setup
    path = tmp_path / 'checkpoint'
    evaluate(Assistant(), 'animals', cases, methods=('keyword',), checkpoint_path=path)
    with pytest.raises(ValueError, match='exists; use --resume'):
        evaluate(Assistant(), 'animals', cases, methods=('keyword',), checkpoint_path=path)
    manifest = json.loads((path / 'manifest.json').read_text())['identity']
    with EvaluationCheckpoint(path, manifest, resume=True):
        with pytest.raises(ValueError, match='in use'):
            with EvaluationCheckpoint(path, manifest, resume=True):
                pytest.fail('two writers must not share a checkpoint')
    with pytest.raises(ValueError, match='existing evaluation checkpoint'):
        evaluate(Assistant(), 'animals', cases, checkpoint_path=tmp_path / 'absent', resume=True)


@pytest.mark.parametrize('workers', [0, -1, True, 1.5])
def test_invalid_workers_fail_before_retrieval(setup, workers):
    Assistant, cases = setup
    with pytest.raises(ValueError, match='positive integer'):
        evaluate(Assistant(), 'animals', cases, workers=workers)


def test_sequence_keeps_history_order_and_cannot_run_concurrently(setup):
    Assistant, cases = setup
    assistant = Assistant()
    cases[2]['history'] = ['Explicit history']
    evaluate(assistant, 'animals', cases[:3], methods=('keyword', 'vector'), sequential=True)
    assert [options['history'] for _, options in assistant.calls] == [
        [], ['Question 0'], ['Explicit history'], [], ['Question 0'], ['Explicit history']]
    with pytest.raises(ValueError, match='requires workers=1'):
        evaluate(assistant, 'animals', cases, sequential=True, workers=2)
    with pytest.raises(ValueError, match='checkpoint path'):
        evaluate(assistant, 'animals', cases, resume=True)


@pytest.mark.parametrize('quiet', [False, True])
def test_cli_workers_progress_default_checkpoint_and_resume(setup, tmp_path, monkeypatch, capsys, quiet):
    from scripts.evaluate_retrieval import main

    Assistant, cases = setup
    assistant = Assistant()
    monkeypatch.setattr('scripts.evaluate_retrieval.PostgresStore', lambda: assistant.store)
    monkeypatch.setattr('scripts.evaluate_retrieval.DocumentAssistant', lambda *args: assistant)
    cfg, casefile, output = (tmp_path / name for name in ['config.json', 'cases.json', 'report.json'])
    cfg.write_text(json.dumps(assistant.config))
    casefile.write_text(json.dumps(cases))
    args = ['--corpus', 'animals', '--config', str(cfg), '--cases', str(casefile), '--methods', 'keyword',
            '--modes', 'passage', 'claim', 'summary', 'passage-and-claim', '--workers', '2',
            '--check-excerpts', '--trace', '--output', str(output)] + (['--quiet'] if quiet else [])
    report = main(args)
    assert len(report['rows']) == 16 and report['workers'] == 2
    assert json.loads(output.read_text()) == report
    assert len(list(output.with_suffix('.checkpoint').glob('case-*.json'))) == 16
    stderr = capsys.readouterr().err
    assert (stderr == '') is quiet
    if not quiet:
        assert '[0/16] starting' in stderr and '[16/16]' in stderr
    assistant.calls.clear()
    assert main([*args, '--resume'])['resumed_cases'] == 16
    assert assistant.calls == []


@pytest.mark.parametrize('extra', [['--workers', '0'], ['--sequence', '--workers', '2']])
def test_cli_invalid_worker_options_fail_before_database(tmp_path, monkeypatch, extra):
    from scripts.evaluate_retrieval import main

    monkeypatch.setattr('scripts.evaluate_retrieval.PostgresStore', lambda: pytest.fail('invalid arguments must fail early'))
    with pytest.raises(SystemExit) as error:
        main(['--corpus', 'animals', '--cases', 'unused.json', '--output', str(tmp_path / 'report.json'), *extra])
    assert error.value.code == 2
