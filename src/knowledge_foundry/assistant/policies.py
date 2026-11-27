"""Project accepted reasoning packages into fresh-case Assistant policies."""
from copy import deepcopy
import hashlib
import json

from knowledge_foundry.provenance.reasoning_packages import (
    BO_DATASET, KYC_DATASET, package_records, read_package,
)
from .datasets import DatasetRegistry as V1Registry


KYC_COMPANY_CASES = {'KYC-RC-DL-02', 'KYC-RC-OH-02', 'KYC-RC-OH-03',
                     'KYC-RC-MH-01', 'KYC-RC-MP-02', 'KYC-RC-CF-02'}


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


class DatasetRegistry(V1Registry):
    """Serve the v1 search API with checked runtime and package boundaries."""

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
        from knowledge_foundry.reasoning_engine import runtime

        if entry['id'] in self.packages or entry['context_kind'] not in {'synthetic', 'source_backed'}:
            raise ValueError('duplicate package or invalid context kind')
        package, sources, encodings, selected, manifest, source_index = read_package(entry)
        self.engine = runtime
        self.engine.check_config(package['engine_config'])
        self.packages[entry['id']] = package
        for identity, source, encoding, passages in package_records(
                entry, sources, encodings, selected, manifest, source_index, runtime.check_encoding):
            key = '/'.join((entry['id'], *identity))
            kyc = entry['dataset_id'] == KYC_DATASET
            scope = source['scope']
            if entry['dataset_id'] == BO_DATASET:
                scope = {**scope, 'text': scope['text'].replace('Synthetic file as of', 'Assessment as of', 1)
                         .replace('Dossier fictif au', 'Évaluation au', 1)}
            elif kyc:
                scope = kyc_policy_scope(source)
            self.records[key] = {**source, 'candidate_id': key, 'package_id': entry['id'],
                                 'context_kind': entry['context_kind'], 'encoding': encoding,
                                 'scope': scope, 'passages': passages}
