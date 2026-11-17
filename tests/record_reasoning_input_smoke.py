"""Record controlled orchestration evidence on unchanged frozen prompt text.

PYTHONPATH=src:tests .venv/bin/python tests/record_reasoning_input_smoke.py --output /tmp/input-smoke-NEW
This does not measure model quality. No reference answers or saved queries are used.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowledge_foundry.assistant.datasets import DatasetRegistry, digest
from scripts.evaluate_reasoning_assistant_inputs import execute_questions, identity, load_questions, save
from test_reasoning_assistant import decision, frozen, FixtureEmbeddings, make_assistant


def smoke_cases():
    cases = {}
    for language, document, assertion, mention in [
        ('en', 'passport', 'Customer A supplied a passport.', 'Customer A'),
        ('fr', 'passeport', 'Le client A a fourni un passeport.', 'Le client A')]:
        text = frozen(category='DL', language=language)
        cases[text] = decision(candidate_id=f'synthetic-v1/KYC-DL-01/{language}', language=language, text=text,
            predicate='identity_document', args=['A', '?document'],
            variables=[{'name': '?document', 'type': 'entity'}], select=['?document'],
            facts=[('identity_document', ['A', document], assertion)],
            entities=[('A', mention), (document, assertion)])
    cases[frozen()] = decision()
    for category, predicate, facts in [
        ('MP', 'onboarding_complete', [('identity_check_complete', ['A'], "A's identity check is complete.")]),
        ('CF', 'identity_verified', [('identity_verified', ['A'], "A's identity is verified."),
                                   ('identity_unverified', ['A'], "A's identity is explicitly unverified.")])]:
        text = frozen(category=category)
        cases[text] = decision(candidate_id=f'synthetic-v1/KYC-{category}-01/en', predicate=predicate,
                               text=text, facts=facts, entities=[('A', facts[0][2])])
    text = frozen('BO', 'MH')
    cases[text] = decision(candidate_id='synthetic-v1/BO-MH-01/en', text=text,
        predicate='controller_verification_required', args=['P', 'A'],
        facts=[('controls', ['P', 'H'], 'P controls H.'), ('controls', ['H', 'A'], 'H controls A.')],
        entities=[('P', 'P controls H.'), ('H', 'P controls H.'), ('A', 'H controls A.')])
    text = frozen(category='EX')
    cases[text] = decision(candidate_id='synthetic-v1/KYC-EX-01/en', text=text,
        predicate='identity_action', args=['A', '?action'],
        variables=[{'name': '?action', 'type': 'entity'}], select=['?action'],
        facts=[('document_pending', ['A'], "A's standard identity document is pending."),
               ('identity_exemption', ['A'], 'A has an approved alternative-evidence exemption.')],
        entities=[('A', "A's standard identity document is pending.")])
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    cases = smoke_cases()
    registry = DatasetRegistry()
    registry.searcher.embedder = FixtureEmbeddings(registry.policies)
    backend, models = make_assistant(registry, decision())

    class ControlledAssistant:
        def ask(self, text, history):
            models.value = cases[text]
            return backend.ask(text, history=history)

    questions = load_questions()
    subset = [q for q in questions if q['text'] in cases]
    pins = identity(backend.config, {'mocked_models': True, 'external_calls': 0})
    pins['mock_code'] = {str(path): digest(path) for path in
                         [Path(__file__), Path('tests/test_reasoning_assistant.py')]}
    summary = execute_questions(ControlledAssistant(), subset, args.output, pins,
                                max_prompts=len(subset), budget=1, reserve=0)
    summary.update(evidence_kind='mocked orchestration; not real-model quality',
                   full_denominator=288, real_model_executed=0, external_calls=0,
                   real_model_unexecuted=[q['question_id'] for q in questions])
    save(args.output / 'summary.json', summary)
    print(json.dumps({k: summary[k] for k in ('attempted', 'failed', 'proof_checks', 'real_model_executed')}))
    return int(summary['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(main())
