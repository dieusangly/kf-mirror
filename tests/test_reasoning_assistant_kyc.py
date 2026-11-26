"""Fresh KYC dossiers with controlled interpretation and the real proof checker."""
import json

import pytest

from test_reasoning_assistant import decision, evidence, make_assistant, registry


INSTITUTION = 'Banque Rivage'
DOSSIER = 'RV-42'
DATE = '2026-07-06'
INDIVIDUAL = 'Lina Masson'
COMPANY = 'Mésange SAS'
REPRESENTATIVE = 'Aya Bernard'


def kyc_case(language, case, predicate, args, question, facts, *, documents=(), company=False,
             representative=False, date=DATE, special=False):
    client = COMPANY if company else INDIVIDUAL
    if language == 'fr':
        client_type = 'une société française' if company else 'une personne physique'
        client_quote = f'{client} est {client_type}.'
        bank_quote = f'{INSTITUTION} est un établissement de crédit français assujetti à l’article L. 561-2.'
        rep_quote = f'{REPRESENTATIVE} agit au nom de {client} pour cette ouverture.'
        dossier_quote = f'Le dossier {DOSSIER} concerne l’ouverture d’un compte bancaire ordinaire pour {client} chez {INSTITUTION}.'
        regime = ('Une dispense de vigilance simplifiée s’applique.' if special else
                  'Aucun régime spécial ni dispense de vigilance simplifiée ne s’applique.')
        date_quote = f'La date de l’évaluation est le {date}.'
    else:
        client_type = 'a French company' if company else 'an individual'
        client_quote = f'{client} is {client_type}.'
        bank_quote = f'{INSTITUTION} is a French credit institution subject to Article L. 561-2.'
        rep_quote = f'{REPRESENTATIVE} acts on behalf of {client} for this opening.'
        dossier_quote = f'{DOSSIER} is the ordinary bank account opening dossier for {client} at {INSTITUTION}.'
        regime = ('A simplified due diligence exemption applies.' if special else
                  'No special regime or simplified due diligence exemption applies.')
        date_quote = f'The assessment date is {date}.'
    dossier_quote += ' ' + regime
    assertions = [client_quote, bank_quote, dossier_quote, date_quote]
    names = [(client, 'party', client_quote), (INSTITUTION, 'institution', bank_quote),
             (DOSSIER, 'account_dossier', dossier_quote)]
    if representative:
        assertions.append(rep_quote)
        names.append((REPRESENTATIVE, 'party', rep_quote))
    assertions.extend(dict.fromkeys(quote for _, _, quote in facts))
    text = '\n'.join(assertions) + '\n\n' + question
    value = decision(candidate_id=f'real-claims-kyc-v1/{case}/{language}', language=language, text=text,
                     predicate=predicate, args=args, facts=facts)
    value['entities'] = [{'name': name, 'type': kind, 'evidence': evidence(quote)}
                         for name, kind, quote in [*names, *documents]]
    value['scope_evidence'] = {
        'client': client, 'institution': INSTITUTION, 'dossier': DOSSIER,
        'representative': REPRESENTATIVE if representative else None,
        'client_evidence': evidence(client_quote), 'institution_evidence': evidence(bank_quote),
        'dossier_evidence': evidence(dossier_quote), 'date_evidence': evidence(date_quote),
        'representative_evidence': evidence(rep_quote) if representative else None,
    }
    return text, value, case


