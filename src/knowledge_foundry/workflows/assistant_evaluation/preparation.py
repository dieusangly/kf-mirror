"""Reproduce Stage 1 v1 questions and engine-checked provisional references offline."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

from knowledge_foundry.assistant.policies import DatasetRegistry
from knowledge_foundry.provenance.reasoning_packages import digest
from knowledge_foundry.shared.paths import REPOSITORY_ROOT as ROOT
from .construction import AUTHORING, FILES, VERSION, PREPARATION_TOOLS, FAMILIES, CATEGORIES, read_rows, build, source_paths, save
from .validation import validate


def generate(output):
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
    manifest = {'preparation_tools': PREPARATION_TOOLS, 'schema_version': 'assistant-evaluation-1', 'dataset_id': 'aml-cft-reasoning-assistant-evaluation',
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
    report = validate(output)
    save(output / 'preparation-checks.json', report)
    save(output / 'run.json', {'dataset_version': VERSION,
        'status': 'ready-for-runner' if report['passed'] else 'preparation-failed',
        'code_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'working_tree': subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True),
        'source_hashes': pins, 'artifact_hashes': {p.name: digest(p) for p in sorted(output.iterdir())},
        'commands': [f'.venv/bin/python -m knowledge_foundry.workflows.assistant_evaluation.preparation --output {output}'],
        'external_calls': 0, 'real_model_executed': 0, 'reference_status': 'provisional',
        'independent_review': 'not-started', 'shared_repair_counter': 0,
        'next_action': 'Review preparation evidence, then agree providers, budget and published snapshot before live execution.'})
    if not report['passed']:
        raise ValueError('preparation failed: ' + '; '.join(report['failures']))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
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
