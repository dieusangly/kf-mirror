"""Development fixtures, case construction and runner checks; no real-model scoring."""
import json

import pytest

from scripts.evaluate_reasoning_assistant_inputs import execute_questions, validate_agreement
from test_reasoning_assistant import (QUESTIONS, decision, evidence, runtime_prompt, make_assistant, registry,
                                      deduction_starter_decision)


def run(registry, text, value):
    assistant, _ = make_assistant(registry, value)
    answer = assistant.ask(text, history=[])
    assert answer['route'] == 'reasoning', answer
    assert answer['checker']['valid']
    return answer


def bo_case(language, *, fallback=False, missing=False, conflict=False, day=6, covered=True):
    if language == 'en':
        company = 'Arden is an unlisted company registered in France.'
        institution = 'Arden is a client of Rivage, an obliged financial institution.' if covered else (
            'Arden is a client of Rivage, a non-financial consultant.')
        assessed = f'The assessment date is {day} July 2026.'
        natural_person = 'Nora is a natural person.'
        holding = "Nora directly holds more than 25% of Arden's capital through ordinary shares on the nominal basis."
        contrary = "Another assessment says Nora directly holds at most 25% of Arden's capital through ordinary shares on the nominal basis."
        question = 'Does Nora qualify as a beneficial owner of Arden under the capital criterion?'
        sas = 'Arden is an SAS and Nora is its president.'
        completed = 'Rivage completed the full first-paragraph assessment of Arden.'
        outcome = 'Rivage identified no natural person under the first paragraph for Arden.'
        clearance = 'Rivage explicitly has no suspicion of money laundering or terrorist financing concerning Arden.'
        fallback_question = 'Does Nora qualify as a fallback beneficial owner of Arden?'
        interest, basis = 'ordinary shares', 'nominal basis'
    else:
        company = 'Arden est une société non cotée immatriculée en France.'
        institution = 'Arden est cliente de Rivage, un établissement financier assujetti.' if covered else (
            'Arden est cliente de Rivage, un cabinet de conseil.')
        assessed = f'La date de l’évaluation est le {day} juillet 2026.'
        natural_person = 'Nora est une personne physique.'
        holding = 'Nora détient directement plus de 25 % du capital d’Arden en actions ordinaires sur la base nominale.'
        contrary = 'Selon une autre évaluation, Nora détient directement au plus 25 % du capital d’Arden en actions ordinaires sur la base nominale.'
        question = 'Nora est-elle bénéficiaire effective d’Arden au titre de la détention du capital ?'
        sas = 'Arden est une SAS dont Nora est la présidente.'
        completed = 'Rivage a achevé l’évaluation complète d’Arden au titre du premier alinéa.'
        outcome = 'Rivage n’a identifié aucune personne physique au titre du premier alinéa pour Arden.'
        clearance = 'Rivage déclare expressément ne soupçonner ni blanchiment de capitaux ni financement du terrorisme pour Arden.'
        fallback_question = 'Nora est-elle bénéficiaire effective de dernier ressort d’Arden ?'
        interest, basis = 'actions ordinaires', 'base nominale'

    assertions = [company, institution, assessed, natural_person]
    facts = [] if missing and not fallback else [('natural_person', ['Nora'], natural_person)]
    entities = [('Nora', 'person', natural_person), ('Arden', 'company', company)]
    if fallback:
        case, predicate, question = 'BO-RC-EX-02', 'fallback_beneficial_owner', fallback_question
        assertions.extend([sas, outcome, clearance])
        entities.append(('Rivage', 'institution', institution))
        dated = ['Rivage', 'Arden', '2026-07-06']
        facts.extend([('sas', ['Arden'], sas), ('sas_president', ['Nora', 'Arden'], sas),
                      ('no_natural_person_identified_under_paragraph_one', dated, outcome),
                      ('no_ml_or_tf_suspicion', dated, clearance)])
        if not missing:
            assertions.append(completed)
            facts.append(('completed_paragraph_one_assessment', dated, completed))
    else:
        case, predicate = 'BO-RC-CF-01', 'capital_beneficial_owner'
        if missing:
            assertions.remove(natural_person)
            entities[0] = ('Nora', 'person', holding)
        assertions.append(holding)
        entities.extend([(interest, 'interest', holding), (basis, 'measurement_basis', holding)])
        dated = ['Nora', 'Arden', interest, '2026-07-06', basis]
        facts.append(('direct_capital_above_25', dated, holding))
        if conflict:
            assertions.append(contrary)
            facts.append(('direct_capital_at_or_below_25', dated, contrary))
    text = '\n'.join(assertions) + '\n\n' + question
    value = decision(candidate_id=f'real-claims-bo-v1/{case}/{language}', language=language, text=text,
                     predicate=predicate, args=['Nora', 'Arden'], facts=facts)
    value['entities'] = [{'name': name, 'type': kind, 'evidence': evidence(quote)} for name, kind, quote in entities]
    value['scope_evidence'] = {'company': 'Arden', 'institution': 'Rivage', 'company_evidence': evidence(company),
                               'institution_evidence': evidence(institution), 'date_evidence': evidence(assessed)}
    return text, value, case


