"""Receipt, selection and original-passage checks for version 1 reasoning packages."""
import hashlib
import json
from pathlib import Path

from knowledge_foundry.shared.paths import REPOSITORY_ROOT as ROOT


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def project_path(name):
    path = (ROOT / name).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('dataset path must remain inside the repository')
    return path


def rows(path):
    values = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    result = {(v['case_id'], v['language']): v for v in values}
    if len(result) != len(values):
        raise ValueError('duplicate dataset record')
    return result


def accepted_query_path(package):
    pins, artifacts = package['artifact_hashes'], package['accepted_artifacts']
    if artifacts:
        name = artifacts['queries']['path']
    else:
        names = [name for name in pins if Path(name).name == 'queries-v1.jsonl']
        if len(names) != 1:
            raise ValueError('acceptance must identify one query set')
        name = names[0]
    path = project_path(name)
    if digest(path) != pins[name]:
        raise ValueError('accepted query set hash differs: ' + name)
    return path


BO_DATASET = 'aml-cft-real-claims-bo'
KYC_DATASET = 'aml-cft-real-claims-kyc'
KYC_RECEIPT_ROLES = {
    'inputs-v1.jsonl': 'readable_inputs',
    'questions-v1.jsonl': 'readable_questions',
    'proposed-answers-v1.jsonl': 'proposed_answers',
    'proposed-proofs-v1.jsonl': 'proposed_proofs',
    'encoding': 'encodings',
    'query': 'queries',
    'result': 'results_and_certificates',
    'checker': 'proof_checks',
}
ARTIFACT_FIELDS = {
    'inputs_path': 'readable_inputs',
    'encodings_path': 'encodings',
    'manifest_path': 'dataset_manifest',
    'engine_config_path': 'configuration',
    'engine_path': 'engine',
    'schema_path': 'schema',
}
BO_SOURCE_TITLES = {
    'CMF-L561-2-2': 'Code monétaire et financier, article L. 561-2-2',
    'CMF-R561-1': 'Code monétaire et financier, article R. 561-1',
    'CC-L233-3': 'Code de commerce, article L. 233-3',
    'ACPR-KYC-2021': 'Lignes directrices de l’ACPR relatives à la connaissance de la clientèle',
}


def accepted_pins(entry, acceptance, status):
    legacy = {'decision', 'exact_sha256'}
    indexed = {'status', 'accepted_artifacts'}
    fields = acceptance.keys()
    if legacy <= fields and not indexed & fields:
        decision, pins, artifacts = acceptance['decision'], acceptance['exact_sha256'], {}
        status_receipt = status.get('acceptance', {})
        if status_receipt.get('exact_hashes') != pins:
            raise ValueError('acceptance/status pins differ')
    elif indexed <= fields and not legacy & fields:
        decision, artifacts = acceptance['status'], acceptance['accepted_artifacts']
        if not isinstance(artifacts, dict) or not artifacts:
            raise ValueError('invalid accepted artifacts')
        kyc = entry['dataset_id'] == KYC_DATASET
        if kyc:
            # The KYC receipt groups the pinned executables and configuration
            # separately from the replay outputs. Keep its original bytes pinned.
            setup = artifacts.get('setup')
            if not isinstance(setup, dict) or KYC_RECEIPT_ROLES.keys() - artifacts.keys():
                raise ValueError('invalid KYC acceptance artifact roles')
            artifacts = {KYC_RECEIPT_ROLES.get(role, role): pin
                         for role, pin in artifacts.items() if role != 'setup'}
            if len(artifacts) != len(acceptance['accepted_artifacts']) - 1 or artifacts.keys() & setup.keys():
                raise ValueError('duplicate KYC acceptance artifact roles')
            artifacts.update(setup)
        pins = {}
        for artifact in artifacts.values():
            if (not isinstance(artifact, dict) or not isinstance(artifact.get('path'), str)
                    or not isinstance(artifact.get('sha256'), str)):
                raise ValueError('invalid accepted artifact pin')
            name, sha256 = artifact['path'], artifact['sha256']
            if name in pins and pins[name] != sha256:
                raise ValueError('conflicting accepted artifact pins')
            pins[name] = sha256
        status_receipt = status.get('hashes', {}).get('independent_recheck_1', {}).get('acceptance', {})
        if acceptance.get('authoritative_status_path') != entry['status_path']:
            raise ValueError('acceptance/status path differs')
        manifest = artifacts.get('dataset_manifest', {})
        source_manifest = acceptance.get('source_dataset_manifest')
        if (not manifest or not isinstance(source_manifest, dict)
                or status.get('source_dataset_manifest') != source_manifest
                or (source_manifest.get('sha256') != manifest['sha256'] if kyc else source_manifest != manifest)
                or any(status.get(k) != acceptance.get(k) for k in ('semantic_cases', 'language_versions'))):
            raise ValueError('acceptance/status manifest or counts differ')
    else:
        raise ValueError('inconsistent acceptance format')
    if decision != 'accepted' or status['status'] != 'accepted':
        raise ValueError('reasoning dataset is not accepted')
    if (status_receipt.get('path'), status_receipt.get('sha256')) != (
            entry['acceptance_path'], entry['acceptance_sha256']):
        raise ValueError('status does not identify the accepted receipt')
    if (any(status[k] != acceptance[k] for k in (
            'batch_id', 'dataset_id', 'dataset_version', 'case_ids', 'languages', 'selected_families'))
            or entry['dataset_id'] != acceptance['dataset_id'] or entry['version'] != acceptance['dataset_version']):
        raise ValueError('acceptance identity/selection differs')
    return pins, artifacts


def source_title(source_id, source):
    return BO_SOURCE_TITLES.get(source_id) or source.get('title') or source_id


