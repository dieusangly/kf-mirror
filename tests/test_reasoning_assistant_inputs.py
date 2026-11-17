"""Frozen input path, case construction and runner checks; no real-model scoring."""
import json

import pytest

from scripts.evaluate_reasoning_assistant_inputs import execute_questions, load_questions, validate_agreement
from test_reasoning_assistant import (QUESTIONS, decision, evidence, frozen, make_assistant, registry,
                                      deduction_starter_decision)


def run(registry, text, value):
    assistant, _ = make_assistant(registry, value)
    answer = assistant.ask(text, history=[])
    assert answer['route'] == 'reasoning', answer
    assert answer['checker']['valid']
    return answer


def test_deduction_starter_separates_fact_from_broad_question_citation(registry):
    from apps.assistant.app import EXAMPLES

    text = EXAMPLES[1][2]
    value = deduction_starter_decision()
    assert value['question_evidence']['quote'] == text
    registry.searcher.embedder.query_vectors[text] = registry.searcher.embedder.vectors['PEP-OH-01']
    answer = run(registry, text, value)
    assert answer['status'] == 'entailed'
    assert answer['encoding']['facts'] == [{'id': 'user-fact-1-e1', 'source_ids': ['user-fact-1'],
        'atom': {'predicate': 'pep', 'args': ['un client']}}]
    span = answer['input_provenance']['question']
    assert span['text'] == 'Une approbation sénior est-elle requise ?'
    assert text[span['start']:span['end']] == span['text']


def test_broad_fact_citation_still_cannot_import_the_question(registry):
    from apps.assistant.app import EXAMPLES

    text = EXAMPLES[1][2]
    value = deduction_starter_decision()
    value['question_evidence'] = evidence('Une approbation sénior est-elle requise ?')
    value['facts'][0]['evidence'] = evidence(text)
    registry.searcher.embedder.query_vectors[text] = registry.searcher.embedder.vectors['PEP-OH-01']
    assistant, _ = make_assistant(registry, value)
    answer = assistant.ask(text)
    assert answer['route'] == 'clarify'
    assert 'result' not in answer


def test_direct_lookup_has_no_rules_and_binds_document(registry):
    text = frozen(category='DL')
    value = decision(candidate_id='synthetic-v1/KYC-DL-01/en', text=text,
        predicate='identity_document', args=['A', '?document'],
        variables=[{'name': '?document', 'type': 'entity'}], select=['?document'],
        facts=[('identity_document', ['A', 'passport'], 'Customer A supplied a passport.')],
        entities=[('A', 'Customer A'), ('passport', 'a passport')])
    answer = run(registry, text, value)
    assert not answer['encoding']['rules']
    assert all(s['kind'] == 'assertion' for s in answer['proof_steps'])
    assert answer['result']['results'][0]['answers'] == [{'?document': 'passport'}]
    assert answer['encoding']['entities'] == {'A': 'entity', 'passport': 'entity'}


def test_engine_binds_control_chain_without_identity_mapping(registry):
    text = frozen('BO', 'MH')
    value = decision(candidate_id='synthetic-v1/BO-MH-01/en', text=text,
        predicate='controller_verification_required', args=['P', 'A'],
        facts=[('controls', ['P', 'H'], 'P controls H.'), ('controls', ['H', 'A'], 'H controls A.')],
        entities=[('P', 'P controls H.'), ('H', 'P controls H.'), ('A', 'H controls A.')])
    answer = run(registry, text, value)
    assert answer['status'] == 'entailed'
    assert any(s.get('binding') == {'?c': 'A', '?h': 'H', '?p': 'P'} for s in answer['proof_steps'])
    assert len(answer['encoding']['facts']) == 2


def test_exception_cannot_supply_clearance_by_silence(registry):
    text = frozen(category='EX')
    value = decision(candidate_id='synthetic-v1/KYC-EX-01/en', text=text,
        predicate='identity_action', args=['A', '?action'],
        variables=[{'name': '?action', 'type': 'entity'}], select=['?action'],
        facts=[('document_pending', ['A'], "A's standard identity document is pending."),
               ('identity_exemption', ['A'], 'A has an approved alternative-evidence exemption.')],
        entities=[('A', "A's standard identity document is pending.")])
    answer = run(registry, text, value)
    assert answer['result']['results'][0]['answers'] == [{'?action': 'alternative_review'}]
    assert 'identity_clearance' not in {f['atom']['predicate'] for f in answer['encoding']['facts']}
    assert {'identity_hold', 'alternative_review'} <= set(answer['encoding']['entities'])


def test_french_lookup_preserves_literal_document_entity(registry):
    text = frozen(category='DL', language='fr')
    value = decision(candidate_id='synthetic-v1/KYC-DL-01/fr', language='fr', text=text,
        predicate='identity_document', args=['A', '?document'],
        variables=[{'name': '?document', 'type': 'entity'}], select=['?document'],
        facts=[('identity_document', ['A', 'passeport'], 'Le client A a fourni un passeport.')],
        entities=[('A', 'Le client A'), ('passeport', 'un passeport')])
    answer = run(registry, text, value)
    assert answer['result']['results'][0]['answers'] == [{'?document': 'passeport'}]
    assert 'passport' not in answer['encoding']['entities']