def bo_assistant(registry, language, **scenario):
    text, value, case = bo_case(language, **scenario)
    embedder = registry.searcher.embedder
    embedder.query_vectors[text] = embedder.vectors[case]
    assistant, models = make_assistant(registry, value)
    return text, assistant, models


def test_full_bo_candidate_set_shares_scopes_and_fits_router_budget(registry, monkeypatch):
    text, value, _ = bo_case('en')
    selected = [registry.candidate(key) for key in registry.policies
                if key.startswith('real-claims-bo-v1/') and key.endswith('/en')]
    assert len(selected) == 6
    monkeypatch.setattr(registry, 'search', lambda *_: list(selected))
    assistant, models = make_assistant(registry, value)
    passages = [{'label': f'E{i}', 'source': {'title': 'Fixture AML corpus'},
                 'text': 'The article describes covered institutions and beneficial ownership assessments. ' * 25,
                 'location': {'article_id': str(i)}} for i in range(1, 6)]
    monkeypatch.setattr(assistant.documents, 'preview',
                        lambda _: {'available': True, 'snapshot_id': 'fixture-snapshot', 'passages': passages})

    answer = assistant.ask(text)
    assert answer['route'] == 'reasoning', answer.get('diagnostic', answer)
    assert answer['checker']['valid']
    request = json.loads(models.requests[0]['input'])
    assert len(request['candidates']) == 22
    assert request['documents']['passages'] == passages
    assert len(request['scopes']) == 2
    for candidate in request['candidates']:
        expected = registry.policies[candidate['candidate_id']]['scope']
        assert request['scopes'][candidate['scope_ref']] == {k: v for k, v in expected.items() if k != 'id'}
    assert answer['context']['scope']['id'] == registry.policies[value['candidate_id']]['scope']['id']
    assert answer['telemetry']['calls'][0]['input_tokens_estimate'] <= (
        assistant.config['steps']['question_interpretation']['max_input_tokens'])


@pytest.mark.parametrize('language', ['en', 'fr'])
@pytest.mark.parametrize('scenario, status', [
    ({}, 'entailed'), ({'missing': True}, 'unknown'), ({'conflict': True}, 'conflict'),
    ({'day': 7}, 'clarification'), ({'covered': False}, 'clarification'),
])
def test_fresh_bo_capital_cases(registry, language, scenario, status):
    text, assistant, models = bo_assistant(registry, language, **scenario)
    answer = assistant.ask(text)
    assert answer['status'] == status, answer
    if status == 'clarification':
        assert answer['route'] == 'clarify' and 'result' not in answer
        return
    assert answer['route'] == 'reasoning' and answer['checker']['valid']
    assert answer['context']['scope']['case']['company'] == 'Arden'
    assert set(answer['encoding']['entities']).isdisjoint({'client_company', 'person_a', 'shared_measurement_basis'})
    assert all(fact['text'] == text[fact['start']:fact['end']] for fact in answer['input_provenance']['facts'])
    payload = models.requests[0]['input']
    assert all(value not in payload for value in ('client_company', 'person_a', 'scenario_facts', 'reference_answer'))
    if status == 'entailed':
        deduction, = [step for step in answer['proof_steps'] if step['kind'] == 'deduction']
        assert deduction['passage_ids'] == ['CMF-L561-2-2-1', 'CMF-R561-1-P1']
        passages = {passage['id']: passage for passage in answer['passages']}
        assert passages['CMF-L561-2-2-1']['excerpts'] == [
            '1° Soit qui contrôlent en dernier lieu, directement ou indirectement, le client ;']
        assert passages['CMF-R561-1-P1']['version'] == 'LEGIARTI000036824564'
    elif status == 'conflict':
        conflict, = answer['result']['conflicts']
        assert conflict['left']['args'] == conflict['right']['args']