def resolve_passages(index, clauses, *, allow_synthetic_conflicts=False):
    resolved = {}
    for clause in clauses:
        if not clause.get('passage_ids'):
            if allow_synthetic_conflicts and clause.get('source_kind') == 'synthetic_conflict_policy':
                continue
            raise ValueError('source-backed clause lacks passages: ' + clause['id'])
        for passage_id in clause['passage_ids']:
            passage = index['passages'].get(passage_id)
            source = index['sources'].get(passage.get('source_id')) if passage else None
            if not source or not source.get('version') or not passage.get('locator'):
                raise ValueError('unresolved source passage: ' + passage_id)
            excerpts = [passage['text']] if 'text' in passage else [span.get('text') for span in passage.get('spans', [])]
            if not excerpts or any(not isinstance(text, str) or not text.strip() for text in excerpts):
                raise ValueError('unresolved source passage text: ' + passage_id)
            source_id = passage['source_id']
            resolved[passage_id] = {'id': passage_id, 'title': source_title(source_id, source),
                                    'version': source['version'], 'locator': passage['locator'],
                                    'excerpts': excerpts, 'official_url': source.get('official_url')}
    return resolved


def read_receipt(entry):
    """Verify the registry's two external pins before interpreting receipt roles."""
    for kind in ('acceptance', 'status'):
        if digest(project_path(entry[kind + '_path'])) != entry[kind + '_sha256']:
            raise ValueError(kind + ' hash differs')
    acceptance = read(project_path(entry['acceptance_path']))
    status = read(project_path(entry['status_path']))
    pins, artifacts = accepted_pins(entry, acceptance, status)
    if entry['engine_path'] != 'scripts/aml_cft_reasoning.py':
        raise ValueError('unsupported engine entry point')
    return acceptance, status, pins, artifacts


def read_package(entry):
    """Read only the accepted inputs required to execute a case."""
    acceptance, _, pins, artifacts = read_receipt(entry)
    source_index = None
    source_backed = entry['context_kind'] == 'source_backed'
    if (entry['dataset_id'] in {BO_DATASET, KYC_DATASET} and not source_backed) or (source_backed and not artifacts):
        raise ValueError('dataset context kind disagrees with the acceptance format')
    fields = dict(ARTIFACT_FIELDS)
    if source_backed:
        fields['source_index_path'] = 'source_index'
    paths = {}
    for field, artifact in fields.items():
        name = entry[field]
        path = project_path(name)
        if (artifacts and artifacts.get(artifact, {}).get('path') != name) or pins.get(name) != digest(path):
            raise ValueError('accepted prerequisite hash differs: ' + field)
        paths[field] = path
    manifest = read(paths['manifest_path'])
    if manifest['dataset_id'] != entry['dataset_id'] or manifest['dataset_version'] != entry['version']:
        raise ValueError('manifest identity differs')
    cases, languages = acceptance['case_ids'], acceptance['languages']
    families = acceptance['selected_families']
    if (any(family not in manifest['families'] for family in families)
            or cases != [cid for family in families for cid in manifest['families'][family]]
            or not cases or len(cases) != len(set(cases))
            or languages != manifest['languages'] or set(languages) != {'en', 'fr'}
            or acceptance['semantic_cases'] != len(cases)
            or acceptance['language_versions'] != len(cases) * len(languages)):
        raise ValueError('manifest/acceptance selection differs')
    if source_backed:
        index_entry = manifest['source_index']
        if (manifest['batch_id'] != acceptance['batch_id']
                or manifest['semantic_cases'] != acceptance['semantic_cases']
                or manifest['language_versions'] != acceptance['language_versions']
                or paths['manifest_path'].parent / index_entry['path'] != paths['source_index_path']
                or index_entry['sha256'] != pins[entry['source_index_path']]):
            raise ValueError('manifest source index or batch differs')
        source_index = read(paths['source_index_path'])
    config = read(paths['engine_config_path'])
    sources, encodings = rows(paths['inputs_path']), rows(paths['encodings_path'])
    selected = {(cid, lang) for cid in cases for lang in languages}
    if set(encodings) != selected or not selected <= set(sources) or (source_backed and set(sources) != selected):
        raise ValueError('accepted record selection differs')
    package = {**entry, 'engine_config': config, 'acceptance': acceptance,
               'artifact_hashes': pins, 'accepted_artifacts': artifacts}
    return package, sources, encodings, selected, manifest, source_index


def package_records(entry, sources, encodings, selected, manifest, source_index, check_encoding):
    """Check identity, scope and source links before exposing each execution input."""
    source_backed = entry['context_kind'] == 'source_backed'
    for identity in selected:
        source, encoding = sources[identity], encodings[identity]
        check_encoding(encoding)
        if any(encoding[k] != source[k] for k in ('dataset_id', 'dataset_version', 'case_id', 'language')):
            raise ValueError('source/encoding identity differs')
        if (source['dataset_id'] != entry['dataset_id'] or source['dataset_version'] != entry['version']
                or encoding['scope_id'] != source['scope']['id']
                or (source_backed and source['scope']['assessment_date'] != manifest['assessment_date'])):
            raise ValueError('record identity or scope differs')
        source_ids = {s['id'] for kind in ('policy_clauses', 'scenario_facts') for s in source[kind]}
        if any(not set(item['source_ids']) <= source_ids for kind in ('facts', 'rules', 'incompatibilities')
               for item in encoding[kind]):
            raise ValueError('unresolved reasoning provenance')
        kyc = entry['dataset_id'] == KYC_DATASET
        passages = resolve_passages(source_index, source['policy_clauses'],
                                    allow_synthetic_conflicts=kyc) if source_backed else {}
        yield identity, source, encoding, passages