def test_saved_customer_assertions_are_not_runtime_input(registry, monkeypatch):
    for row in registry.records.values():
        monkeypatch.setitem(row, 'scenario_facts', [{'id': 'FORBIDDEN', 'text': 'SAVED CUSTOMER POISON'}])
        monkeypatch.setitem(row['encoding'], 'facts', [{'id': 'FORBIDDEN'}])
    assistant, models = make_assistant(registry, decision())
    answer = assistant.ask(frozen())
    assert answer['status'] == 'entailed'
    assert 'SAVED CUSTOMER POISON' not in json.dumps(answer)
    assert 'SAVED CUSTOMER POISON' not in json.dumps(models.requests)
    assert answer['encoding']['facts'][0]['source_ids'] == ['user-fact-1']


def test_no_supplied_facts_yields_unknown_even_when_saved_case_has_facts(registry):
    value = decision(facts=[])
    answer = run(registry, frozen(), value)
    assert answer['status'] == 'unknown'
    assert answer['encoding']['facts'] == []
    # This is a deliberately incomplete mocked interpretation, not model quality.


def test_different_named_entity_is_declared_from_literal_user_text_only(registry):
    # The same frozen direct-lookup prompt also names a document. Every entity is
    # new to the empty policy template; none is copied from the pilot declaration.
    assert registry.policies['synthetic-v1/KYC-DL-01/en']['template']['entities'] == {}
    value = decision()
    value['entities'][0]['name'] = 'Bob'
    assistant, _ = make_assistant(registry, value)
    assert assistant.ask(frozen())['route'] == 'clarify'


def test_fact_evidence_cannot_hide_inside_the_interrogative(registry):
    value = decision()
    value['facts'][0]['evidence'] = evidence('identity check for A')
    # Deliberately shorten query evidence to test the sentence-level guard too.
    value['question_evidence'] = evidence('Under this policy')
    assistant, _ = make_assistant(registry, value)
    assert assistant.ask(frozen())['route'] == 'clarify'


def test_history_evidence_requires_explicit_continuation_in_same_policy(registry):
    value = decision()
    value['facts'][0]['evidence']['message_id'] = 'history-0'
    assistant, _ = make_assistant(registry, value)
    history = [{'role': 'user', 'text': frozen()},
               {'role': 'assistant', 'text': 'Not evidence', 'candidate_id': value['candidate_id']}]
    assert assistant.ask(frozen(), history)['route'] == 'clarify'
    value['use_history'] = True
    answer = assistant.ask(frozen(), history)
    assert answer['status'] == 'entailed'
    assert answer['input_provenance']['facts'][0]['message_id'] == 'history-0'
    assert assistant.ask(frozen(), history=[])['route'] == 'clarify'


def test_runner_submits_only_original_text_with_empty_history_and_retains_failures(tmp_path):
    questions = load_questions()
    calls = []

    class FakeAssistant:
        def ask(self, text, history):
            calls.append((text, history))
            if len(calls) == 2:
                raise RuntimeError('secret provider body')
            return {'route': 'clarify', 'status': 'clarification', 'text': 'Which policy?'}

    output = tmp_path / 'run'
    summary = execute_questions(FakeAssistant(), questions, output, {'pin': 'code'},
                                 max_prompts=3, budget=2, reserve=.5)
    assert calls == [(q['text'], []) for q in questions[:3]]
    assert summary['denominator'] == 288 and summary['failed'] == 1
    assert len(summary['unexecuted']) == 285
    assert 'secret provider body' not in (output / 'responses.jsonl').read_text()
    resumed = execute_questions(FakeAssistant(), questions, output, {'pin': 'code'},
                                max_prompts=3, budget=2, reserve=.5, resume=True)
    assert resumed['attempted'] == 4 and resumed['failed'] == 1
    assert calls[-1] == (questions[3]['text'], [])
    with pytest.raises(ValueError, match='identity differs'):
        execute_questions(FakeAssistant(), questions, output, {'pin': 'changed'},
                          max_prompts=1, budget=2, reserve=.5, resume=True)


def test_runner_records_real_runtime_encoding_query_and_checker_with_mocked_model(registry, tmp_path):
    assistant, _ = make_assistant(registry, decision())
    question = QUESTIONS['RAE1-reworded-KYC-OH-en']
    execute_questions(assistant, [question], tmp_path / 'run', {'mocked_models': True},
                      max_prompts=1, budget=1, reserve=1)
    row = json.loads((tmp_path / 'run/responses.jsonl').read_text())
    assert row['answer']['checker']['valid']
    assert row['answer']['encoding']['facts'][0]['source_ids'] == ['user-fact-1']
    assert row['answer']['query']['queries'][0]['target']['predicate'] == 'identity_check_complete'
    assert row['answer']['input_provenance']['facts'][0]['text'] in question['text']


