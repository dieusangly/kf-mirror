"""Vector ranking/cache/privacy boundaries, with controlled vectors and no paid calls."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from threading import Thread

import pytest

from knowledge_foundry.assistant.config import load_config
from knowledge_foundry.assistant.search import ScenarioEmbeddings, ScenarioSearch
from knowledge_foundry.embeddings import EmbeddingAdapter


def record(cid, language, text):
    return {'case_id': cid, 'package_id': 'fixture', 'language': language,
            'scope': {'text': 'Fictional policy.'}, 'scenario_facts': [{'text': text}],
            'policy_clauses': [{'text': 'Apply the supplied rule.'}],
            'reference_answer': 'must never be embedded', 'queries': ['must never be embedded']}


class ControlledEmbeddings:
    def __init__(self, vectors, query):
        self.vectors, self.query = vectors, query
        self.inputs = []
        self.telemetry = {'provider': 'fixture', 'model': 'controlled', 'requests': 0,
                          'cache_hits': 0, 'input_tokens': 0}

    def embed(self, records):
        self.inputs.extend(deepcopy(records))
        return [{'structural_chunk_id': r['id'], 'vector': self.vectors[r['id']]} for r in records]

    def embed_query(self, question):
        return self.query


def test_cosine_ranking_without_word_overlap_and_bilingual_deduplication():
    records = {'one/en': record('ONE', 'en', 'Politically exposed person.'),
               'one/fr': record('ONE', 'fr', 'Personne politiquement exposée.'),
               'two/en': record('TWO', 'en', 'Identity verification.')}
    embedder = ControlledEmbeddings({'one/en': [10, 0], 'one/fr': [1, 0], 'two/en': [0, 1]}, [3, 0])
    search = ScenarioSearch(records, load_config()['scenario_search'], embedder=embedder)
    assert search.search('Does a senior signoff apply?', limit=2) == ['one/en', 'two/en']
    assert search.telemetry['candidates'][0]['cosine_similarity'] == 1
    assert 'must never be embedded' not in json.dumps(embedder.inputs)
    assert len(embedder.inputs) == 3
    search.search('Another question', limit=1)
    assert len(embedder.inputs) == 3  # Corpus vectors are retained in memory.


def test_exact_case_ids_take_precedence_without_matching_longer_ids():
    records = {cid: record(cid, 'en', 'A supplied fact.') for cid in ('KYC-01', 'KYC-010', 'OTHER')}
    embedder = ControlledEmbeddings({'KYC-01': [0, 1], 'KYC-010': [1, 0], 'OTHER': [1, 0]}, [1, 0])
    search = ScenarioSearch(records, load_config()['scenario_search'], embedder=embedder)
    assert search.search('Explain kyc-01.', limit=1, preferred='OTHER') == ['KYC-01']
    assert search.search('Explain KYC-010.', limit=1) == ['KYC-010']
    assert search.search('And what about its approval?', limit=1, preferred='KYC-01') == ['KYC-01']


def embedding_config():
    config = deepcopy(load_config()['scenario_search']['embedding'])
    config.update(dimensions=2, expected_dimensions=2)
    return config


def test_persistent_cache_reuses_scenarios_but_never_writes_questions(tmp_path, monkeypatch):
    calls = []

    def request(self, texts):
        calls.append(texts)
        return {'data': [{'index': i, 'embedding': [1, 0]} for i in range(len(texts))],
                'usage': {'prompt_tokens': 4}}

    monkeypatch.setattr(ScenarioEmbeddings, '_request', request)
    inputs = [{'id': 'scenario', 'text': 'The fictional customer is a PEP.'}]
    config = embedding_config()
    adapter = ScenarioEmbeddings(config, tmp_path)
    adapter.embed(inputs)
    adapter.embed_query('Private question about customer 12345')
    adapter.embed_query('Private question about customer 12345')
    assert len(calls) == 2
    saved = list(tmp_path.glob('*.json'))
    assert len(saved) == 1
    assert 'Private question' not in saved[0].read_text()
    ScenarioEmbeddings(config, tmp_path).embed(inputs)
    assert len(calls) == 2
    config['model'] = 'different-model'
    ScenarioEmbeddings(config, tmp_path).embed(inputs)
    assert len(calls) == 3
    ScenarioEmbeddings(config, tmp_path).embed([{'id': 'scenario', 'text': 'Changed scenario facts.'}])
    assert len(calls) == 4
    config.update(provider='local', endpoint='http://127.0.0.1:8000/v1/embeddings')
    ScenarioEmbeddings(config, tmp_path).embed(inputs)
    config['endpoint'] = 'http://127.0.0.1:9000/v1/embeddings'
    ScenarioEmbeddings(config, tmp_path).embed(inputs)
    assert len(calls) == 6  # Endpoint changes cannot reuse another server's cache.


@pytest.mark.parametrize('vector', [[0, 0], [float('nan'), 1], [1], [True, 0]])
def test_invalid_vectors_are_rejected(vector, tmp_path, monkeypatch):
    monkeypatch.setattr(ScenarioEmbeddings, '_request', lambda *args: {
        'data': [{'index': 0, 'embedding': vector}], 'usage': {'prompt_tokens': 1}})
    with pytest.raises(ValueError):
        ScenarioEmbeddings(embedding_config(), tmp_path).embed_query('Question')


def test_embedding_failure_is_terminal_before_routing(registry, monkeypatch):
    from test_reasoning_assistant import CASES, decision, make_assistant

    assistant, models = make_assistant(registry, decision(CASES[0]))
    monkeypatch.setattr(registry.searcher.embedder, 'embed_query', lambda text: (_ for _ in ()).throw(
        ValueError('provider detail that must remain hidden')))
    answer = assistant.ask(CASES[0]['question'])
    assert answer['route'] == 'error'
    assert answer['telemetry']['scenario_search']['status'] == 'failed'
    assert not models.requests
    assert 'provider detail' not in json.dumps(answer)


@pytest.fixture
def registry():
    from test_reasoning_assistant import FixtureEmbeddings
    from knowledge_foundry.assistant.datasets import DatasetRegistry

    registry = DatasetRegistry()
    registry.searcher.embedder = FixtureEmbeddings(registry.records)
    return registry


def test_embedding_budget_is_checked_before_a_request(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ScenarioEmbeddings, '_request', lambda *args: calls.append(args))
    config = embedding_config()
    config['max_input_tokens'] = 1
    with pytest.raises(ValueError, match='input budget'):
        ScenarioEmbeddings(config, tmp_path).embed_query('This question is too long for the configured budget.')
    assert not calls


def test_local_only_rejects_cloud_embeddings_even_with_local_language_models(tmp_path):
    config = load_config()
    config['local_only'] = True
    for step in config['steps'].values():
        step.update(provider='local', endpoint='http://127.0.0.1:8000/v1/responses', reasoning_effort=None)
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='local providers for embeddings'):
        load_config(path)


@pytest.mark.parametrize('redirect', [False, True])
def test_local_embedding_http_uses_no_cloud_key_or_redirects(tmp_path, monkeypatch, redirect):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append((self.headers.get('Authorization'),
                          json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(302 if redirect else 200)
            if redirect:
                self.send_header('Location', 'https://external.example/v1/embeddings')
            self.end_headers()
            self.wfile.write(json.dumps({'data': [{'index': 0, 'embedding': [1, 0]}],
                                        'usage': {'prompt_tokens': 3}}).encode())

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-be-forwarded')
    config = embedding_config()
    config.update(provider='local', endpoint=f'http://127.0.0.1:{server.server_port}/v1/embeddings')
    try:
        adapter = ScenarioEmbeddings(config, tmp_path, local_only=True)
        if redirect:
            with pytest.raises(ValueError):
                adapter.embed_query('Question')
        else:
            assert adapter.embed_query('Question') == [1, 0]
        assert len(calls) == 1
        assert calls[0][0] is None
        assert calls[0][1]['model'] == config['model']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_offline_evaluator_never_requests_embeddings(monkeypatch):
    from scripts.evaluate_reasoning_assistant import evaluate, ROOT

    def forbidden(*args):
        raise AssertionError('Offline evaluation must not call an embedding model')

    monkeypatch.setattr(EmbeddingAdapter, '_request', forbidden)
    monkeypatch.setattr(ScenarioEmbeddings, '_request', forbidden)
    report = evaluate('config/reasoning-datasets.json', ROOT / 'tests/fixtures/reasoning-routing-v1.json')
    assert report['passed']
    assert report['external_calls'] == 0
    assert report['baseline']['passed'] == 192
    assert report['candidate_recall_at_6'] is None