@pytest.mark.parametrize('language', ['en', 'fr'])
@pytest.mark.parametrize('missing', [False, True])
def test_fresh_sas_fallback_keeps_completion_and_company_binding(registry, language, missing):
    text, assistant, _ = bo_assistant(registry, language, fallback=True, missing=missing)
    answer = assistant.ask(text)
    assert answer['route'] == 'reasoning' and answer['checker']['valid'], answer
    assert answer['status'] == ('unknown' if missing else 'entailed')
    rule, = answer['encoding']['rules']
    assert rule['variables']['?company'] == 'company'
    assert 'client_company' not in json.dumps(answer['encoding'])
    if missing:
        assert any('completed paragraph one assessment' in item['text'] for item in answer['diagnostics'])
    else:
        step, = [s for s in answer['proof_steps'] if s['kind'] == 'deduction']
        assert step['atom']['args'] == ['Nora', 'Arden']
        completed, = [premise for premise in step['premises'] if premise['predicate'] == 'completed_paragraph_one_assessment']
        assert completed['args'] == ['Rivage', 'Arden', '2026-07-06']
        assert step['passage_ids'] == ['CMF-R561-1-P2', 'CMF-R561-1-D']


@pytest.mark.parametrize('role, value', [('company', 'Albane'), ('institution', 'Brise'), ('date', '2026-07-07')])
def test_fallback_cannot_mix_case_bindings(registry, role, value):
    text, assistant, models = bo_assistant(registry, 'en', fallback=True)
    completion = next(f for f in models.value['facts'] if f['predicate'] == 'completed_paragraph_one_assessment')
    completion['args'] = list(completion['args'])
    completion['args'][['institution', 'company', 'date'].index(role)] = value
    answer = assistant.ask(text)
    assert answer['route'] == 'clarify'
    assert 'same client company, covered institution and assessment date' in answer['text']


@pytest.mark.parametrize('language', ['en', 'fr'])
def test_fallback_outcome_alone_cannot_supply_completion(registry, language):
    text, assistant, models = bo_assistant(registry, language, fallback=True, missing=True)
    outcome = next(fact for fact in models.value['facts']
                   if fact['predicate'] == 'no_natural_person_identified_under_paragraph_one')
    models.value['facts'].append({**outcome, 'predicate': 'completed_paragraph_one_assessment'})
    answer = assistant.ask(text)
    assert answer['route'] == 'clarify'
    assert 'completed the first-paragraph assessment' in answer['text']


def test_bo_requires_an_unambiguous_assessment_date(registry):
    text, assistant, models = bo_assistant(registry, 'en')
    date_evidence = models.value['scope_evidence']['date_evidence']
    new_quote = 'The assessment date is 06/07/2026.'
    changed = text.replace(date_evidence['quote'], new_quote)
    date_evidence['quote'] = new_quote
    registry.searcher.embedder.query_vectors[changed] = registry.searcher.embedder.query_vectors[text]
    assert assistant.ask(changed)['route'] == 'clarify'


@pytest.mark.parametrize('company_type, status', [('company', 'entailed'), ('institution', 'clarification')])
def test_bo_voting_rights_keeps_unused_scope_entities_out_of_engine(registry, company_type, status):
    base_text, value, _ = bo_case('en')
    holding = 'Nora directly holds more than 25% of Arden’s voting rights.'
    question = 'Does Nora qualify as a beneficial owner of Arden under the voting-rights criterion?'
    text = ' '.join(base_text.splitlines()[:4] + [holding]) + '\n' + question
    value.update(candidate_id='real-claims-bo-v1/BO-RC-OH-02/en', predicate='voting_rights_beneficial_owner',
                 question_evidence=evidence(question))
    value['facts'] = value['facts'][:1] + [
        {'predicate': 'direct_voting_rights_above_25', 'args': ['Nora', 'Arden'], 'evidence': evidence(holding)}]
    value['entities'] = value['entities'][:2] + [
        {'name': 'Rivage', 'type': 'institution', 'evidence': value['scope_evidence']['institution_evidence']},
        {'name': '6 July 2026', 'type': 'date', 'evidence': value['scope_evidence']['date_evidence']},
    ]
    value['entities'][1]['type'] = company_type
    embedder = registry.searcher.embedder
    embedder.query_vectors[text] = embedder.vectors['BO-RC-OH-02']
    assistant, _ = make_assistant(registry, value)

    answer = assistant.ask(text)
    assert answer['status'] == status, answer['text']
    if status == 'entailed':
        assert answer['checker']['valid']
        assert answer['encoding']['entities'] == {'Nora': 'person', 'Arden': 'company'}
        assert answer['input_provenance']['scope']['institution'] == 'Rivage'
        assert answer['input_provenance']['scope']['assessment_date'] == '2026-07-06'
    else:
        assert answer['text'] == 'Unsupported entity type.'


