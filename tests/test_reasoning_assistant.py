"""Offline assistant contracts and deterministic replay; no provider or production DB calls."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from threading import Thread

import pytest

from knowledge_foundry.assistant.config import ROOT, load_config
from knowledge_foundry.assistant.datasets import DatasetRegistry, digest, read
from knowledge_foundry.assistant.llm import LanguageModels, ModelError, ROUTE_SCHEMA, TEXT_SCHEMA
from knowledge_foundry.assistant.service import Assistant, DocumentEvidence
from knowledge_foundry.assistant.search import scenario_text


@pytest.fixture(scope='module')
def registry():
    registry = DatasetRegistry()
    registry.searcher.embedder = FixtureEmbeddings(registry.policies)
    return registry


class FixtureEmbeddings:
    """Controlled vectors for orchestration tests, not a semantic retrieval model."""
    def __init__(self, records):
        cases = sorted({r['candidate_id'].split('/')[1] for r in records.values()})
        vectors = {cid: [float(i == index) for i in range(len(cases))]
                   for index, cid in enumerate(cases)}
        self.text_vectors = {scenario_text(row): vectors[row['candidate_id'].split('/')[1]] for row in records.values()}
        self.query_vectors = {r['text']: vectors[f"{r['family_id']}-{r['category']}-01"]
                              for r in QUESTIONS.values() if f"{r['family_id']}-{r['category']}-01" in vectors}
        self.default = vectors['KYC-OH-01']
        self.vectors = vectors
        self.telemetry = {'provider': 'fixture', 'model': 'controlled-vectors',
                          'requests': 0, 'cache_hits': 0, 'input_tokens': 0}

    def embed(self, records):
        return [{'structural_chunk_id': r['id'], 'vector': self.text_vectors[r['text']]} for r in records]

    def embed_query(self, question):
        return self.query_vectors.get(question, next((v for cid, v in self.vectors.items() if cid in question),
                                                     self.default))


class FakeDocuments:
    def __init__(self, available=False, status='insufficient'):
        self.available, self.status, self.calls = available, status, []

    def preview(self, question):
        return {'available': self.available, 'passages': [], 'snapshot_id': 'fixture-snapshot'}

    def answer(self, question, history, snapshot_id=None):
        self.calls.append((question, history, snapshot_id))
        return {'status': self.status, 'text': 'Fixture document answer.', 'citations': []}


RUNTIME_FIXTURES = ROOT / 'tests/fixtures/reasoning-assistant-runtime-v1.json'
QUESTIONS = {r['question_id']: r for r in json.loads(RUNTIME_FIXTURES.read_text())}


def runtime_prompt(family='KYC', category='OH', language='en'):
    return QUESTIONS[f'runtime-{family}-{category}-{language}']['text']


def evidence(quote, message_id='current'):
    return {'message_id': message_id, 'quote': quote}


def decision(case=None, *, route='reasoning', language='en', candidate_id=None, predicate=None, args=None,
             general_requested=False, text=None, facts=None, entities=None, variables=None, select=None):
    if case:
        candidate_id, predicate, args = 'synthetic-v1/KYC-OH-01/en', 'identity_check_complete', ['A']
    text = text or runtime_prompt()
    if route == 'reasoning':
        candidate_id = candidate_id or 'synthetic-v1/KYC-OH-01/en'
        predicate, args = predicate or 'identity_check_complete', args or ['A']
        facts = facts if facts is not None else [('document_verified', ['A'], "A's identity document is verified.")]
        entities = entities or [('A', 'customer A' if 'customer A' in text else "A's identity document")]
    return {'route': route, 'language': language, 'candidate_id': candidate_id, 'general_requested': general_requested,
            'context_match': 'supported_policy' if route == 'reasoning' else 'none', 'use_history': False,
            'interpretation': 'Assess the question under the supplied example policy.',
            'clarification': 'Which policy and customer facts should I use?' if route == 'clarify' else None,
            'predicate': predicate, 'args': args or [], 'variables': variables or [], 'select': select or [],
            'question_evidence': evidence(text.split('\n\n')[-1]) if route == 'reasoning' else None,
            'scope_evidence': None,
            'entities': [{'name': name, 'type': 'entity', 'evidence': evidence(quote)} for name, quote in
                         (entities or [])] if route == 'reasoning' else [],
            'facts': [{'predicate': pred, 'args': arguments, 'evidence': evidence(quote)} for pred, arguments, quote in
                      (facts or [])] if route == 'reasoning' else []}


def deduction_starter_decision():
    from apps.assistant.app import EXAMPLES

    text = EXAMPLES[1][2]
    return decision(candidate_id='synthetic-v1/PEP-OH-01/fr', language='fr', text=text,
        predicate='approval_required', args=['un client'],
        facts=[('pep', ['un client'], 'un client est enregistré comme PPE.')],
        entities=[('un client', 'un client est enregistré comme PPE.')])


class FixtureModels(LanguageModels):
    def __init__(self, value, config=None, wording=None):
        self.value, self.wording, self.requests = value, wording, []
        super().__init__(config or load_config(), self.respond)

    def respond(self, payload, step):
        self.requests.append(deepcopy(payload))
        role = payload['text']['format']['name']
        data = json.loads(payload['input'])
        if role == 'question_interpretation':
            value = deepcopy(self.value)
            # A fixture simulates semantic interpretation; it is not the deployed router.
            if value['candidate_id']:
                semantic = value['candidate_id'].rsplit('/', 1)[0]
                matching = [c['candidate_id'] for c in data['candidates'] if c['candidate_id'].rsplit('/', 1)[0] == semantic]
                if matching:
                    value['candidate_id'] = matching[0]
        elif role == 'answer_verbalization':
            value = {'status': data['outcome']['status'], 'answer': 'Under the example policy: ' + data['outcome']['status'],
                     'steps': [{'proof_id': s['proof_id'], 'text': s['text']} for s in data['proof_steps']]}
            if self.wording:
                self.wording(value)
        else:
            value = {'text': 'A fact is supplied; an inference follows from facts and rules.'}
        return {'status': 'completed', 'model': step['model'], 'output_text': json.dumps(value),
                'usage': {'input_tokens': 100, 'output_tokens': 40}}


def make_assistant(registry, value, documents=None, wording=None):
    models = FixtureModels(value, wording=wording)
    return Assistant(registry=registry, models=models, documents=documents or FakeDocuments()), models


def test_runtime_never_deserializes_answers_queries_or_saved_results(monkeypatch):
    opened = []
    original = Path.read_text

    def record(path, *args, **kwargs):
        opened.append(path.name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', record)
    registry = DatasetRegistry()
    assert len(registry.records) == 242
    assert not any(name.startswith(('queries-', 'results-', 'proposed-answers-', 'proposed-proofs-'))
                   or 'reference' in name for name in opened)


def test_accepted_prerequisites_and_unaccepted_packages_fail_closed(tmp_path):
    config = read(ROOT / 'config/aml-cft-reasoning-datasets.json')
    config['datasets'][0]['acceptance_sha256'] = '0' * 64
    path = tmp_path / 'registry.json'
    path.write_text(json.dumps(config))
    # Registry paths cannot escape the project, including absolute temp paths.
    with pytest.raises(ValueError, match='inside the repository'):
        DatasetRegistry(path)
    with pytest.raises(ValueError, match='acceptance hash'):
        registry = object.__new__(DatasetRegistry)
        registry.records, registry.packages, registry.engine = {}, {}, None
        registry._load(config['datasets'][0])


@pytest.mark.parametrize('failure, expected', [
    ('hash', 'accepted prerequisite hash differs: source_index_path'),
    ('status', 'acceptance identity/selection differs'),
    ('format', 'inconsistent acceptance format'),
    ('passage', 'unresolved source passage'),
])
def test_bo_pins_and_provenance_fail_closed(monkeypatch, failure, expected):
    from knowledge_foundry.assistant import datasets

    entry = read(ROOT / 'config/aml-cft-reasoning-datasets.json')['datasets'][1]

    def tampered_digest(path):
        return '0' * 64 if str(path.relative_to(ROOT)) == entry['source_index_path'] else digest(path)

    def inconsistent_record(path):
        value = read(path)
        name = str(path.relative_to(ROOT))
        if failure == 'status' and name == entry['status_path']:
            value['languages'] = ['fr']
        elif failure == 'format' and name == entry['acceptance_path']:
            value['decision'] = 'accepted'
        elif failure == 'passage' and name == entry['source_index_path']:
            del value['passages']['CMF-R561-1-P1']
        return value

    if failure == 'hash':
        monkeypatch.setattr(datasets, 'digest', tampered_digest)
    else:
        monkeypatch.setattr(datasets, 'read', inconsistent_record)
    with pytest.raises(ValueError, match=expected):
        DatasetRegistry()


CASES = [{'question': runtime_prompt(), 'case_id': 'KYC-OH-01', 'route': 'reasoning'}]


def test_supplied_text_constructs_runtime_facts_and_provenance(registry):
    assistant, models = make_assistant(registry, decision())
    answer = assistant.ask(runtime_prompt())
    assert answer['route'] == 'reasoning'
    assert answer['status'] == 'entailed'
    assert answer['checker']['valid']
    assert answer['context']['case_id'].startswith('user-')
    assert answer['encoding']['facts'] == [{'id': 'user-fact-1-e1', 'source_ids': ['user-fact-1'],
        'atom': {'predicate': 'document_verified', 'args': ['A']}}]
    assert answer['input_provenance']['facts'][0]['text'] in runtime_prompt()
    assert answer['input_provenance']['question']['text'] == runtime_prompt().split('\n\n')[-1]
    assert all(s['source_ids'][0].startswith('user-fact-') for s in answer['proof_steps'] if s['kind'] == 'assertion')
    payload = json.loads(models.requests[0]['input'])
    assert all('scenario_facts' not in c and 'entities' not in c['vocabulary'] for c in payload['candidates'])
    assert all(request['store'] is False for request in models.requests)


@pytest.mark.parametrize('category,predicate,facts,status', [
    ('MP', 'onboarding_complete', [('identity_check_complete', ['A'], "A's identity check is complete.")], 'unknown'),
    ('CF', 'identity_verified', [('identity_verified', ['A'], "A's identity is verified."),
                               ('identity_unverified', ['A'], "A's identity is explicitly unverified.")], 'conflict'),
])
def test_unknown_and_conflict_are_terminal(registry, category, predicate, facts, status):
    text = runtime_prompt(category=category)
    value = decision(candidate_id=f'synthetic-v1/KYC-{category}-01/en', predicate=predicate, facts=facts,
                     entities=[('A', facts[0][2])], text=text)
    assistant, models = make_assistant(registry, value, CoveredDocuments())
    answer = assistant.ask(text)
    assert answer['status'] == status
    assert answer['checker']['valid']
    assert answer['diagnostics']
    assert len(models.requests) == 2


@pytest.mark.parametrize('mutation', ['unretrieved', 'predicate', 'entity', 'question_fact', 'duplicate_variable', 'missing_quote'])
def test_unsupported_interpretations_clarify_without_execution(registry, mutation):
    value = decision()
    if mutation == 'unretrieved':
        value['candidate_id'] = 'synthetic-v1/NOT-A-POLICY/en'
    elif mutation == 'predicate':
        value['predicate'] = 'invented_legal_approval'
    elif mutation == 'entity':
        value['args'] = ['InventedCustomer']
    elif mutation == 'question_fact':
        value['facts'][0]['evidence'] = value['question_evidence']
    elif mutation == 'missing_quote':
        value['facts'][0]['evidence'] = evidence('Not supplied by the user')
    else:
        value['variables'] = [{'name': '?x', 'type': 'entity'}] * 2
    assistant, models = make_assistant(registry, value)
    answer = assistant.ask(runtime_prompt())
    assert answer['route'] == 'clarify'
    assert 'result' not in answer
    assert len(models.requests) == 1


def test_invalid_wording_preserves_checked_result(registry):
    def corrupt(value):
        value['status'] = 'conflict'
    assistant, _ = make_assistant(registry, decision(), wording=corrupt)
    answer = assistant.ask(runtime_prompt())
    assert answer['status'] == 'entailed'
    assert answer['checker']['valid']
    assert 'Supported conclusion' in answer['text']
    assert answer['warnings']


def test_unavailable_or_insufficient_documents_do_not_become_general_answers(registry):
    for available in (False, True):
        assistant, models = make_assistant(registry, decision(route='documents'), FakeDocuments(available))
        result = assistant.ask('What do the regulatory documents require?')
        assert result['route'] == 'documents'
        assert result['status'] == ('insufficient' if available else 'unavailable')
        assert len(models.requests) == 1


class CoveredDocuments(FakeDocuments):
    def __init__(self, status='draft'):
        super().__init__(available=True, status=status)
        self.passage = {'label': 'E1', 'text': "COSI : Communication systématique d'informations.",
                        'source': {'title': 'Corpus glossary', 'version': 'fixture'}, 'location': {'page': 1}}

    def preview(self, question):
        return {**super().preview(question), 'passages': [self.passage]}

    def answer(self, question, history, snapshot_id=None):
        answer = super().answer(question, history, snapshot_id)
        if self.status == 'draft':
            answer.update(text=self.passage['text'] + ' [E1]', citations=[self.passage])
        return answer


@pytest.mark.parametrize('question,language', [
    ('What is a COSI?', 'en'), ('Qu’est-ce qu’une COSI ?', 'fr'),
    ('Explain this term from the retrieved glossary.', 'en'),
])
def test_corpus_answer_precedes_misclassified_general_route(registry, question, language):
    documents = CoveredDocuments()
    assistant, models = make_assistant(registry, decision(route='general', language=language), documents)
    result = assistant.ask(question)
    assert result['route'] == 'documents'
    assert result['citations'] == [documents.passage]
    assert len(documents.calls) == 1
    assert len(models.requests) == 1  # No general-model answer after a cited corpus answer.
    assert documents.calls[0][2] == 'fixture-snapshot'
    assert result['telemetry']['routing'] == {'proposed': 'general', 'selected': 'documents'}


def test_general_answer_requires_document_insufficiency_when_preview_matches(registry):
    documents = CoveredDocuments(status='insufficient')
    assistant, models = make_assistant(registry, decision(route='general'), documents)
    result = assistant.ask('Explain a concept outside the retrieved evidence.')
    assert documents.calls
    assert result['route'] == 'general'
    assert len(models.requests) == 2
    assert result['telemetry']['document_check']['status'] == 'insufficient'


def test_explicit_general_knowledge_request_keeps_its_source_preference(registry):
    documents = CoveredDocuments()
    assistant, models = make_assistant(registry, decision(route='general', general_requested=True), documents)
    result = assistant.ask('Use your general knowledge instead of the corpus: what is a COSI?')
    assert result['route'] == 'general'
    assert not documents.calls
    assert len(models.requests) == 2


@pytest.mark.parametrize('status', ['citation_validation_failed', 'error', 'unavailable'])
def test_failed_document_check_never_silently_becomes_general(registry, status):
    documents = CoveredDocuments(status=status)
    assistant, models = make_assistant(registry, decision(route='general'), documents)
    result = assistant.ask('What is a COSI?')
    assert result['route'] == 'documents'
    assert result['status'] == status
    assert len(models.requests) == 1


def test_preview_prefers_matching_passages_over_leading_context(monkeypatch):
    from knowledge_foundry.retrieval.service import DocumentAssistant
    context = [{'label': f'E{i}', 'text': 'Surrounding context', 'source': {}, 'location': {},
                'element_kind': 'paragraph', 'is_context': True} for i in range(1, 7)]
    match = {'label': 'E7', 'text': "COSI : Communication systématique d'informations.",
             'source': {}, 'location': {}, 'element_kind': 'paragraph', 'is_context': False}
    monkeypatch.setattr(DocumentAssistant, 'retrieve', lambda *args, **kwargs: {
        'snapshot_id': 'fixture-snapshot', 'evidence': context + [match]})
    adapter = DocumentEvidence(load_config()['documents'])
    adapter.assistant = DocumentAssistant(object())
    preview = adapter.preview('What is a COSI?')
    assert preview['passages'][0]['label'] == 'E7'


def test_preview_searches_content_terms_instead_of_english_question_words(monkeypatch):
    from knowledge_foundry.retrieval.service import DocumentAssistant
    queries = []

    def retrieve(self, corpus, query, **options):
        queries.append(query)
        return {'snapshot_id': 'fixture-snapshot', 'evidence': []}

    monkeypatch.setattr(DocumentAssistant, 'retrieve', retrieve)
    adapter = DocumentEvidence(load_config()['documents'])
    adapter.assistant = DocumentAssistant(object())
    assert adapter.preview('What is a COSI?')['available']
    assert queries == ['cosi']


def test_history_excludes_assistant_conclusions_and_empty_history_isolates(registry):
    assistant, models = make_assistant(registry, decision())
    first = assistant.ask(runtime_prompt())
    history = [{'role': 'user', 'text': runtime_prompt()}, {'role': 'assistant', 'text': 'INVENTED CONCLUSION',
               'candidate_id': first['candidate_id'], 'route': 'reasoning'}]
    second = assistant.ask(runtime_prompt(), history)
    assert second['encoding']['case_id'] != first['encoding']['case_id']
    payload = json.loads(models.requests[2]['input'])
    assert 'INVENTED CONCLUSION' not in json.dumps(payload)
    assert all(m['role'] == 'user' for m in payload['history'])
    independent = assistant.ask(runtime_prompt(), history=[])
    assert independent['encoding']['case_id'] not in {first['encoding']['case_id'], second['encoding']['case_id']}
    assert json.loads(models.requests[4]['input'])['history'] == []


def test_local_only_rejects_cloud_config_and_disables_document_adapter(registry, tmp_path):
    config = load_config()
    config['local_only'] = True
    path = tmp_path / 'assistant.json'
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='requires local providers'):
        load_config(path)
    for step in config['steps'].values():
        step.update(provider='local', endpoint='http://127.0.0.1:8000/v1/responses', reasoning_effort=None)
    config['scenario_search']['embedding'].update(provider='local', endpoint='http://127.0.0.1:8000/v1/embeddings')
    path.write_text(json.dumps(config))
    config = load_config(path)
    models = FixtureModels(decision(route='documents'), config)
    assistant = Assistant(config, registry=registry, models=models, documents=FakeDocuments(True))
    assert assistant.documents is None
    assert assistant.ask('What do the documents require?')['status'] == 'unavailable'
    assert 'reasoning' not in models.requests[0]
    config['steps']['general']['endpoint'] = 'https://external.example/v1/responses'
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='loopback'):
        load_config(path)


@pytest.mark.parametrize('body', [
    {'status': 'incomplete', 'output_text': '{}'},
    {'status': 'completed', 'output': [{'content': [{'type': 'refusal', 'refusal': 'No'}]}]},
    {'status': 'completed', 'output_text': '{bad json'},
    {'status': 'completed', 'output_text': '{"route":"invented"}'},
    [],
])
def test_incomplete_refused_malformed_or_schema_invalid_output_is_rejected(body):
    models = LanguageModels(load_config(), lambda payload, step: body)
    with pytest.raises(ModelError):
        models.call('question_interpretation', 'assistant_route_v1.txt', {'question': 'Test'}, ROUTE_SCHEMA)
    assert models.records[-1]['status'] == 'failed'


def test_full_payload_input_budget_prevents_calls():
    config = load_config()
    config['steps']['question_interpretation']['max_input_tokens'] = 1
    calls = []
    models = LanguageModels(config, lambda payload, step: calls.append(payload))
    with pytest.raises(ModelError, match='input budget'):
        models.call('question_interpretation', 'assistant_route_v1.txt', {'question': 'Test'}, ROUTE_SCHEMA)
    assert calls == []


def test_document_adapter_reuses_generation_without_writing_sessions():
    class Retrieval:
        config = {'answer': {'provider': 'extractive'}, 'budget': {'tokenizer': 'cl100k_base', 'output_tokens': 1600}}
        answer_stream = None

        def retrieve(self, corpus, question, **options):
            assert options['snapshot_id'] == 'pinned-snapshot'
            assert options['route_inventory'] is True
            evidence = {'id': 'passage', 'label': 'E1', 'text': 'Exact supported passage.',
                        'element_kind': 'paragraph', 'context_ids': [], 'source': {'title': 'Fixture'}}
            return {'snapshot_id': 'pinned-snapshot', 'evidence': [evidence],
                    'payload': {'input': json.dumps({'evidence': [evidence]})}}

        def __getattr__(self, name):
            raise AssertionError('Unexpected persistence/service access: ' + name)

    adapter = DocumentEvidence(load_config()['documents'])
    adapter.assistant = Retrieval()
    result = adapter.answer('Question', [], 'pinned-snapshot')
    assert result['status'] == 'extractive'
    assert 'Exact supported passage.' in result['text']
    assert result['snapshot_id'] == 'pinned-snapshot'


def test_preview_disables_embedding_reranking_and_model_rewriting(monkeypatch):
    from knowledge_foundry.retrieval.service import DocumentAssistant
    captured = []

    def retrieve(self, corpus, question, **options):
        captured.append((self.config, options))
        return {'snapshot_id': 'preview-snapshot', 'evidence': []}

    monkeypatch.setattr(DocumentAssistant, 'retrieve', retrieve)
    adapter = DocumentEvidence(load_config()['documents'])
    # Do not construct a real database store for this boundary test.
    adapter.assistant = DocumentAssistant(object())
    assert adapter.preview('Question')['available']
    config, options = captured[0]
    assert config['answer']['provider'] == 'extractive'
    assert config['search']['rerank'] is False
    assert options == {'method': 'keyword', 'history': []}


@pytest.mark.parametrize('redirect', [False, True])
def test_local_http_transport_sends_no_cloud_key_and_refuses_redirects(monkeypatch, redirect):
    captured = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            captured.append({'authorization': self.headers.get('Authorization'),
                             'payload': json.loads(self.rfile.read(int(self.headers['Content-Length'])))})
            self.send_response(302 if redirect else 200)
            if redirect:
                self.send_header('Location', 'https://external.example/v1/responses')
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'completed', 'output_text': '{"text":"Local result"}'}).encode())

    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-be-forwarded-to-local')
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = load_config()
        config['local_only'] = True
        for step in config['steps'].values():
            step.update(provider='local', endpoint=f'http://127.0.0.1:{server.server_port}/v1/responses', reasoning_effort=None)
        config['scenario_search']['embedding'].update(provider='local', endpoint='http://127.0.0.1:8000/v1/embeddings')
        models = LanguageModels(config)
        if redirect:
            with pytest.raises(ModelError):
                models.call('general', 'assistant_general_v1.txt', {'question': 'Hello'}, TEXT_SCHEMA)
        else:
            assert models.call('general', 'assistant_general_v1.txt', {'question': 'Hello'}, TEXT_SCHEMA)['text'] == 'Local result'
        assert len(captured) == 1
        assert captured[0]['authorization'] is None
        assert captured[0]['payload']['store'] is False
        assert 'reasoning' not in captured[0]['payload']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