def fallback_case(language, variant='complete', **scope):
    person = [INSTITUTION, DOSSIER, DATE, INDIVIDUAL]
    document = 'passeport P-17' if language == 'fr' else 'passport P-17'
    other_doc = 'carte Q-52' if language == 'fr' else 'card Q-52'
    certified_doc = other_doc if variant == 'different-document' else document
    if language == 'fr':
        unavailable = f'Aucune mesure directe des 1° à 4° ne peut être mise en œuvre pour {INDIVIDUAL}.'
        copy = f'{INSTITUTION} a obtenu une copie du {document}, pièce d’identité officielle avec photo en cours de validité.'
        certification = f'Un notaire indépendant de {INDIVIDUAL} a vérifié et certifié la copie du {certified_doc}.'
        coverage = 'Les deux mesures corroborent le nom, tous les prénoms, la date et le lieu de naissance.'
        expired = f'Le {document} est expiré à la date de l’évaluation.'
        question = f'{INSTITUTION} a-t-elle vérifié l’identité de {INDIVIDUAL} dans {DOSSIER} ?'
    else:
        unavailable = f'None of the direct measures in points 1 to 4 can be used for {INDIVIDUAL}.'
        copy = f'{INSTITUTION} obtained a copy of {document}, a valid official photo ID.'
        certification = f'A notary independent of {INDIVIDUAL} checked and certified the copy of {certified_doc}.'
        coverage = 'Together the measures corroborate the surname, all given names, birth date and birthplace.'
        expired = f'The {document} was expired at assessment.'
        question = f'Did {INSTITUTION} verify the identity of {INDIVIDUAL} for {DOSSIER}?'
    facts = [('all_direct_measures_unavailable', person, unavailable)]
    documents = [(document, 'document', expired if variant == 'expired' else copy)]
    if variant != 'expired':
        facts.extend([('obtained_qualifying_copy', person + [document], copy),
                      ('document_eligible_for_fallback_verification', person + [document], copy)])
    else:
        certification = f'{expired} {certification}'
        documents = [(document, 'document', certification)]
    if variant == 'different-document':
        documents.append((other_doc, 'document', certification))
    if variant != 'missing':
        facts.append(('copy_checked_and_certified_by_independent_third_party', person + [certified_doc], certification))
        if variant in {'complete', 'different-document'}:
            facts.append(('both_measures_corroborate_all_identification_details', person, coverage))
    return kyc_case(language, 'KYC-RC-MH-02', 'client_identity_verified', person, question, facts,
                    documents=documents, **scope)


def conflict_case(language, *, identity=False):
    parties = [INSTITUTION, DOSSIER, DATE, REPRESENTATIVE, COMPANY]
    if language == 'fr':
        grant = f'Le constat A dit que {REPRESENTATIVE} peut ouvrir seul ce compte au nom de {COMPANY}.'
        deny = f'Le constat B dit que {REPRESENTATIVE} ne peut pas ouvrir seul ce compte au nom de {COMPANY}.'
        attend = f'{REPRESENTATIVE} est venue en personne aux fins d’identification.'
        original = f'{REPRESENTATIVE} a présenté un original officiel valide avec photo corroborant tous ses éléments d’identification.'
        copied = f'{INSTITUTION} a pris copie de cet original.'
        question = (f'L’identité de {REPRESENTATIVE} a-t-elle été vérifiée ?' if identity else
                    f'{REPRESENTATIVE} peut-elle ouvrir seule ce compte au nom de {COMPANY} ?')
    else:
        grant = f'Finding A grants {REPRESENTATIVE} power to open this account alone for {COMPANY}.'
        deny = f'Finding B denies {REPRESENTATIVE} power to open this account alone for {COMPANY}.'
        attend = f'{REPRESENTATIVE} attended physically for identification.'
        original = f'{REPRESENTATIVE} presented a valid original official photo ID corroborating all identification details.'
        copied = f'{INSTITUTION} took a copy of that original.'
        question = (f'Was the identity of {REPRESENTATIVE} verified?' if identity else
                    f'Can {REPRESENTATIVE} open this account alone for {COMPANY}?')
    facts = [
        ('representative_has_power_to_open_account_alone', parties, grant),
        ('representative_lacks_power_to_open_account_alone', parties, deny),
        ('representative_present_for_identification_at_onboarding', parties, attend),
        ('representative_presented_valid_original_official_photo_id_corroborating_details', parties, original),
        ('bank_copied_representative_original_identity_document', parties, copied),
    ]
    return kyc_case(language, 'KYC-RC-CF-02',
                    'representative_identity_verified' if identity else 'representative_has_power_to_open_account_alone',
                    parties, question, facts, company=True, representative=True)


def ask(registry, text, value, case):
    embedder = registry.searcher.embedder
    embedder.query_vectors[text] = embedder.vectors[case]
    assistant, models = make_assistant(registry, value)
    return assistant.ask(text), models


