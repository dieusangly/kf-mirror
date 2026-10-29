"""Validate prior classification outcomes and persist per-candidate progress."""

from pathlib import Path

from jsonschema import Draft202012Validator

from knowledge_foundry.claim_generation_and_classification import (
    CLASSIFICATION_SCHEMA, _classification_input, _read_json, _write_json_atomic,
    canonical, digest, execution_settings, file_digest,
)


def context_maps(bundle):
    return ({c['id']: c for c in bundle['chunks']},
            {e['id']: e for e in bundle['elements']},
            {d['id']: d for d in bundle['documents']},
            {(s['id'], s['version']): s for s in bundle['sources']})


def load_reuse(classified_dir, candidates_dir, candidates, bundle, config, taxonomy_path, prompt_hash,
               classifier=None):
    """Reuse matching requests, including explicit nulls; never reuse errors."""
    from scripts.deterministic_claim_review import _load

    classified_dir, candidates_dir = Path(classified_dir), Path(candidates_dir)
    saved = _load(candidates_dir, classified_dir, taxonomy_path)
    manifest = saved['classification_manifest']
    expected_config = digest(canonical({
        'config': execution_settings(config, 'topic_classification'),
        'candidate_manifest_sha256': file_digest(candidates_dir / 'build-manifest.json'),
        'candidate_sha256': file_digest(candidates_dir / 'claim-candidates.jsonl'),
        'taxonomy_sha256': file_digest(taxonomy_path),
        'classification_prompt_sha256': prompt_hash}))
    if manifest['config_sha256'] != expected_config or manifest['classification_prompt_sha256'] != prompt_hash:
        raise ValueError('classification reuse requires identical settings, taxonomy and prompt')
    if saved['bundle']['builds'][-1]['config_sha256'] != expected_config:
        raise ValueError('classification reuse build settings mismatch')
    if classifier is not None and any(claim['classification'] != classifier for claim in saved['bundle']['claims']):
        raise ValueError('classification reuse model/version/prompt metadata mismatch')
    old = {c['id']: c for c in saved['candidates']}
    old_maps, new_maps = context_maps(saved['bundle']), context_maps(bundle)
    outcomes = {}
    for candidate in candidates:
        prior = old.get(candidate['id'])
        if prior != candidate or _classification_input(prior, *old_maps) != _classification_input(candidate, *new_maps):
            continue
        outcome = saved['outcomes'][candidate['id']]
        if outcome['status'] != 'classification_error':
            outcomes[candidate['id']] = {'topic_id': outcome['topic_id']}
    provenance = {'classified_directory': str(classified_dir.resolve()),
                  'candidates_directory': str(candidates_dir.resolve()),
                  'classification_manifest_sha256': file_digest(classified_dir / 'build-manifest.json'),
                  'candidate_manifest_sha256': file_digest(candidates_dir / 'build-manifest.json')}
    return outcomes, provenance


class ClassificationJournal:
    """One atomic, integrity-checked result per candidate, tied to exact requests."""

    def __init__(self, directory, identity, requests, valid_topics, resume):
        self.directory = Path(directory)
        self.path = self.directory / 'classification-checkpoint.json'
        self.records_dir = self.directory / 'classification-checkpoints'
        self.requests, self.valid_topics = requests, valid_topics
        self.records = {}
        if resume:
            if (self.directory / 'build-manifest.json').exists():
                raise ValueError('classification output is already finalized; use a fresh directory')
            if _read_json(self.path) != identity:
                raise ValueError('classification checkpoint input, settings or reuse source changed')
            for path in sorted(self.records_dir.glob('*.json')):
                record = _read_json(path)
                checksum = record.pop('sha256', None)
                if checksum != digest(canonical(record)):
                    raise ValueError('classification checkpoint integrity mismatch')
                self.validate(record)
                cid = record['candidate_id']
                if cid in self.records or path.name != digest(cid) + '.json':
                    raise ValueError('classification checkpoint candidate mismatch')
                self.records[cid] = record
        else:
            if self.directory.exists():
                raise ValueError(f'output directory already exists: {self.directory}; use --resume for an interrupted run')
            self.records_dir.mkdir(parents=True)
            _write_json_atomic(self.path, identity)

    def validate(self, record):
        cid = record['candidate_id']
        if cid not in self.requests or record['request_sha256'] != self.requests[cid]:
            raise ValueError('classification checkpoint request mismatch')
        if type(record['reused']) is not bool or not isinstance(record['usage_records'], list):
            raise ValueError('invalid classification checkpoint metadata')
        for usage in record['usage_records']:
            if set(usage) != {'input_tokens', 'cached_input_tokens', 'cache_write_tokens', 'output_tokens'} or any(
                    type(value) is not int or value < 0 for value in usage.values()):
                raise ValueError('invalid classification checkpoint usage')
        if record['error'] is None:
            Draft202012Validator(CLASSIFICATION_SCHEMA).validate(record['classification'])
            topic = record['classification']['topic_id']
            if topic is not None and topic not in self.valid_topics:
                raise ValueError('classification checkpoint topic is not curated')
        elif (not isinstance(record['error'], str) or not record['error'] or
              record['classification'] is not None or record['reused']):
            raise ValueError('invalid classification checkpoint failure')

    def save(self, record):
        self.validate(record)
        path = self.records_dir / (digest(record['candidate_id']) + '.json')
        _write_json_atomic(path, {**record, 'sha256': digest(canonical(record))})
        self.records[record['candidate_id']] = record

    def clean(self):
        self.path.unlink(missing_ok=True)
        for path in self.records_dir.glob('*.json'):
            path.unlink()
        self.records_dir.rmdir()
