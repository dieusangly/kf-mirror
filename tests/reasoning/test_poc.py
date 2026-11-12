"""POC presentation must never replace execution or relax proof/citation checks."""
import json

import pytest

from knowledge_foundry.reasoning.assistant import ReasoningAssistant
from knowledge_foundry.reasoning.catalogue import load_dataset, resolve_checker
from knowledge_foundry.reasoning.poc import POC_LABEL, load_poc, verified_examples
from knowledge_foundry.retrieval.artifacts import file_hash
from knowledge_foundry.retrieval.config import ROOT, load_config
from rag_fixtures import json_stream, excerpt_explanation, faithful_audit, plan_for
from test_assistant import PilotMemoryStore

POC = ROOT / 'artifacts/20261028-docling-aml-cft-01/reasoning-poc-01'


@pytest.fixture(autouse=True)
def no_paid(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('offline POC tests cannot call a provider')
    for target in ('knowledge_foundry.reasoning.assistant.openai_stream',
                   'knowledge_foundry.retrieval.answering.openai_stream',
                   'knowledge_foundry.retrieval.query.openai_stream',
                   'knowledge_foundry.embeddings.EmbeddingAdapter._request'):
        monkeypatch.setattr(target, forbidden)


def test_assertions_are_not_prompted_as_deductions_and_arbitrary_question_runs(tmp_path):
    data = load_dataset()
    row = data['mapping']['mappings'][0]
    seen = []
    def answer(payload, config):
        seen.append(payload['instructions'])
        assert 'no new deduction is established' in payload['instructions']
        assert 'Do not describe this as a deduction' in payload['instructions']
        assert 'never grouped inside one bracket' in payload['instructions']
        yield from excerpt_explanation(payload, config)
    def audit(payload, config):
        if payload['text']['format']['name'] == 'explanation_audit':
            assert 'an empty rules/proof list is expected' in payload['instructions']
        yield from faithful_audit(payload, config)
    store = PilotMemoryStore(data)
    question = 'An arbitrary wording outside any suggestion inventory: what is stated here?'
    assistant = ReasoningAssistant(store, load_config(), output_dir=tmp_path / 'poc-proofs',
        checker=resolve_checker(), interpretation_stream=json_stream(plan_for(row, goal=row['facts'][0])),
        answer_stream=answer, audit_stream=audit, experience_label=POC_LABEL)
    result = assistant.ask('poc', question, token='t'*32, session_id='s', snapshot_id='snapshot:pilot')
    assert result['status'] == 'reasoned' and seen
    assert result['knowledge_label'] == POC_LABEL
    assert result['retrieval']['reasoning']['status'] == 'asserted'
    assert not result['retrieval']['reasoning']['rules']
    assert result['citations'] and result['retrieval']['reasoning']['certificate']['status'] == 'accepted'


def presentation(tmp_path, monkeypatch):
    import knowledge_foundry.reasoning.poc as module
    monkeypatch.setattr('knowledge_foundry.reasoning.catalogue.load_suggestions', lambda *a: [])
    monkeypatch.setattr(module, 'current_bindings', lambda: {'runtime': 'current'})
    examples = {'cases': [{'id': 'poc-fact', 'question': 'Arbitrary source question?', 'source_ids': ['src:x'],
                           'document_ids': ['doc:x'], 'evaluation_oracle': {'must_not': 'escape'},
                           'answer': 'must not escape'}]}
    (tmp_path / 'poc-examples.json').write_text(json.dumps(examples))
    result = tmp_path / 'poc-result.json'; result.write_text('{}')
    report = {'code_config_bindings': {'runtime': 'current'}, 'snapshot_id': 'snapshot:poc',
              'dataset_sha256': 'dataset', 'release_identity': {'id': 'release'},
              'examples_sha256': file_hash(tmp_path / 'poc-examples.json'),
              'results': [{'id': 'poc-fact', 'verification_result': 'pass', 'ui_verified': True,
                           'evidence': 'poc-result.json', 'evidence_sha256': file_hash(result)}]}
    (tmp_path / 'poc-verification.json').write_text(json.dumps(report))
    return {'directory': tmp_path, 'dataset_path': tmp_path, 'examples': 'poc-examples.json',
            'verification': 'poc-verification.json'}, {'id': 'snapshot:poc', 'identity': {
            'reasoning_dataset_sha256': 'dataset', 'release_identity': {'id': 'release'}}}, report


def test_suggestions_contain_only_question_and_visible_filters(tmp_path, monkeypatch):
    poc, snapshot, _ = presentation(tmp_path, monkeypatch)
    assert verified_examples(poc, snapshot) == [{'id': 'poc-fact', 'question': 'Arbitrary source question?',
                                               'source_ids': ['src:x'], 'document_ids': ['doc:x']}]


@pytest.mark.parametrize('binding', ['runtime', 'snapshot', 'dataset', 'release', 'inventory', 'evidence', 'failed', 'unreviewed'])
def test_stale_or_failed_examples_are_hidden(tmp_path, monkeypatch, binding):
    poc, snapshot, report = presentation(tmp_path, monkeypatch)
    if binding == 'runtime': report['code_config_bindings'] = {'runtime': 'stale'}
    elif binding == 'snapshot': snapshot['id'] = 'snapshot:other'
    elif binding == 'dataset': snapshot['identity']['reasoning_dataset_sha256'] = 'other'
    elif binding == 'release': snapshot['identity']['release_identity'] = {'id': 'other'}
    elif binding == 'inventory': (tmp_path / 'poc-examples.json').write_text('{}')
    elif binding == 'evidence': (tmp_path / 'poc-result.json').write_text('{"tampered": true}')
    elif binding == 'failed': report['results'][0]['verification_result'] = 'fail'
    else: report['results'][0]['ui_verified'] = False
    (tmp_path / 'poc-verification.json').write_text(json.dumps(report))
    assert verified_examples(poc, snapshot) == []


def test_poc_reference_is_separate_and_cannot_grant_admission():
    if not (POC / 'poc-config.json').exists(): pytest.skip('local POC artifact required')
    poc = load_poc(POC / 'poc-config.json', 'reasoning-poc-01')
    assert poc['dataset_path'] == POC.with_name('reasoning-release-01')
    assert load_poc(POC / 'poc-config.json', 'other-corpus') is None
    assert 'mapping' not in poc and 'catalogue' not in poc


def test_poc_reference_rejects_changed_release_manifest(tmp_path):
    (tmp_path / 'manifest.json').write_text('{}')
    (tmp_path / 'poc-config.json').write_text(json.dumps({'format':'reasoning-poc-v1',
        'corpus':'reasoning-poc-01', 'label':POC_LABEL, 'dataset':'.', 'release_manifest_sha256':'0'*64}))
    with pytest.raises(ValueError, match='source release changed'):
        load_poc(tmp_path / 'poc-config.json', 'reasoning-poc-01')


def test_live_runner_requires_new_scope_and_cap_authorization(tmp_path, monkeypatch):
    from scripts.reasoning_poc import legacy_recorder
    monkeypatch.setattr('scripts.reasoning_poc.CHECKS', tmp_path)
    scope = {'code_config_bindings': {}, 'limits': {'max_provider_requests': 36}}
    # Creating a recorder does not authorize calls; it must fail before network on invalid bindings.
    recorder = legacy_recorder()(scope, {'approved_spending_cap_usd': .25})
    with pytest.raises(ValueError, match='runtime/configuration changed'):
        list(recorder.stream({}, {}))
