"""Install/check the accepted assistant or run isolated, offline draft experiments.

These commands make no model calls. Migrations and publication are separate.
"""
import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from knowledge_foundry.reasoning.adapter import _compile_projection, content_digest, require
from knowledge_foundry.reasoning.assistant import AUDIT_FIELDS, FIDELITY_FIELDS, ReasoningAssistant
from knowledge_foundry.reasoning.catalogue import (
    DEFAULT_CHECKER, DEFAULT_DATASET, accepted_projection, load_dataset, resolve_checker,
)
from knowledge_foundry.reasoning.engine import ReasoningEngine
from knowledge_foundry.provenance import ProvenanceArchive
from knowledge_foundry.retrieval.artifacts import file_hash
from knowledge_foundry.retrieval.config import load_config
from knowledge_foundry.storage.postgres import PostgresStore
from knowledge_foundry.structural_chunks import stable_hash


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


LABEL = 'Experimental — unreviewed formalization'


def draft_findings(directory):
    """Bind required findings to this frozen draft, from loose files or archive."""
    review_dir = directory.with_name(directory.name + '-review')
    draft_hash = file_hash(directory / 'manifest.json')
    if review_dir.exists():
        review = json.loads((review_dir / 'review-manifest.json').read_bytes())
        findings = (review_dir / 'findings.jsonl').read_bytes()
        location = str(review_dir / 'findings.jsonl')
    else:
        package = directory.with_name('formalization-v2')
        original = f'artifacts/{directory.parent.name}/{directory.name}'
        with ProvenanceArchive(package) as archive:
            archive.read(original + '/manifest.json', expected_sha256=draft_hash)
            review = json.loads(archive.read(original + '-review/review-manifest.json'))
            findings = archive.read(original + '-review/findings.jsonl',
                                    expected_sha256=review['file_sha256']['findings.jsonl'])
            location = archive.location(original + '-review/findings.jsonl')
    require(review['frozen_manifest_sha256'] == draft_hash, 'review frozen draft binding mismatch')
    digest = hashlib.sha256(findings).hexdigest()
    require(digest == review['file_sha256']['findings.jsonl'], 'review findings hash mismatch')
    return location, digest


def load_draft_batch(directory, batch, *, mapping_ids=None):
    """Read one frozen batch, retaining every original status/hash and its findings.

    The full frozen lineage stays bound to its original bundle hash. A selection
    limits execution/interpretation only; it is never a new reviewed artifact.
    Invalid input is rejected, never normalized or repaired here.
    """
    directory = Path(directory).resolve()
    require(Path(batch).name == batch and batch.endswith('.json'), 'expected a batch filename')
    manifest = json.loads((directory / 'manifest.json').read_text())
    require(manifest['status'] == 'generation_complete_draft_only', 'not a frozen draft export')
    hashes = manifest['output_file_sha256']
    names = ['input-bundle.json', 'vocabulary.json', 'batches/' + batch]
    for name in names:
        require(file_hash(directory / name) == hashes[name], 'frozen draft file binding mismatch: ' + name)
    bundle = json.loads((directory / names[0]).read_text())
    full_mapping = json.loads((directory / names[2]).read_text())
    require(full_mapping['review']['status'] == 'draft', 'only unreviewed draft batches are supported')
    require(full_mapping['bundle_sha256'] == stable_hash(bundle) == manifest['bundle_stable_hash'], 'draft bundle binding mismatch')
    require(full_mapping['review']['content_sha256'] == content_digest(full_mapping), 'draft content binding mismatch')
    selected = full_mapping['mappings']
    if mapping_ids is not None:
        require(bool(mapping_ids) and len(mapping_ids) == len(set(mapping_ids)), 'invalid draft mapping selection')
        selected = [r for r in selected if r['id'] in mapping_ids]
        require({r['id'] for r in selected} == set(mapping_ids), 'draft mapping not in selected batch')
    # Validate selected rows structurally against unchanged original claims. The
    # original full batch/review digest is retained separately, not falsely rebound.
    mapping = {**full_mapping, 'mappings': selected}
    vocabulary = json.loads((directory / 'vocabulary.json').read_text())['terms']
    from knowledge_foundry.reasoning.retrieval import mapping_atoms
    used = {v for r in selected for a in mapping_atoms(r) for v in a}
    findings_path, findings_hash = draft_findings(directory)
    # Keep review findings as data, with their file identity; this harness makes
    # no claim that later review has accepted a selected formalization.
    return {'bundle': bundle, 'mapping': mapping, 'full_mapping': full_mapping,
            'vocabulary': [v for v in vocabulary if v['iri'] in used],
            'trust': 'experimental_unreviewed', 'directory': directory,
            'digest': stable_hash({n: hashes[n] for n in names}),
            'experiment': {'label': LABEL, 'files': {n: hashes[n] for n in names},
                           'manifest_sha256': file_hash(directory / 'manifest.json'),
                           'mapping_ids': [r['id'] for r in selected],
                           'original_review': full_mapping['review'],
                           'claim_statuses': sorted({c['grounding_review_status'] for c in bundle['claims']}),
                           'findings_file': str(findings_path),
                           'findings_sha256': findings_hash,
                           'coverage_limit': 'One explicitly selected frozen batch/subset; no source-fidelity acceptance.'}}


