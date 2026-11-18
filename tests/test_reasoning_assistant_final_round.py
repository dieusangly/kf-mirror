"""General regression coverage for lookup access, edition selection and diagnostics."""
import json
from urllib.error import HTTPError

from knowledge_foundry.assistant.datasets import DatasetRegistry
from knowledge_foundry.assistant.diagnostics import exception_diagnostic
from knowledge_foundry.retrieval.service import explicit_source_ids


def test_lookup_catalog_excludes_customer_assertions_and_deduplicates_languages():
    registry = DatasetRegistry()
    catalog = registry.lookup_candidates()
    signatures = set()
    for candidate in catalog:
        policy = registry.policies[candidate['candidate_id']]
        assert not policy['template']['rules']
        assert not policy['template']['incompatibilities']
        assert 'scenario_facts' not in candidate
        assert 'facts' not in candidate['vocabulary']
        assert 'entities' not in candidate['vocabulary']
        assert policy['policy_identity'] not in signatures
        signatures.add(policy['policy_identity'])
    # Every accepted rule-free vocabulary is available, in either language.
    expected = {p['policy_identity'] for p in registry.policies.values()
                if not p['template']['rules'] and not p['template']['incompatibilities']}
    assert signatures == expected


def test_explicit_edition_filters_metadata_without_choosing_by_answer_language():
    snapshot = {'identity': {'sources': [
        {'id': 'one', 'title': 'Regulation 2099/987 (EN)', 'version': 'saved-en'},
        {'id': 'two', 'title': 'Regulation 2099/987 (FR)', 'version': 'saved-fr'},
        {'id': 'other', 'title': 'Regulation 2099/988 (EN)', 'version': 'saved-en'}]}}
    assert explicit_source_ids('Expliquez le texte anglais du règlement 2099/987.', snapshot) == ['one']
    assert explicit_source_ids('Explain Regulation 2099/987.', snapshot) == ['one', 'two']
    assert explicit_source_ids('Compare English and French 2099/987.', snapshot) is None
    assert explicit_source_ids('Compare 2099/987 and 2099/988.', snapshot) is None
    assert explicit_source_ids('English 2099/999.', snapshot) is None
    assert explicit_source_ids('Expliquez cette obligation.', snapshot) is None


def test_exception_diagnostics_preserve_cause_and_status_without_secrets():
    try:
        try:
            raise HTTPError('https://secret.invalid/?key=PRIVATE', 429, 'PRIVATE', {}, None)
        except HTTPError as cause:
            raise ValueError('postgres://PRIVATE') from cause
    except ValueError as exc:
        value = exception_diagnostic(exc, 'document_answer')
    assert [e['type'] for e in value['exception_chain']] == ['ValueError', 'HTTPError']
    assert value['exception_chain'][1]['http_status'] == 429
    assert 'PRIVATE' not in json.dumps(value)
    assert 'secret.invalid' not in json.dumps(value)
    assert all(set(f) == {'file', 'function', 'line'} for e in value['exception_chain'] for f in e['frames'])