@pytest.mark.parametrize('case, target, premises, extra_entities, deductions', [
    ('BO-RC-DL-01', 'register_lists_beneficial_owner', [
        ('register_lists_beneficial_owner', ['Nora', 'Arden'],
         'The supplied register lists Nora as a beneficial owner of Arden.'),
    ], {}, 0),
    ('BO-RC-OH-02', 'voting_rights_beneficial_owner', [
        ('indirect_voting_rights_above_25', ['Nora', 'Arden'],
         'Nora indirectly holds more than 25% of voting rights in Arden.'),
    ], {}, 1),
    ('BO-RC-MH-01', 'meeting_control_beneficial_owner', [
        ('general_meeting_voting_rights_at_disposal', ['Nora', 'Arden', 'meeting voting rights'],
         'The meeting voting rights of Arden are at Nora’s disposal.'),
        ('determines_general_meeting_decisions_through_rights', ['Nora', 'Arden', 'meeting voting rights'],
         'Nora actually determines decisions at Arden’s general meetings through these meeting voting rights.'),
    ], {'meeting voting rights': 'voting_rights'}, 2),
    ('BO-RC-MH-02', 'removal_power_beneficial_owner', [
        ('company_shareholder', ['Nora', 'Arden'], 'Nora is a shareholder of Arden.'),
        ('power_over_majority_of_company_body', ['Nora', 'Arden', 'remove', 'supervisory board'],
         'The articles give Nora the power to remove a majority of Arden’s supervisory board.'),
    ], {'supervisory board': 'company_body'}, 2),
])
def test_fresh_bo_policy_catalog(registry, case, target, premises, extra_entities, deductions):
    base_text, value, _ = bo_case('en')
    question = f'Does the supplied evidence establish the {target.replace("_", " ")} criterion for Nora at Arden?'
    assertions = base_text.splitlines()[:4] + [quote for _, _, quote in premises]
    text = '\n'.join(assertions) + '\n\n' + question
    facts = (value['facts'][:1] if deductions else []) + [
        {'predicate': predicate, 'args': args, 'evidence': evidence(quote)} for predicate, args, quote in premises]
    entities = value['entities'][:2] + [
        {'name': name, 'type': kind, 'evidence': evidence(next(quote for quote in assertions if name in quote))}
        for name, kind in extra_entities.items()]
    value.update(candidate_id=f'real-claims-bo-v1/{case}/en', predicate=target, question_evidence=evidence(question),
                 facts=facts, entities=entities)
    embedder = registry.searcher.embedder
    embedder.query_vectors[text] = embedder.vectors[case]
    answer = run(registry, text, value)
    assert answer['status'] == 'entailed'
    steps = [step for step in answer['proof_steps'] if step['kind'] == 'deduction']
    assert len(steps) == deductions
    assert all(step['passage_ids'] for step in steps)


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
    text = runtime_prompt(category='DL')
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
    text = runtime_prompt('BO', 'MH')
    value = decision(candidate_id='synthetic-v1/BO-MH-01/en', text=text,
        predicate='controller_verification_required', args=['P', 'A'],
        facts=[('controls', ['P', 'H'], 'P controls H.'), ('controls', ['H', 'A'], 'H controls A.')],
        entities=[('P', 'P controls H.'), ('H', 'P controls H.'), ('A', 'H controls A.')])
    answer = run(registry, text, value)
    assert answer['status'] == 'entailed'
    assert any(s.get('binding') == {'?c': 'A', '?h': 'H', '?p': 'P'} for s in answer['proof_steps'])
    assert len(answer['encoding']['facts']) == 2


