"""POC presentation and verification bindings; never a question router."""
import json
import os
from pathlib import Path

from knowledge_foundry.reasoning.adapter import require
from knowledge_foundry.retrieval.artifacts import file_hash
from knowledge_foundry.retrieval.config import ROOT

POC_LABEL = 'Experimental reasoning POC'


def current_bindings():
    """Bind verification to runtime code, checker and effective config files."""
    paths = set((ROOT / 'src/knowledge_foundry').rglob('*.py'))
    paths.update((ROOT / 'src/knowledge_foundry/prompts').rglob('*.txt'))
    paths.update([ROOT / 'apps/chat/app.py', ROOT / 'scripts/setup_reasoning_assistant.py',
                  ROOT / 'scripts/reasoning_poc.py',
                  ROOT / 'artifacts/20261028-docling-aml-cft-01/reasoning-release-01/checks/final-live-runtime.py',
                  Path(os.environ.get('KF_REASONING_CONFIG', ROOT / 'config/reasoning-assistant.json')),
                  Path(os.environ.get('KF_RETRIEVAL_CONFIG', ROOT / 'config/retrieval.json'))])
    from knowledge_foundry.reasoning.catalogue import resolve_checker
    paths.add(Path(resolve_checker()))
    return {str(p.resolve()): file_hash(p) for p in sorted(paths)}


def load_poc(path, corpus):
    """A POC reference never grants admission; asks still load the real release."""
    if not path:
        return None
    path = Path(path).resolve()
    value = json.loads(path.read_text())
    require(value['format'] == 'reasoning-poc-v1' and value['corpus'] == 'reasoning-poc-01',
            'invalid POC presentation configuration')
    if value['corpus'] != corpus:
        return None
    require(value['label'] == POC_LABEL, 'invalid POC experience label')
    dataset = (path.parent / value['dataset']).resolve()
    require(file_hash(dataset / 'manifest.json') == value['release_manifest_sha256'],
            'POC source release changed')
    return {**value, 'directory': path.parent, 'dataset_path': dataset}


def verified_examples(poc, snapshot):
    """Hide stale/unverified suggestions. Never return answers or query plans."""
    from knowledge_foundry.reasoning.catalogue import load_suggestions
    load_suggestions(poc['dataset_path'])  # Display review gate; full admission stays in ask.
    path = poc['directory'] / poc['verification']
    if not path.exists():
        return []
    report = json.loads(path.read_text())
    identity = snapshot['identity']
    if (report['code_config_bindings'] != current_bindings() or
            report['snapshot_id'] != snapshot['id'] or
            report['dataset_sha256'] != identity.get('reasoning_dataset_sha256') or
            report['release_identity'] != identity.get('release_identity') or
            report['examples_sha256'] != file_hash(poc['directory'] / poc['examples'])):
        return []
    inventory = json.loads((poc['directory'] / poc['examples']).read_text())['cases']
    outcomes = {r['id']: r for r in report['results']}
    suggestions = []
    for case in inventory:
        row = outcomes.get(case['id'], {})
        if row.get('verification_result') != 'pass' or row.get('ui_verified') is not True:
            continue
        evidence = poc['directory'] / row['evidence']
        require(evidence.resolve().is_relative_to(poc['directory']), 'POC result escapes directory')
        if not evidence.is_file() or file_hash(evidence) != row['evidence_sha256']:
            continue
        suggestions.append({k: case[k] for k in ('id', 'question', 'source_ids', 'document_ids')})
    return suggestions
