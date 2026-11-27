"""Reproduce Stage 1 v1 questions and engine-checked provisional references offline."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from knowledge_foundry.assistant.datasets import DatasetRegistry, accepted_query_path, digest

AUTHORING = ROOT / 'config/aml-cft-reasoning-assistant-evaluation-cases.jsonl'
DATASET = ROOT / 'artifacts/aml-cft-reasoning-assistant-evaluation/01-dataset-run-007'
VERSION = '1.0.0'
FILES = {'questions': 'questions-v1.jsonl', 'references': 'proposed-references-v1.jsonl',
         'encodings': 'reference-encodings-v1.jsonl', 'queries': 'reference-queries-v1.jsonl',
         'results': 'reference-results-v1.jsonl'}
FAMILIES = ('KYC', 'BO', 'PEP', 'RISK', 'MON', 'TFS', 'PAY', 'CORR')
CATEGORIES = ('DL', 'OH', 'MH', 'MP', 'EX', 'CF')


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def fact_signature(facts, query):
    """Conservative renaming screen, not a claim of semantic equivalence testing.

    Same predicate multiplicities and query shape are rejected even if role joins
    differ. This deliberately rejects some novel cases; independent review is still
    required for cases that pass this screen.
    """
    return (tuple(sorted(Counter(f['atom']['predicate'] for f in facts).items())),
            query['target']['predicate'], tuple(a.startswith('?') for a in query['target']['args']))


def reference_case(case, language, registry):
    key = f"synthetic-v1/{case['policy_case_id']}/{language}"
    # Deduplicated runtime candidates may use another equivalent policy ID.
    source = registry.records[key]
    policy_ids = {p['id'] for p in source['policy_clauses']}
    encoding = deepcopy(source['encoding'])
    constants = {}
    for item in encoding['rules'] + encoding['incompatibilities']:
        if not set(item['source_ids']) <= policy_ids:
            raise ValueError('policy depends on pilot customer facts')
        for atom in item.get('body', []) + ([item['head']] if 'head' in item else [item['left'], item['right']]):
            for arg in atom['args']:
                if not arg.startswith('?'):
                    constants[arg] = encoding['entities'][arg]
    encoding.update(dataset_id='aml-cft-reasoning-assistant-evaluation', dataset_version=VERSION,
                    case_id=case['case_id'], language=language, facts=[], entities=constants)
    for fact in case['facts']:
        atom = deepcopy(fact['atom'])
        for arg, typ in zip(atom['args'], encoding['predicates'][atom['predicate']]):
            encoding['entities'][arg] = typ
        encoding['facts'].append({'id': fact['id'], 'source_ids': [case['case_id'] + ':' + fact['id']], 'atom': atom})
    target = case['query']['target']
    for arg, typ in zip(target['args'], encoding['predicates'][target['predicate']]):
        if not arg.startswith('?'):
            encoding['entities'][arg] = typ
    query = {k: encoding[k] for k in (*registry.engine.IDENTITY, 'schema_version')}
    query['queries'] = [{'id': 'reference-query', 'question_id': case['case_id'] + '-' + language,
                         **deepcopy(case['query'])}]
    config = registry.packages['synthetic-v1']['engine_config']
    result = registry.engine.solve(encoding, query, config)
    check = registry.engine.check_result(encoding, query, config, result)
    if not check['valid'] or result['results'][0]['status'] != case['expected_status']:
        raise ValueError('authored status disagrees with checked engine outcome: ' + case['case_id'])
    return encoding, query, result, check


def source_paths(cases, registry):
    paths = {AUTHORING, Path(__file__), ROOT / 'scripts/validate_reasoning_assistant_evaluation.py',
             registry.path, ROOT / 'config/aml-cft-reasoning-vocabulary.json',
             ROOT / 'src/knowledge_foundry/assistant/datasets.py',
             ROOT / 'src/knowledge_foundry/assistant/config.py',
             ROOT / 'tests/fixtures/reasoning-assistant-runtime-v1.json',
             ROOT / 'tests/fixtures/reasoning-routing-v1.json',
             ROOT / 'artifacts/aml-cft-reasoning-pilot/01-dataset-run-001/questions-v1.jsonl'}
    paths.update(accepted_query_path(package) for package in registry.packages.values())
    for entry in registry.definition['datasets']:
        paths.update(ROOT / entry[k] for k in ('inputs_path', 'encodings_path', 'manifest_path',
                     'engine_path', 'engine_config_path', 'schema_path', 'acceptance_path', 'status_path'))
    for case in cases:
        paths.update(ROOT / p for p in case.get('source_paths', []))
        for evidence in case.get('document_evidence', []):
            paths.update((ROOT / evidence['original_path'], ROOT / evidence['extracted_path']))
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)}


def build(cases, registry):
    records = {k: [] for k in FILES}
    links = []
    for line, case in enumerate(cases, 1):
        links.append({'case_id': case['case_id'], 'base_case_id': case['base_case_id'],
                      'authoring_line': line, 'authoring_sha256': canonical_hash(case),
                      'change': case.get('change'), 'novelty_rationale': case.get('novelty_rationale')})
        for language in ('en', 'fr'):
            qid = case['case_id'] + '-' + language
            if case['question_type'] == 'routing':
                text = case['text'][language]
            else:
                text = '\n\n'.join([case['context'][language],
                    ' '.join(f['text'][language] for f in case['facts']), case['question'][language]])
            records['questions'].append({'question_id': qid, 'pair_id': case['case_id'],
                'question_type': case['question_type'], 'family_id': case['family_id'],
                'category': case['category'], 'coverage_category': case['coverage_category'],
                'base_case_id': case['base_case_id'], 'routing_kind': case.get('routing_kind'),
                'language': language, 'text': text})
            ref = {'question_id': qid, 'pair_id': case['case_id'], 'language': language,
                   'reference_status': 'provisional', 'independent_review': 'not-started',
                   'authoring_source': {'path': str(AUTHORING.relative_to(ROOT)), 'line': line,
                                        'sha256': canonical_hash(case)}}
            if case['question_type'] != 'routing':
                encoding, query, result, check = reference_case(case, language, registry)
                records['encodings'].append(encoding)
                records['queries'].append(query)
                records['results'].append({'question_id': qid, 'result': result, 'checker': check})
                ref.update(expected_route='reasoning', reference_check='engine_checked',
                    policy_case_id=case['policy_case_id'], expected=result['results'][0],
                    reference_encoding_sha256=result['encoding_sha256'],
                    reference_query_sha256=result['query_sha256'],
                    readable_facts=[{'id': f['id'], 'text': f['text'][language]} for f in case['facts']],
                    readable_question=case['question'][language],
                    policy_source={'path': 'artifacts/aml-cft-reasoning-pilot/01-dataset-run-001/inputs-v1.jsonl',
                                   'case_id': case['policy_case_id'], 'language': language},
                    response_requirements=['Answer in the question language under the fictional policy.',
                        'Return every supported binding and no unsupported binding.',
                        'Explain the applied rules, or missing/conflicting premises; preserve unknown vs negative.',
                        'Use current user facts as provenance, not pilot customer facts or reference IDs.'])
            else:
                ref.update(expected_route=case['expected_route'], reference_check='source_linked',
                           response_requirements=case['response_requirements'], routing_kind=case['routing_kind'])
                if case['routing_kind'] == 'documents':
                    ref.update(proposed_answer=case['proposed_answer'][language],
                               document_evidence=case['document_evidence'])
                else:
                    ref['source_paths'] = case['source_paths']
            records['references'].append(ref)
    return records, links


def generate(output=DATASET):
    output = Path(output)
    if output.exists():
        raise ValueError('output must be a fresh directory')
    registry = DatasetRegistry()
    cases = read_rows(AUTHORING)
    records, links = build(cases, registry)
    pins = source_paths(cases, registry)
    output.mkdir(parents=True)
    for role, filename in FILES.items():
        (output / filename).write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n'
                                              for r in records[role]))
    manifest = {'schema_version': 'assistant-evaluation-1', 'dataset_id': 'aml-cft-reasoning-assistant-evaluation',
        'dataset_version': VERSION, 'reference_status': 'provisional', 'roles': FILES,
        'coverage': {'prompts': len(records['questions']), 'bilingual_pairs': len(cases),
                     'per_type_pairs': dict(Counter(c['question_type'] for c in cases)),
                     'new_case_matrix': {'families': list(FAMILIES), 'categories': list(CATEGORIES)},
                     'routing_pairs': dict(Counter(c['routing_kind'] for c in cases if c['question_type'] == 'routing'))},
        'files': {name: {'sha256': digest(output / name), 'records': len(records[role])} for role, name in FILES.items()},
        'source_hashes': pins, 'cases': links,
        'document_snapshot': {'published_snapshot_id': None, 'verified': False},
        'semantics': {'source': 'accepted pilot policy scope and engine configuration',
                      'copied_into_question_text': False},
        'limitations': ['Known-policy generalization; not unseen-policy learning or current legal advice.',
            'Translations and base/variant cases are correlated; 288 prompts are not 288 independent situations.',
            'Engine checks do not establish natural-language encoding fidelity or independent acceptance.',
            'Document questions reuse source topics; no source-topic novelty is claimed.',
            'Development fixtures are separate; broad tuning after evaluation requires a fresh held-out set.']}
    save(output / 'dataset-manifest-v1.json', manifest)
    # Local import keeps the validator independently invocable.
    from scripts.validate_reasoning_assistant_evaluation import validate
    report = validate(output)
    save(output / 'preparation-checks.json', report)
    save(output / 'run.json', {'dataset_version': VERSION,
        'status': 'ready-for-runner' if report['passed'] else 'preparation-failed',
        'code_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'working_tree': subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True),
        'source_hashes': pins, 'artifact_hashes': {p.name: digest(p) for p in sorted(output.iterdir())},
        'commands': [f'.venv/bin/python scripts/generate_reasoning_assistant_evaluation.py --output {output}'],
        'external_calls': 0, 'real_model_executed': 0, 'reference_status': 'provisional',
        'independent_review': 'not-started', 'shared_repair_counter': 0,
        'next_action': 'Review preparation evidence, then agree providers, budget and published snapshot before live execution.'})
    if not report['passed']:
        raise ValueError('preparation failed: ' + '; '.join(report['failures']))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DATASET)
    args = parser.parse_args()
    try:
        report = generate(args.output)
        print(json.dumps({k: report[k] for k in ('passed', 'prompts', 'engine_checked_references')}))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