class ExperimentalReasoningEngine(ReasoningEngine):
    def __init__(self, dataset, *, jurisdiction, as_of):
        require(dataset['trust'] == 'experimental_unreviewed', 'explicit experimental dataset required')
        original = dataset['full_mapping']
        require(original['review']['status'] == 'draft', 'draft status must remain unchanged')
        require({k: v for k, v in dataset['mapping'].items() if k != 'mappings'} ==
                {k: v for k, v in original.items() if k != 'mappings'}, 'changed draft metadata')
        require(original['review']['content_sha256'] == content_digest(original), 'stale draft content')
        require(all(row in original['mappings'] for row in dataset['mapping']['mappings']), 'changed draft selection')
        projection = _compile_projection(dataset['bundle'], dataset['mapping'], jurisdiction=jurisdiction,
                                         as_of=as_of, experimental=True)
        projection['version'] = 'experimental-unreviewed-v1'
        self._initialize_projection(projection)

    def explain(self, goal):
        return {**super().explain(goal), 'knowledge_label': LABEL, 'assumption_status': 'unreviewed'}


class DraftMemoryStore:
    """No production persistence: exact frozen claim spans in a private harness snapshot."""
    def __init__(self, dataset):
        bundle = dataset['bundle']
        elements = {e['id']: e for e in bundle['elements']}
        docs = {d['id']: d for d in bundle['documents']}
        sources = {(s['id'], s['version']): s for s in bundle['sources']}
        from knowledge_foundry.retrieval.artifacts import evidence_id
        self.rows, self.saved = {}, []
        selected = {r['claim_id'] for r in dataset['mapping']['mappings']}
        for claim in bundle['claims']:
            if claim['id'] not in selected:
                continue
            ids = [evidence_id(ref) for ref in claim['evidence']]
            for ref in claim['evidence']:
                key = evidence_id(ref)
                doc, e = docs[ref['document_id']], elements[ref['element_id']]
                row = {'id': key, 'reference': ref, 'text': e['text'][ref['span']['start']:ref['span']['end']],
                       'source': sources[(doc['source_id'], doc['source_version'])], 'document': doc,
                       'location': e['location'], 'element_kind': e['kind'], 'origin': 'experimental-draft',
                       'original_chunk_ids': claim['chunk_ids'], 'context_ids': [k for k in ids if k != key]}
                if key in self.rows:
                    row['context_ids'] = sorted(set(row['context_ids']) | set(self.rows[key]['context_ids']))
                self.rows[key] = row
        self.identity = {'knowledge_status': 'experimental_unreviewed', 'reasoning_dataset_sha256': dataset['digest']}
        self.sid = 'experimental:' + dataset['digest']

    def snapshot(self, corpus, snapshot_id=None):
        require(corpus == 'experimental' and snapshot_id in {None, self.sid}, 'experimental snapshot only')
        return {'id': self.sid, 'identity': self.identity}

    def evidence(self, snapshot_id, ids):
        require(snapshot_id == self.sid, 'experimental snapshot only')
        return {k: copy.deepcopy(self.rows[k]) for k in ids if k in self.rows}

    def history(self, session_id, token, snapshot_id, assistant_mode):
        require(snapshot_id == self.sid and assistant_mode == 'reasoning', 'experimental history only')
        return []

    def save_turn(self, session_id, token, snapshot_id, question, answer, assistant_mode):
        self.history(session_id, token, snapshot_id, assistant_mode)
        require(answer['knowledge_status'] == 'experimental_unreviewed', 'unlabelled experimental answer')
        self.saved.append(copy.deepcopy(answer))
        return 'experimental-message:' + str(len(self.saved))

    def rank_evidence(self, snapshot_id, query, rows):
        return rows

    def list_siblings(self, *args):
        return {}


