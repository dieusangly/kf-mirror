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


STOP = set('the a an is are does do of to for and or in with that this it its be what which '
           'le la les un une des du de est sont et ou pour avec dans ce cette il elle quel quelle'.split())


def terms(text):
    plain = ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))
    return set(re.findall(r'[a-z0-9]+', plain)) - STOP


class DatasetRegistry:
    def __init__(self, path=None, *, search_config=None, local_only=False, embedder=None):
        self.path = project_path(path or 'config/reasoning-datasets.json')
        self.definition = read(self.path)
        if self.definition['version'] != 'reasoning-datasets-v1':
            raise ValueError('unsupported dataset registry version')
        self.records, self.packages = {}, {}
        self.engine = None
        for entry in self.definition['datasets']:
            self._load(entry)
        if not self.records:
            raise ValueError('no accepted reasoning datasets registered')
        self.searcher = ScenarioSearch(self.records, search_config or load_config()['scenario_search'],
                                       local_only=local_only, embedder=embedder)

    def _load(self, entry):
        acceptance_path = project_path(entry['acceptance_path'])
        if digest(acceptance_path) != entry['acceptance_sha256']:
            raise ValueError('acceptance hash differs')
        acceptance = read(acceptance_path)
        status_path = project_path(entry['status_path'])
        if digest(status_path) != entry['status_sha256']:
            raise ValueError('status hash differs')
        status = read(status_path)
        if acceptance['decision'] != 'accepted' or status['status'] != 'accepted':
            raise ValueError('reasoning dataset is not accepted')
        if (status['batch_id'] != acceptance['batch_id'] or status['case_ids'] != acceptance['case_ids']
                or entry['dataset_id'] != acceptance['dataset_id'] or entry['version'] != acceptance['dataset_version']):
            raise ValueError('acceptance identity/selection differs')
        if entry['id'] in self.packages or entry['context_kind'] not in {'synthetic', 'source_backed'}:
            raise ValueError('duplicate package or invalid context kind')
        paths = {}
        for field in ('inputs_path', 'encodings_path', 'manifest_path', 'engine_config_path', 'engine_path', 'schema_path'):
            name = entry[field]
            path = project_path(name)
            if acceptance['exact_sha256'].get(name) != digest(path):
                raise ValueError('accepted prerequisite hash differs: ' + field)
            paths[field] = path
        manifest = read(paths['manifest_path'])
        if manifest['dataset_id'] != entry['dataset_id'] or manifest['dataset_version'] != entry['version']:
            raise ValueError('manifest identity differs')
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
        selected = {(cid, lang) for cid in acceptance['case_ids'] for lang in acceptance['languages']}
        if set(encodings) != selected or not selected <= set(sources):
            raise ValueError('accepted record selection differs')
        self.packages[entry['id']] = {**entry, 'engine_config': config, 'acceptance': acceptance}
        for identity in selected:
            source, encoding = sources[identity], encodings[identity]
            self.engine.check_encoding(encoding)
            if any(encoding[k] != source[k] for k in ('dataset_id', 'dataset_version', 'case_id', 'language')):
                raise ValueError('source/encoding identity differs')
            source_ids = {s['id'] for kind in ('policy_clauses', 'scenario_facts') for s in source[kind]}
            if any(not set(item['source_ids']) <= source_ids for kind in ('facts', 'rules', 'incompatibilities')
                   for item in encoding[kind]):
                raise ValueError('unresolved reasoning provenance')
            key = '/'.join((entry['id'], *identity))
            self.records[key] = {**source, 'candidate_id': key, 'package_id': entry['id'],
                                 'context_kind': entry['context_kind'], 'encoding': encoding}

    def search(self, question, limit=6, preferred=None):
        return [self.candidate(key) for key in self.searcher.search(question, limit, preferred)]

    def candidate(self, key):
        row = self.records[key]
        # Only readable inputs and declared vocabulary; no category, saved query or reference hints.
        return deepcopy({k: row[k] for k in ('candidate_id', 'context_kind', 'language', 'scope',
                                            'policy_clauses', 'scenario_facts')} | {
            'vocabulary': {k: row['encoding'][k] for k in ('entities', 'predicates')}})

    def execute(self, key, query):
        row = self.records[key]
        config = self.packages[row['package_id']]['engine_config']
        result = self.engine.solve(row['encoding'], query, config)
        checked = self.engine.check_result(row['encoding'], query, config, result)
        return result, checked