def test_snapshot_failure_stops_before_submission(tmp_path):
    class Forbidden:
        def ask(self, *args, **kwargs):
            raise AssertionError('must not submit')

    def unavailable():
        raise ValueError('snapshot changed')

    summary = execute_questions(Forbidden(), load_questions(), tmp_path / 'run', {}, max_prompts=288,
                                budget=1, reserve=.1, verify_snapshot=unavailable)
    assert summary['attempted'] == 0
    assert len(summary['unexecuted']) == 288


def test_failed_checker_withholds_conclusion_and_retains_input_trace(registry, monkeypatch):
    monkeypatch.setattr(registry.engine, 'check_result', lambda *args: {'valid': False})
    assistant, models = make_assistant(registry, decision())
    answer = assistant.ask(frozen())
    assert answer['route'] == 'error'
    assert 'result' not in answer and 'proof_steps' not in answer
    assert answer['encoding']['facts'][0]['source_ids'] == ['user-fact-1']
    assert answer['query']['queries'][0]['target']['predicate'] == 'identity_check_complete'
    assert len(models.requests) == 1


def test_policy_clause_cannot_be_encoded_as_customer_fact(registry):
    value = decision()
    value['facts'][0]['evidence'] = evidence("If a customer's identity document is verified, that customer's identity check is complete.")
    assistant, _ = make_assistant(registry, value)
    assert assistant.ask(frozen())['route'] == 'clarify'


def test_reset_discards_private_query_cache(registry):
    assistant, _ = make_assistant(registry, decision())
    registry.searcher.embedder.queries = {frozen(): [1, 0]}
    assistant.clear_case_context()
    assert registry.searcher.embedder.queries == {}


def test_agreement_requires_exact_effective_configuration_and_budget():
    import hashlib
    from knowledge_foundry.assistant.config import load_config
    from knowledge_foundry.assistant.datasets import digest
    from knowledge_foundry.assistant.config import ROOT
    config = load_config()
    agreement = {'config_sha256': hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
                 'api_budget_usd': 1, 'reserve_usd_per_prompt': .1,
                 'retrieval_config_sha256': digest(ROOT / config['documents']['retrieval_config']),
                 'snapshot_id': 'published', 'cost_reservation_acknowledged': True}
    validate_agreement(config, agreement)
    for field, value in [('config_sha256', 'wrong'), ('api_budget_usd', -1),
                         ('reserve_usd_per_prompt', float('nan')), ('snapshot_id', None),
                         ('cost_reservation_acknowledged', False), ('retrieval_config_sha256', 'wrong')]:
        with pytest.raises(ValueError):
            validate_agreement(config, agreement | {field: value})


def test_document_snapshot_is_published_pinned_and_passed_to_preview(monkeypatch):
    from knowledge_foundry.assistant.config import load_config
    from knowledge_foundry.assistant.service import DocumentEvidence
    from knowledge_foundry.retrieval.service import DocumentAssistant
    active = ['published-one']

    class Store:
        def snapshot(self, corpus, snapshot_id=None):
            return {'id': snapshot_id or active[0], 'status': 'published', 'identity': {'version': 1}}

    adapter = DocumentEvidence(load_config()['documents'])
    adapter.assistant = DocumentAssistant(Store())
    pin = adapter.pin_snapshot('published-one')
    assert pin == adapter.verify_snapshot()
    options_seen = []

    def retrieve(self, corpus, question, **options):
        options_seen.append(options)
        return {'snapshot_id': options['snapshot_id'], 'evidence': []}

    monkeypatch.setattr(DocumentAssistant, 'retrieve', retrieve)
    assert adapter.preview('What is a COSI?')['snapshot_id'] == 'published-one'
    assert options_seen[0]['snapshot_id'] == 'published-one'
    active[0] = 'published-two'
    with pytest.raises(ValueError):
        adapter.verify_snapshot()
    assert adapter.preview('What is a COSI?')['available'] is False


def test_snapshot_change_during_request_records_failure(tmp_path):
    checks = []

    def verify():
        checks.append(True)
        if len(checks) == 2:
            raise ValueError('changed')

    class Backend:
        def ask(self, text, history):
            return {'route': 'documents', 'status': 'draft', 'text': 'Untrusted after snapshot change'}

    summary = execute_questions(Backend(), load_questions(), tmp_path / 'run', {}, max_prompts=288,
                                budget=1, reserve=.1, verify_snapshot=verify)
    assert summary['attempted'] == 1 and summary['failed'] == 1
    row = json.loads((tmp_path / 'run/responses.jsonl').read_text())
    assert row['snapshot_check'] == 'changed or unavailable after request'


def test_general_route_preserves_conversation_without_supplying_assistant_facts(registry):
    assistant, models = make_assistant(registry, decision(route='general', general_requested=True))
    assistant.ask('Explain the distinction again.', history=[
        {'role': 'user', 'text': 'Explain facts and inferences.'},
        {'role': 'assistant', 'text': 'PREVIOUS GENERAL EXPLANATION'}])
    interpretation = json.loads(models.requests[0]['input'])
    general = json.loads(models.requests[1]['input'])
    assert 'PREVIOUS GENERAL EXPLANATION' not in json.dumps(interpretation)
    assert general['history'][-1]['text'] == 'PREVIOUS GENERAL EXPLANATION'
