"""Offline baseline replay; no embedding/LLM calls or acceptance decisions.

Exit 0: all baseline checks pass; 1: recorded failures; 2: input/I/O error.
Outputs must be fresh. Model routing/interpretation/wording accuracy is not measured.
"""
import argparse
import json
from pathlib import Path
import sys
import time

from knowledge_foundry.assistant.policies import DatasetRegistry
from knowledge_foundry.provenance.reasoning_packages import accepted_query_path, digest, project_path, read, rows
from knowledge_foundry.shared.paths import REPOSITORY_ROOT as ROOT


def evaluate(registry_path, cases_path):
    started = time.monotonic()
    registry = DatasetRegistry(registry_path)
    records = []
    packages = {}
    for package_id, package in registry.packages.items():
        pins, artifacts = package['artifact_hashes'], package['accepted_artifacts']
        if artifacts:
            result_name = artifacts['results_and_certificates']['path']
        else:
            result_names = [name for name in pins if Path(name).name == 'results-v1.json']
            if len(result_names) != 1:
                raise ValueError('acceptance must identify one baseline result set')
            result_name = result_names[0]
        if digest(project_path(result_name)) != pins[result_name]:
            raise ValueError('baseline result hash differs')
        queries = rows(accepted_query_path(package))
        baseline = read(project_path(result_name))
        expected = {(r['case_id'], r['language']): r for r in baseline['records']}
        selected = {key: row for key, row in registry.records.items() if row['package_id'] == package_id}
        identities = {(row['case_id'], row['language']) for row in selected.values()}
        if (baseline['batch_id'] != package['acceptance']['batch_id']
                or len(expected) != len(baseline['records']) or set(expected) != identities or set(queries) != identities):
            raise ValueError('accepted replay selection differs: ' + package_id)
        passed_count = 0
        for key, row in sorted(selected.items()):
            identity = row['case_id'], row['language']
            case_started = time.monotonic()
            try:
                result, checked = registry.execute(key, queries[identity])
                passed = checked['valid'] and result == expected[identity]
                error = None if passed else 'fresh result differs from the accepted baseline'
            except (ValueError, KeyError, TypeError) as exc:
                passed, error = False, str(exc)
            records.append({'candidate_id': key, 'passed': passed, 'error': error,
                            'latency_seconds': round(time.monotonic() - case_started, 6)})
            passed_count += passed
        packages[package_id] = {'language_versions': len(selected), 'passed': passed_count}
    passed = all(row['passed'] for row in records)
    return {'version': 'pilot-symbolic-replay-v1', 'passed': passed,
            'dataset_registry_sha256': digest(project_path(registry_path)), 'fixtures_sha256': digest(Path(cases_path)),
            'baseline': {'language_versions': len(records), 'passed': sum(r['passed'] for r in records),
                         'packages': packages, 'records': records},
            'candidate_recall_at_6': None,
            'routing_accuracy': None, 'interpretation_accuracy': None, 'wording_fidelity': None,
            'limitations': ['No live embeddings or models called; vector candidate recall is not measured.',
                           'Development examples are handwritten and not an independent evaluation.',
                           'Fresh symbolic replay does not establish interpretation or prose fidelity.',
                           'No Stage 4 acceptance/status records were modified.'],
            'external_calls': 0, 'latency_seconds': round(time.monotonic() - started, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', default='config/aml-cft-reasoning-datasets.json')
    parser.add_argument('--cases', default=str(ROOT / 'tests/fixtures/reasoning-routing-v1.json'))
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        if Path(args.output).exists():
            raise ValueError('output must be fresh')
        report = evaluate(args.registry, args.cases)
        with Path(args.output).open('x') as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({k: report[k] for k in ('passed', 'external_calls', 'latency_seconds')}))
        return 0 if report['passed'] else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
