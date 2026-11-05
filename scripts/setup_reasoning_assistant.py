"""Install the pinned checker, or validate/import the reviewed subset without model calls.

Migrations and snapshot publication remain separate, explicit operations.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from knowledge_foundry.reasoning.catalogue import DEFAULT_CHECKER, DEFAULT_DATASET, accepted_projection, load_dataset
from knowledge_foundry.retrieval.artifacts import file_hash
from knowledge_foundry.storage.postgres import PostgresStore


def install_checker(source_dir, destination=DEFAULT_CHECKER):
    """Retain the checker built from the pinned clean source outside /tmp."""
    from knowledge_foundry.reasoning.engine import UPSTREAM_COMMIT
    source_dir, destination = Path(source_dir).resolve(), Path(destination)
    revision = subprocess.run(['git', '-C', str(source_dir), 'rev-parse', 'HEAD'],
                              check=True, capture_output=True, text=True).stdout.strip()
    changes = subprocess.run(['git', '-C', str(source_dir), 'status', '--porcelain',
                              '--untracked-files=all', '--', 'lean'],
                             check=True, capture_output=True, text=True).stdout.strip()
    if revision != UPSTREAM_COMMIT or changes:
        raise ValueError('checker requires the pinned unmodified Lean source')
    binary = source_dir / 'lean/.lake/build/bin/oo-horn'
    subprocess.run([str(binary), 'rules'], check=True, capture_output=True, timeout=30)
    manifest = {'upstream_commit': revision, 'binary_sha256': file_hash(binary),
                'lean_toolchain': (source_dir / 'lean/lean-toolchain').read_text().strip()}
    if destination.exists() and file_hash(destination) != manifest['binary_sha256']:
        raise ValueError('a different checker is already installed; preserve it before replacing')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(binary, destination)
    destination.with_name('manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return {'checker': str(destination), **manifest}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate', 'import', 'install-checker'])
    parser.add_argument('--source-dir', type=Path, help='clean pinned Open Ontologies checkout with built Lean checker')
    parser.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    parser.add_argument('--corpus', default='aml-cft-reviewed-assistant')
    args = parser.parse_args(argv)
    if args.command == 'install-checker':
        if not args.source_dir:
            parser.error('--source-dir is required for install-checker')
        result = install_checker(args.source_dir)
        print(json.dumps(result, indent=2))
        return result
    dataset = load_dataset(args.dataset)
    records, evidence, identity = accepted_projection(dataset)
    if args.command == 'import':
        result = PostgresStore().import_snapshot(args.corpus, identity, records, evidence, None, 'simple')
    else:
        result = {'dataset_sha256': dataset['digest'], 'records': len(records), 'evidence': len(evidence),
                  'supported_questions': [q['id'] for q in dataset['catalogue']['questions']]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    main()
