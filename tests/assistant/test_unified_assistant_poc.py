"""Actual pinned snapshots and SQL; language responses/assessments are MOCKED."""
import copy
import json
import os
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

from knowledge_foundry.assistant.bindings import BindingFailure, KnowledgeBinding
from knowledge_foundry.assistant.service import Assistant, TurnBudget, TurnLimit, failure
from knowledge_foundry.reasoning.catalogue import resolve_checker
from knowledge_foundry.retrieval.config import ROOT, load_config
from knowledge_foundry.storage.postgres import PostgresStore
from knowledge_foundry.structural_chunks import stable_hash
from poc_fixtures import audits, case, document_answer, emit, interpretation, ranking, reasoning_answer, route


@pytest.fixture(scope='module')
def poc_store():
    dsn = os.environ.get('KF_ASSISTANT_POC_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('prepare isolated full-snapshot restore and set KF_ASSISTANT_POC_TEST_DATABASE_URL')
    from scripts.assistant_poc import ISOLATED_DSN, isolated
    assert dsn == ISOLATED_DSN
    return isolated(dsn)


@pytest.fixture(scope='module')
def binding(poc_store):
    b = KnowledgeBinding.load()
    assert b.validate(poc_store)['accepted_mapping_count'] == 2164
    return b


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail('offline Assistant POC tests cannot call a provider')
    for target in ('knowledge_foundry.assistant.service.openai_stream',
                   'knowledge_foundry.reasoning.assistant.openai_stream',
                   'knowledge_foundry.retrieval.answering.openai_stream',
                   'knowledge_foundry.retrieval.query.openai_stream',
                   'knowledge_foundry.retrieval.ranking.openai_stream',
                   'knowledge_foundry.embeddings.EmbeddingAdapter._request'):
        monkeypatch.setattr(target, forbidden)


@pytest.fixture(scope='module')
def proof_dir(tmp_path_factory):
    return tmp_path_factory.mktemp('assistant-poc-proofs')


@pytest.fixture
def context(poc_store, binding, proof_dir):
    token = str(uuid4()) * 2
    cid = poc_store.new_assistant_conversation(token, binding.value)
    assistant = Assistant(poc_store, load_config(), binding, route_stream=route, audit_stream=audits,
        document_stream=document_answer, interpretation_stream=interpretation, reasoning_stream=reasoning_answer,
        reasoning_audit_stream=audits, checker=resolve_checker(), output_dir=proof_dir)
    assistant.ranking_stream = ranking
    def ask(question="C'est quoi une COSI ?", **kwargs):
        return assistant.ask(question, token=token, conversation_id=cid, **kwargs)
    return assistant, ask, cid, token


def test_cosi_actual_full_document_passages_no_symbolic_pipeline(context, poc_store, binding):
    assistant, ask, cid, token = context
    def forbidden(*args): pytest.fail('definitions must not enter symbolic interpretation')
    assistant._accepted = forbidden
    result = ask()
    assert result['status'] == 'answered', result
    assert 'communication systématique' in result['text']
    assert any('critères objectifs' in c['text'] for c in result['citations'])
    assert any("communications systématiques d'information" in c['text'] for c in result['citations'])
    assert {r['phase'] for r in result['telemetry']['requests']} == {
        'assistant_plan', 'assistant_plan_audit', 'document_ranking', 'document_answer', 'document_audit'}
    saved = poc_store.assistant_history(cid, token, binding.value)
    assert len(saved) == 2 and saved[0]['payload']['bindings'] == saved[1]['payload']['bindings'] == binding.value
    assert saved[1]['payload']['citations'] == result['citations']


def test_actual_accepted_assertion_checked_certificate_not_deduction(context):
    _, ask, _, _ = context
    result = ask('Check accepted: ' + case()['question'])
    assert result['status'] == 'answered', result
    assert result['language'] == result['parts'][0]['language'] == 'fr'
    part = result['parts'][0]
    proof = part['retrieval']['reasoning']
    assert part['outcome'] == 'asserted' and part['support'] == 'accepted_source_assertions'
    assert proof['certificate']['status'] == 'accepted' and not proof['rules']
    assert result['citations'] and all(c['element_kind'] != 'heading' for c in result['citations'])


def test_multipart_keeps_both_paths_and_unique_citations_and_turns(context, poc_store, binding):
    _, ask, cid, token = context
    result = ask('Multipart: explain COSI and check the accepted decree assertion')
    assert result['status'] == 'answered' and len(result['parts']) == 2, result
    assert {p['capability'] for p in result['parts']} == {'documents', 'accepted'}
    assert len({c['label'] for c in result['citations']}) == len(result['citations'])
    assert len(poc_store.assistant_history(cid, token, binding.value)) == 2


@pytest.mark.parametrize('outcome', ['citation_failure', 'certificate_failure', 'audit_failure',
                                    'integrity_failure', 'scope_failure', 'validation_failure'])
def test_failed_validation_never_authorizes_fallback_but_other_part_survives(context, monkeypatch, outcome):
    assistant, ask, _, _ = context
    monkeypatch.setattr(assistant, '_accepted', lambda *a: failure(outcome, 'fr', rejected_draft='FORBIDDEN DRAFT'))
    result = ask('Multipart: independently supported explanation and blocked conclusion')
    assert result['status'] == 'partial'
    assert result['parts'][1]['outcome'] == outcome
    assert 'FORBIDDEN DRAFT' not in result['text'] and not result['parts'][1]['citations']
    assert result['citations'] and result['parts'][0]['status'] == 'supported'
    assert sum(r['phase'] == 'document_answer' for r in result['telemetry']['requests']) == 1


def test_explicit_missing_mapping_independently_retrieves_background(context, monkeypatch):
    assistant, ask, _, _ = context
    monkeypatch.setattr(assistant, '_accepted', lambda *a: failure('coverage_gap', 'fr'))
    result = ask('Check accepted: COSI')
    assert result['status'] == 'partial' and result['citations']
    assert result['parts'][0]['coverage_fallback'] == 'symbolic_coverage_gap'
    assert 'remains unestablished' in result['text'] or 'reste non établi' in result['text']


def test_missing_evidence_in_both_capabilities(context, monkeypatch):
    assistant, ask, _, _ = context
    monkeypatch.setattr(assistant, '_accepted', lambda *a: failure('coverage_gap', 'en'))
    result = ask('Check accepted: zyxqvnonexistent987')
    assert result['status'] == 'withheld' and not result['citations']
    assert result['parts'][0]['outcome'] == 'coverage_gap'


def test_citation_and_document_audit_failures_discard_draft(context):
    assistant, ask, _, _ = context
    assistant.document_stream = lambda *args: emit('FORBIDDEN DRAFT [E999999]')
    result = ask()
    assert result['parts'][0]['outcome'] == 'citation_failure' and not result['citations']
    assert 'FORBIDDEN DRAFT' not in result['text']
    assistant.document_stream = document_answer
    def failed_audit(payload, config):
        fields = dict.fromkeys(payload['text']['format']['schema']['properties'], True)
        if payload['text']['format']['name'] == 'document_audit': fields['all_statements_supported'] = False
        yield from emit(fields)
    assistant.audit_stream = failed_audit
    result = ask()
    assert result['parts'][0]['outcome'] == 'audit_failure' and not result['citations']


def test_actual_certificate_rejection_does_not_fallback(context, monkeypatch):
    from knowledge_foundry.reasoning.engine import ReasoningEngine
    assistant, ask, _, _ = context
    monkeypatch.setattr(ReasoningEngine, 'verify_certificate', lambda self: {'status': 'rejected', 'reason': 'OFFLINE fixture'})
    result = ask('Check accepted: ' + case()['question'])
    assert result['status'] == 'withheld' and not result['citations']
    assert result['parts'][0]['outcome'] == 'certificate_failure', result
    assert not any(r['phase'] == 'document_answer' for r in result['telemetry']['requests'])


def test_filters_do_not_broaden_for_either_capability(context, binding):
    assistant, ask, _, _ = context
    bundle = binding.bundle
    source = next(s['id'] for s in bundle['sources'] if 'bcbs' in s['id'])
    doc = next(d['id'] for d in bundle['documents'] if d['source_id'] == source)
    result = ask('COSI', source_ids=[source], document_ids=[doc])
    assert all(c['source']['id'] == source and c['document']['id'] == doc for c in result['citations'])
    assert result['filters'] == {'source_ids': [source], 'document_ids': [doc]}
    result = ask('Check accepted: ' + case()['question'], source_ids=[source], document_ids=[doc])
    assert result['status'] != 'answered'
    assert all(c['source']['id'] == source for c in result['citations'])
    result = ask(source_ids=['unknown-source'])
    assert result['parts'][0]['outcome'] == 'binding_failure' and not result['citations']


def test_followups_use_only_user_context_and_conversations_isolate(context, binding, poc_store):
    assistant, ask, cid, token = context
    ask()
    seen = []
    def recorded(payload, cfg):
        seen.append(json.loads(payload['input']))
        yield from route(payload, cfg)
    assistant.route_stream = recorded
    fr = ask('Et la déclaration de soupçon ?')
    en = ask('Explain that distinction in English')
    assert fr['language'] == 'fr' and en['language'] == 'en'
    assert all('previous_user_questions' in x and all('Une COSI est' not in q for q in x['previous_user_questions']) for x in seen)
    cid2 = poc_store.new_assistant_conversation(token, binding.value)
    assistant.ask('What is COSI?', token=token, conversation_id=cid2)
    assert seen[-1]['previous_user_questions'] == []
    assert len(poc_store.assistant_history(cid, token, binding.value)) == 6
    assert len(poc_store.assistant_history(cid2, token, binding.value)) == 2
    assert {c['id'] for c in PostgresStore(poc_store.dsn).assistant_conversations(token)} == {cid, cid2}


def test_owner_binding_submission_isolation_and_legacy_preservation(context, binding, poc_store):
    _, ask, cid, token = context
    sid = binding.value['documents']['snapshot_id']
    legacy = poc_store.new_session(sid, token, 'document')
    old = {'text': 'Original retained answer', 'status': 'extractive', 'citations': [], 'provenance': 'historical'}
    poc_store.save_turn(legacy, token, sid, 'Original question', old, assistant_mode='document')
    before = copy.deepcopy(poc_store.history(legacy, token, sid, 'document'))
    request = str(uuid4())
    first, second = ask(submission_id=request), ask(submission_id=request)
    assert first['message_id'] == second['message_id']
    assert len(poc_store.assistant_history(cid, token, binding.value)) == 2
    assert poc_store.history(legacy, token, sid, 'document') == before
    assert any(r['id'] == legacy for r in poc_store.legacy_conversations(token))
    for wrong_owner in [str(uuid4()) * 2]:
        with pytest.raises(ValueError): poc_store.assistant_history(cid, wrong_owner, binding.value)
        with pytest.raises(ValueError): poc_store.save_assistant_turn(cid, wrong_owner, binding.value, str(uuid4()), 'x', first)
        assert poc_store.assistant_conversations(wrong_owner) == []
    changed = copy.deepcopy(binding.value); changed['accepted']['dataset_sha256'] = 'wrong'
    with pytest.raises(ValueError): poc_store.assistant_history(cid, token, changed)
    with pytest.raises(ValueError): ask('Different question', submission_id=request)
    with pytest.raises(ValueError): ask(submission_id=request, source_ids=['different-filter'])
    with pytest.raises(ValueError): poc_store.assistant_history(legacy, token, binding.value)


def test_incompatible_pair_and_tampered_evidence_are_hard_failures(binding, poc_store, monkeypatch):
    wrong = KnowledgeBinding(copy.deepcopy(binding.value))
    wrong.value['documents']['snapshot_id'] = binding.value['accepted']['snapshot_id']
    with pytest.raises((ValueError, BindingFailure)): wrong.validate(poc_store)
    row = next(e for e in binding.bundle['elements'] if 'La finalité des COSI est différente' in e['text'])
    # Actual source evidence, modified only in this test's private copy.
    evidence = poc_store.evidence_for_elements(binding.value['documents']['snapshot_id'], {row['id']})[0]
    evidence['text'] += ' invented'
    with pytest.raises(BindingFailure): binding.validate_evidence(evidence)


def test_plan_audit_rejects_before_capabilities_and_limits_apply(context):
    assistant, ask, _, _ = context
    assistant.audit_stream = lambda payload, cfg: emit(dict.fromkeys(payload['text']['format']['schema']['properties'], False))
    result = ask()
    assert result['parts'][0]['outcome'] == 'audit_failure'
    assert len(result['telemetry']['requests']) == 2 and not result['citations']
    oversized = ask('x ' * 10000)
    assert oversized['status'] == 'withheld' and oversized['parts'][0]['outcome'] == 'limit'
    assert oversized['telemetry']['requests'] == []
    budget = TurnBudget(load_config(), {**assistant.binding.value['limits'], 'max_provider_requests': 1})
    payload = {'model': 'gpt-6-luna', 'max_output_tokens': 50, 'input': 'x'}
    list(budget.wrap('fixture', lambda *args: emit('ok'))(payload, load_config()))
    with pytest.raises(TurnLimit): list(budget.wrap('fixture', lambda *args: emit('ok'))(payload, load_config()))
    budget.started -= budget.limits['max_elapsed_seconds']
    with pytest.raises(TurnLimit): budget.check()


def test_inventory_and_no_credentials_source_excerpts(context):
    assistant, ask, _, _ = context
    result = ask('Documents in corpus', intent='document_inventory')
    assert len(result['parts'][0]['documents']) == 13 and result['telemetry']['requests'] == []
    assistant.config['answer']['provider'] = 'extractive'
    assistant.route_stream = None
    # A fresh conversation is required for unambiguous standalone excerpt mode.
    token = str(uuid4()) * 2
    cid = assistant.store.new_assistant_conversation(token, assistant.binding.value)
    result = assistant.ask("C'est quoi une COSI ?", token=token, conversation_id=cid)
    assert result['parts'][0]['verbatim'] and result['citations'] and not result['telemetry']['requests']
    result = assistant.ask('Et ensuite ?', token=token, conversation_id=cid)
    assert result['status'] == 'clarification'


def test_unified_ui_creation_switch_reopen_legacy_and_no_duplicate_turns(poc_store, binding, monkeypatch, tmp_path):
    monkeypatch.setenv('KF_DATABASE_URL', poc_store.dsn)
    monkeypatch.setenv('KF_ASSISTANT_POC_ENABLED', '1')
    cfg = load_config(); cfg['answer']['provider'] = 'extractive'
    path = tmp_path / 'assistant-poc-retrieval.json'; path.write_text(json.dumps(cfg))
    monkeypatch.setenv('KF_RETRIEVAL_CONFIG', str(path))
    # Sealed loader receipt retained; actual DB spans still checked every turn.
    monkeypatch.setattr(KnowledgeBinding, 'load', classmethod(lambda cls, path=None: binding))
    app = AppTest.from_file(str(ROOT / 'apps/chat/app.py'), default_timeout=180).run()
    assert not app.exception and app.title[0].value == 'Assistant'
    assert not any(s.label in {'Assistant', 'Search', 'Retrieve via', 'Answer mode'} for s in app.selectbox)
    assert any('Experimental POC' in i.value for i in app.info)
    app.chat_input[0].set_value("C'est quoi une COSI ?").run()
    assert not app.exception and len(app.chat_message) == 2
    cid = next(s.value for s in app.selectbox if s.label == 'Conversations')
    token = app.query_params['chat_token']
    assert len(poc_store.assistant_history(cid, token, binding.value)) == 2
    next(b for b in app.button if b.label == 'New conversation').click().run()
    assert not app.exception and not app.chat_message
    cid2 = next(s.value for s in app.selectbox if s.label == 'Conversations')
    assert cid2 != cid
    next(s for s in app.selectbox if s.label == 'Conversations').select(cid).run()
    assert not app.exception and len(app.chat_message) == 2
    app.run()
    assert len(poc_store.assistant_history(cid, token, binding.value)) == 2
    app.chat_input[0].set_value('Et ensuite ?').run()
    assert not app.exception and len(app.chat_message) == 4
    legacy = poc_store.new_session(binding.value['documents']['snapshot_id'], token)
    poc_store.save_turn(legacy, token, binding.value['documents']['snapshot_id'], 'Old question',
                        {'text':'Original history', 'status':'extractive','citations':[]})
    app.run()
    next(s for s in app.selectbox if s.label == 'Open previous history').select(legacy).run()
    assert not app.exception and len(app.chat_message) == 2 and not app.chat_input
    next(b for b in app.button if b.label == 'New conversation').click().run()
    assert not app.exception and app.chat_input


def test_aggregate_budget_preserves_complete_independent_part():
    binding = KnowledgeBinding.load()
    binding.value['limits']['max_final_output_tokens'] = 256
    assistant = Assistant(None, load_config(), binding)
    assert assistant.output_dir.name == 'assistant-poc-runs'
    first = {'status': 'supported', 'outcome': 'source_passages', 'text': 'Supported passage. [E1]',
             'citations': [{'id':'synthetic:first','label':'E1'}]}
    second = {'status':'supported','outcome':'source_passages','text':'long output ' * 600 + '[E1]',
              'citations':[{'id':'synthetic:second','label':'E1'}]}
    result = assistant._compose([first, second], 'en')
    assert result['status'] == 'partial' and result['text'].startswith(first['text'])
    assert len(result['citations']) == 1 and result['parts'][1]['outcome'] == 'limit'


def test_concurrent_duplicate_saves_commit_only_one_pair(poc_store):
    from concurrent.futures import ThreadPoolExecutor
    binding = KnowledgeBinding.load().value
    token, request = str(uuid4()) * 2, str(uuid4())
    cid = poc_store.new_assistant_conversation(token, binding)
    def save(index):
        return poc_store.save_assistant_turn(cid, token, binding, request, 'Atomic POC fixture',
            {'text':'Synthetic result ' + str(index), 'bindings':binding, 'filters':{
                'source_ids':None,'document_ids':None},'intent':None})
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(save, range(3)))
    assert len(set(ids)) == 1
    assert len(poc_store.assistant_history(cid, token, binding)) == 2


def test_live_runner_rejects_missing_scope_approval_and_replay_before_transport(tmp_path, monkeypatch):
    import scripts.assistant_poc as runner
    monkeypatch.setattr(runner, 'OUT', tmp_path)
    scope = {'limits':{'spending_cap_usd':1.0}, 'code_config_bindings':{'runtime':'bound'}}
    (tmp_path / 'poc-live-batch.json').write_text(json.dumps({'scope':scope,'scope_sha256':stable_hash(scope)}))
    auth = tmp_path / 'poc-authorization.json'
    auth.write_text(json.dumps({'approved':False}))
    with pytest.raises(ValueError, match='new exact paid approval'):
        runner.live(auth)
    auth.write_text(json.dumps({'approved':True,'scope_sha256':stable_hash(scope),'approved_spending_cap_usd':1.0}))
    monkeypatch.setattr(runner, 'code_bindings', lambda: scope['code_config_bindings'])
    (tmp_path / 'poc-live-started.json').write_text('{}')
    with pytest.raises(ValueError, match='automatic replay'):
        runner.live(auth)
    with pytest.raises(ValueError, match='preserve its original scope'):
        runner.prepare_live()