@pytest.mark.parametrize('language', ['en', 'fr'])
@pytest.mark.parametrize('variant', ['complete', 'missing', 'expired', 'different-document'])
def test_fresh_fallback_preserves_document_join_and_conditions(registry, language, variant):
    text, value, case = fallback_case(language, variant)
    answer, models = ask(registry, text, value, case)
    assert answer['route'] == 'reasoning', answer['text']
    assert answer['checker']['valid']
    outcome = 'entailed' if variant == 'complete' else 'unknown'
    assert answer['status'] == outcome
    assert answer['context']['scope']['case']['client'] == INDIVIDUAL
    assert set(answer['encoding']['entities']).isdisjoint({'bank_alpha', 'noor_martin', 'identity_card', 'scoped_account'})
    assert all(text[span['start']:span['end']] == span['text'] for span in answer['input_provenance']['facts'])
    certificate = answer['result']['usable_certificate']
    intermediate = 'independent_certification_qualifies_as_second_measure'
    if outcome == 'entailed':
        assert [step['atom']['predicate'] for step in certificate if 'rule_id' in step] == [
            intermediate, 'client_identity_verified']
        assert {'CMF-R561-5-2-2', 'CMF-R561-5-2-COVERAGE'} <= {source['id'] for source in answer['passages']}
    elif variant in {'expired', 'missing'}:
        assert all(step['atom']['predicate'] != intermediate for step in certificate)
    candidates = json.loads(models.requests[0]['input'])
    assert 'Bank Alpha' not in json.dumps(candidates['scopes'])
    assert 'Noor Martin' not in json.dumps(candidates['scopes'])


@pytest.mark.parametrize('language', ['en', 'fr'])
@pytest.mark.parametrize('identity', [False, True])
def test_fresh_authority_conflict_leaves_separate_identity_proof(registry, language, identity):
    answer, _ = ask(registry, *conflict_case(language, identity=identity))
    assert answer['route'] == 'reasoning', answer['text']
    assert answer['checker']['valid'] and len(answer['result']['conflicts']) == 1
    assert answer['status'] == ('entailed' if identity else 'conflict')
    assert [step['atom']['predicate'] for step in answer['result']['usable_certificate'] if 'rule_id' in step] == [
        'representative_identity_verified']
    assert answer['context']['scope']['case']['representative'] == REPRESENTATIVE
    assert answer['context']['scope']['case']['client'] == COMPANY


@pytest.mark.parametrize('change', ['wrong-date', 'special-regime', 'different-dossier',
                                   'injected-conclusion', 'expired-marked-eligible'])
def test_rejects_unsupported_scope_or_conclusion(registry, change):
    options = {'date': '2026-07-07'} if change == 'wrong-date' else {'special': True} if change == 'special-regime' else {}
    variant = 'expired' if change == 'expired-marked-eligible' else 'complete'
    text, value, case = fallback_case('en', variant, **options)
    if change == 'different-dossier':
        value['facts'][0]['args'][1] = 'Different dossier'
    if change == 'injected-conclusion':
        value['facts'][0]['predicate'] = 'client_identity_verified'
    if change == 'expired-marked-eligible':
        value['facts'][1]['predicate'] = 'document_eligible_for_fallback_verification'
    answer, models = ask(registry, text, value, case)
    assert answer['route'] == 'clarify', answer['text']
    assert 'result' not in answer and len(models.requests) == 1


def test_recorded_verification_date_is_distinct_from_assessment(registry):
    recorded = '2026-07-04'
    quote = f'{INSTITUTION} recorded that the identity of {REPRESENTATIVE} was verified on {recorded}.'
    target = 'representative_identity_verification_date_recorded'
    prefix = [INSTITUTION, DOSSIER, DATE, REPRESENTATIVE, COMPANY]
    text, value, case = kyc_case('en', 'KYC-RC-DL-02', target, prefix + ['?recorded'],
        f'Which date did {INSTITUTION} record for verification of {REPRESENTATIVE}?',
        [(target, prefix + [recorded], quote)], documents=[(recorded, 'date', quote)],
        company=True, representative=True)
    value.update(variables=[{'name': '?recorded', 'type': 'date'}], select=['?recorded'])
    answer, _ = ask(registry, text, value, case)
    assert answer['status'] == 'entailed' and answer['checker']['valid']
    assert answer['result']['results'][0]['answers'] == [{'?recorded': recorded}]
    assert all(step['kind'] == 'assertion' for step in answer['proof_steps'])
