"""Read accepted dataset packages without loading reference answers or saved results."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import re
import unicodedata

from .config import ROOT, load_config
from .search import ScenarioSearch


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


BO_DATASET = 'aml-cft-real-claims-bo'
KYC_DATASET = 'aml-cft-real-claims-kyc'
KYC_COMPANY_CASES = {'KYC-RC-DL-02', 'KYC-RC-OH-02', 'KYC-RC-OH-03',
                     'KYC-RC-MH-01', 'KYC-RC-MP-02', 'KYC-RC-CF-02'}
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


def kyc_policy_scope(source):
    scope = source['scope']
    company = source['case_id'] in KYC_COMPANY_CASES
    date = scope['assessment_date']
    if source['language'] == 'fr':
        client = 'une société française' if company else 'une personne physique'
        text = (f'Évaluation au {date} de l’entrée en relation pour un compte bancaire ordinaire. '
                f'Le client est {client}. L’établissement est un établissement de crédit français '
                'assujetti à l’article L. 561-2. Aucun régime spécial ni dispense de vigilance simplifiée.')
    else:
        client = 'a French company' if company else 'an individual'
        text = (f'Assessment on {date} of onboarding for an ordinary bank account. The client is {client}. '
                'The institution is a French credit institution subject to Article L. 561-2. '
                'No special regime or simplified due-diligence exemption applies.')
    return {**scope, 'text': text, 'client_kind': 'company' if company else 'individual'}


def kyc_policy_items(encoding, assessment_date):
    """Keep the accepted joins and replace dossier-specific constants with typed variables."""
    rules, conflicts = deepcopy((encoding['rules'], encoding['incompatibilities']))
    dynamic = {name: f'?entity_{index}' for index, name in enumerate(sorted(encoding['entities']), 1)
               if name != assessment_date}
    for item in rules + conflicts:
        atoms = item['body'] + [item['head']] if 'body' in item else [item['left'], item['right']]
        for atom in atoms:
            for arg in atom['args']:
                if arg in dynamic:
                    item['variables'][dynamic[arg]] = encoding['entities'][arg]
            atom['args'] = [dynamic.get(arg, arg) for arg in atom['args']]
    return rules, conflicts


STOP = set('the a an is are does do of to for and or in with that this it its be what which '
           'le la les un une des du de est sont et ou pour avec dans ce cette il elle quel quelle'.split())


def terms(text):
    plain = ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))
    return set(re.findall(r'[a-z0-9]+', plain)) - STOP


class DatasetRegistry:
    def __init__(self, path=None, *, search_config=None, local_only=False, embedder=None):
        self.path = project_path(path or 'config/aml-cft-reasoning-datasets.json')
        self.definition = read(self.path)
        if self.definition['version'] != 'reasoning-datasets-v1':
            raise ValueError('unsupported dataset registry version')
        self.records, self.packages, self.policies = {}, {}, {}
        self.vocabulary = read(ROOT / 'config/aml-cft-reasoning-vocabulary.json')['predicates']
        self.engine = None
        for entry in self.definition['datasets']:
            self._load(entry)
        if not self.records:
            raise ValueError('no accepted reasoning datasets registered')
        self._policy_contexts()
        self.searcher = ScenarioSearch(self.policies, search_config or load_config()['scenario_search'],
                                       local_only=local_only, embedder=embedder)

    def _policy_contexts(self):
        """Project accepted policies; customer assertions/identities never cross this boundary."""
        signatures = set()
        for key, row in sorted(self.records.items()):
            encoding = row['encoding']
            bo_policy = row['dataset_id'] == BO_DATASET
            kyc_policy = row['dataset_id'] == KYC_DATASET
            rules, conflicts = encoding['rules'], encoding['incompatibilities']
            if bo_policy and row['case_id'] == 'BO-RC-EX-02':
                # Only the company is a case binding. The date pins applicability
                # to the accepted scope, and all six fallback premises remain required.
                rules = deepcopy(rules)
                for rule in rules:
                    rule['variables']['?company'] = 'company'
                    for atom in rule['body'] + [rule['head']]:
                        atom['args'] = ['?company' if arg == 'client_company' else arg for arg in atom['args']]
            if kyc_policy:
                rules, conflicts = kyc_policy_items(encoding, row['scope']['assessment_date'])
            policy_ids = {s['id'] for s in row['policy_clauses']}
            items = rules + conflicts
            if any(not set(item['source_ids']) <= policy_ids for item in items):
                raise ValueError('policy depends on customer provenance')
            constants = {}
            for item in items:
                atoms = item['body'] + [item['head']] if 'body' in item else [item['left'], item['right']]
                for atom in atoms:
                    for arg in atom['args']:
                        if not arg.startswith('?'):
                            constants[arg] = encoding['entities'][arg]
            if (bo_policy or kyc_policy) and any('date' in types for types in encoding['predicates'].values()):
                constants[row['scope']['assessment_date']] = 'date'
            meanings = {}
            for name, types in encoding['predicates'].items():
                definition = self.vocabulary.get(name)
                if not definition or [arg['type'] for arg in definition['arguments']] != types:
                    raise ValueError('vocabulary signature differs: ' + name)
                meanings[name] = definition
            # The capital/conflict and fallback/suspicion variants cover the
            # corresponding narrower rules and vocabularies in fresh cases.
            if bo_policy and row['case_id'] in {'BO-RC-OH-01', 'BO-RC-EX-01'}:
                continue
            # The conflict policy contains the same in-person verification rule
            # and can also withhold it when validity is disputed.
            if kyc_policy and row['case_id'] == 'KYC-RC-OH-01':
                continue
            vocabulary = {'predicates': meanings, 'policy_constants': constants}
            if kyc_policy:
                supplied = {fact['atom']['predicate'] for fact in encoding['facts']}
                vocabulary['derived_only_predicates'] = sorted({rule['head']['predicate'] for rule in rules} - supplied)
            policy_structure = [{k: v for k, v in item.items() if k not in {'id', 'source_ids'}}
                                for item in items]
            signature = json.dumps({
                'package': row['package_id'], 'language': row['language'], 'scope': row['scope']['text'],
                'predicates': encoding['predicates'],
                'policy': policy_structure
            }, sort_keys=True)
            if signature in signatures:
                continue
            signatures.add(signature)
            self.policies[key] = deepcopy({k: row[k] for k in (
                'candidate_id', 'package_id', 'context_kind', 'language', 'dataset_id', 'dataset_version',
                'scope', 'policy_clauses', 'passages')} | {
                    'policy_identity': hashlib.sha256(json.dumps({
                        'package': row['package_id'], 'scope_id': row['scope']['id'],
                        'predicates': encoding['predicates'],
                        'policy': policy_structure}, sort_keys=True).encode()).hexdigest(),
                    'vocabulary': vocabulary,
                    'template': {'schema_version': encoding['schema_version'], 'scope_id': encoding['scope_id'],
                                 'predicates': encoding['predicates'], 'entities': constants,
                                 'rules': rules, 'incompatibilities': conflicts}})

    def _load(self, entry):
        acceptance_path = project_path(entry['acceptance_path'])
        if digest(acceptance_path) != entry['acceptance_sha256']:
            raise ValueError('acceptance hash differs')
        acceptance = read(acceptance_path)
        status_path = project_path(entry['status_path'])
        if digest(status_path) != entry['status_sha256']:
            raise ValueError('status hash differs')
        status = read(status_path)
        pins, artifacts = accepted_pins(entry, acceptance, status)
        if entry['id'] in self.packages or entry['context_kind'] not in {'synthetic', 'source_backed'}:
            raise ValueError('duplicate package or invalid context kind')
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
        # One schema/engine contract; later packages may add data, not swap executable code.
        if entry['engine_path'] != 'scripts/aml_cft_reasoning.py':
            raise ValueError('unsupported engine entry point')
        if self.engine is None:
            spec = importlib.util.spec_from_file_location('assistant_reasoning_engine', paths['engine_path'])
            self.engine = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.engine)
        config = read(paths['engine_config_path'])
        self.engine.check_config(config)
        sources, encodings = rows(paths['inputs_path']), rows(paths['encodings_path'])
        selected = {(cid, lang) for cid in cases for lang in languages}
        if set(encodings) != selected or not selected <= set(sources) or (source_backed and set(sources) != selected):
            raise ValueError('accepted record selection differs')
        self.packages[entry['id']] = {**entry, 'engine_config': config, 'acceptance': acceptance,
                                      'artifact_hashes': pins, 'accepted_artifacts': artifacts}
        for identity in selected:
            source, encoding = sources[identity], encodings[identity]
            self.engine.check_encoding(encoding)
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
            key = '/'.join((entry['id'], *identity))
            kyc = entry['dataset_id'] == KYC_DATASET
            passages = resolve_passages(source_index, source['policy_clauses'],
                                        allow_synthetic_conflicts=kyc) if source_backed else {}
            scope = source['scope']
            if entry['dataset_id'] == BO_DATASET:
                scope = {**scope, 'text': scope['text'].replace('Synthetic file as of', 'Assessment as of', 1)
                         .replace('Dossier fictif au', 'Évaluation au', 1)}
            elif kyc:
                scope = kyc_policy_scope(source)
            self.records[key] = {**source, 'candidate_id': key, 'package_id': entry['id'],
                                 'context_kind': entry['context_kind'], 'encoding': encoding,
                                 'scope': scope, 'passages': passages}

    def search(self, question, limit=6, preferred=None):
        return [self.candidate(key) for key in self.searcher.search(question, limit, preferred)]

    def lookup_candidates(self):
        """Expose rule-free accepted vocabularies without saved customer facts.

        Vector similarity can miss a relation with no accompanying policy prose,
        especially across languages. One context per equivalent vocabulary is
        enough: symbols/argument meanings are shared between language versions.
        """
        seen, candidates = set(), []
        for key, policy in sorted(self.policies.items()):
            template = policy['template']
            if template['rules'] or template['incompatibilities']:
                continue
            signature = policy['policy_identity']
            if signature not in seen:
                seen.add(signature)
                candidates.append(self.candidate(key))
        return candidates

    def candidate(self, key):
        row = self.policies[key]
        return deepcopy({k: row[k] for k in ('candidate_id', 'context_kind', 'language', 'scope',
                                            'policy_clauses', 'vocabulary')})

    def execute_case(self, key, encoding, query):
        config = self.packages[self.policies[key]['package_id']]['engine_config']
        result = self.engine.solve(encoding, query, config)
        checked = self.engine.check_result(encoding, query, config, result)
        if not checked['valid']:
            raise ValueError('runtime proof check failed')
        return result, checked

    def execute(self, key, query):
        """Explicit frozen-baseline replay only; Assistant uses execute_case instead."""
        row = self.records[key]
        config = self.packages[row['package_id']]['engine_config']
        result = self.engine.solve(row['encoding'], query, config)
        checked = self.engine.check_result(row['encoding'], query, config, result)
        return result, checked