def test_exception_cannot_supply_clearance_by_silence(registry):
    text = runtime_prompt(category='EX')
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
    text = runtime_prompt(category='DL', language='fr')
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
    answer = assistant.ask(runtime_prompt())
    assert answer['status'] == 'entailed'
    assert 'SAVED CUSTOMER POISON' not in json.dumps(answer)
    assert 'SAVED CUSTOMER POISON' not in json.dumps(models.requests)
    assert answer['encoding']['facts'][0]['source_ids'] == ['user-fact-1']


def test_no_supplied_facts_yields_unknown_even_when_saved_case_has_facts(registry):
    value = decision(facts=[])
    answer = run(registry, runtime_prompt(), value)
    assert answer['status'] == 'unknown'
    assert answer['encoding']['facts'] == []
    # This is a deliberately incomplete mocked interpretation, not model quality.


def test_different_named_entity_is_declared_from_literal_user_text_only(registry):
    # The same development direct-lookup prompt also names a document. Every entity is
    # new to the empty policy template; none is copied from the pilot declaration.
    assert registry.policies['synthetic-v1/KYC-DL-01/en']['template']['entities'] == {}
    value = decision()
    value['entities'][0]['name'] = 'Bob'
    assistant, _ = make_assistant(registry, value)
    assert assistant.ask(runtime_prompt())['route'] == 'clarify'


def test_fact_evidence_cannot_hide_inside_the_interrogative(registry):
    value = decision()
    value['facts'][0]['evidence'] = evidence('identity check for A')
    # Deliberately shorten query evidence to test the sentence-level guard too.
    value['question_evidence'] = evidence('Under this policy')
    assistant, _ = make_assistant(registry, value)
    assert assistant.ask(runtime_prompt())['route'] == 'clarify'


def test_history_evidence_requires_explicit_continuation_in_same_policy(registry):
    value = decision()
    value['facts'][0]['evidence']['message_id'] = 'history-0'
    assistant, _ = make_assistant(registry, value)
    history = [{'role': 'user', 'text': runtime_prompt()},
               {'role': 'assistant', 'text': 'Not evidence', 'candidate_id': value['candidate_id']}]
    assert assistant.ask(runtime_prompt(), history)['route'] == 'clarify'
    value['use_history'] = True
    answer = assistant.ask(runtime_prompt(), history)
    assert answer['status'] == 'entailed'
    assert answer['input_provenance']['facts'][0]['message_id'] == 'history-0'
    assert assistant.ask(runtime_prompt(), history=[])['route'] == 'clarify'


def test_runner_submits_only_original_text_with_empty_history_and_retains_failures(tmp_path):
    questions = list(QUESTIONS.values())
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
    assert summary['denominator'] == len(QUESTIONS) and summary['failed'] == 1
    assert len(summary['unexecuted']) == len(QUESTIONS) - 3
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
    question = QUESTIONS['runtime-KYC-OH-en']
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

    summary = execute_questions(Forbidden(), list(QUESTIONS.values()), tmp_path / 'run', {}, max_prompts=len(QUESTIONS),
                                budget=1, reserve=.1, verify_snapshot=unavailable)
    assert summary['attempted'] == 0
    assert len(summary['unexecuted']) == len(QUESTIONS)


def test_failed_checker_withholds_conclusion_and_retains_input_trace(registry, monkeypatch):
    monkeypatch.setattr(registry.engine, 'check_result', lambda *args: {'valid': False})
    assistant, models = make_assistant(registry, decision())
    answer = assistant.ask(runtime_prompt())
    assert answer['route'] == 'error'
    assert 'result' not in answer and 'proof_steps' not in answer
    assert answer['encoding']['facts'][0]['source_ids'] == ['user-fact-1']
    assert answer['query']['queries'][0]['target']['predicate'] == 'identity_check_complete'
    assert len(models.requests) == 1


def test_policy_clause_cannot_be_encoded_as_customer_fact(registry):
    value = decision()
    value['facts'][0]['evidence'] = evidence("If a customer's identity document is verified, that customer's identity check is complete.")
    assistant, _ = make_assistant(registry, value)
    assert assistant.ask(runtime_prompt())['route'] == 'clarify'


def test_reset_discards_private_query_cache(registry):
    assistant, _ = make_assistant(registry, decision())
    registry.searcher.embedder.queries = {runtime_prompt(): [1, 0]}
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

    summary = execute_questions(Backend(), list(QUESTIONS.values()), tmp_path / 'run', {}, max_prompts=len(QUESTIONS),
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