class ExperimentalReasoningAssistant(ReasoningAssistant):
    """Only constructed explicitly by tests/harness, never from production config."""
    experimental = True

    def __init__(self, dataset, config, **options):
        require(dataset['trust'] == 'experimental_unreviewed', 'explicit draft dataset required')
        self.draft = dataset
        super().__init__(DraftMemoryStore(dataset), config, **options)

    def _dataset(self):
        return self.draft

    def _engine(self, dataset, snapshot_id, plan, limits):
        engine = ExperimentalReasoningEngine(dataset, jurisdiction=plan['jurisdiction'], as_of=plan['as_of'])
        engine.run(Path(self.output_dir) / ('experimental-' + uuid4().hex), checker=resolve_checker(self.checker),
                   timeout=limits['checker_timeout_seconds'], max_iterations=limits['max_iterations'])
        return engine

    def ask(self, *args, **kwargs):
        answer = super().ask(*args, **kwargs)
        answer['retrieval']['experiment'] = self.draft['experiment']
        return answer


def mock_response(value):
    def stream(payload, config):
        yield {'type': 'delta', 'text': json.dumps(value, ensure_ascii=False)}
        yield {'type': 'usage', 'usage': {'input_tokens': 0, 'output_tokens': 0,
                                         'cached_input_tokens': 0, 'cache_write_tokens': 0}}
    return stream


def run_case(directory, case, output, checker=None):
    dataset = load_draft_batch(directory, case['batch'], mapping_ids=case['mapping_ids'])
    def explanation(payload, config):
        data = json.loads(payload['input'])
        label = data['evidence'][0]['label']
        yield {'type': 'delta', 'text': case['explanation'].replace('[SOURCE]', '[' + label + ']')}
        yield {'type': 'usage', 'usage': {'input_tokens': 0, 'output_tokens': 0,
                                         'cached_input_tokens': 0, 'cache_write_tokens': 0}}
    def audit(payload, config):
        fields = FIDELITY_FIELDS if payload['text']['format']['name'] == 'explanation_audit' else AUDIT_FIELDS
        yield from mock_response(dict.fromkeys(fields, True))(payload, config)
    assistant = ExperimentalReasoningAssistant(dataset, load_config(), output_dir=output, checker=checker,
        interpretation_stream=mock_response(case['plan']), answer_stream=explanation, audit_stream=audit)
    result = assistant.ask('experimental', case['question'], token='experimental-local', session_id='experimental',
                           snapshot_id=assistant.store.sid)
    return {'case_id': case['id'], 'question': case['question'], 'expected_status': case['expected_status'],
            'matched_expectation': result['status'] == case['expected_status'], 'answer': result}


def run_experiment(args, parser):
    args.output.mkdir(parents=True, exist_ok=False)
    fixture = json.loads(args.cases.read_text())
    cases = fixture['cases']
    if args.case_id:
        cases = [case for case in cases if case['id'] in args.case_id]
        if {case['id'] for case in cases} != set(args.case_id):
            parser.error('unknown case id')
    results = []
    for case in cases:
        try:
            result = run_case(args.draft_dir, case, args.output / uuid4().hex, args.checker)
        except Exception as exc:
            result = {'case_id': case['id'], 'question': case['question'], 'matched_expectation': False,
                      'batch': case['batch'], 'mapping_ids': case['mapping_ids'],
                      'input_failure': type(exc).__name__, 'input_failure_detail': str(exc)[:2000], 'status': 'rejected_input', 'label': LABEL}
        results.append(result)
        print(case['id'] + ': ' + ('expected outcome' if result['matched_expectation'] else 'failure'), flush=True)
    report = {'label': LABEL, 'model_quality': fixture['model_quality'], 'paid_api_calls': 0,
              'cases_sha256': file_hash(args.cases), 'results': results}
    (args.output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return 0 if all(r['matched_expectation'] for r in results) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate', 'import', 'install-checker', 'experiment'])
    parser.add_argument('--source-dir', type=Path, help='clean pinned Open Ontologies checkout with built Lean checker')
    parser.add_argument('--dataset', type=Path, default=DEFAULT_DATASET)
    parser.add_argument('--corpus', default='aml-cft-reviewed-assistant')
    parser.add_argument('--draft-dir', type=Path, help='frozen draft directory, for experiment only')
    parser.add_argument('--cases', type=Path, default=ROOT / 'tests/fixtures/reasoning-draft-questions.json')
    parser.add_argument('--output', type=Path, help='new experiment directory; prior evidence is retained')
    parser.add_argument('--checker', help='experiment checker override')
    parser.add_argument('--case-id', action='append', help='run only these experiment fixture cases')
    args = parser.parse_args(argv)
    if args.command == 'experiment':
        if not args.draft_dir or not args.output:
            parser.error('--draft-dir and --output are required for experiment')
        return run_experiment(args, parser)
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
                  'suggested_question_ids': [q['id'] for q in dataset['catalogue']['questions']],
                  'supported_operations': ['check', 'select', 'connected_conjunction']}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    result = main()
    if isinstance(result, int):
        raise SystemExit(result)
