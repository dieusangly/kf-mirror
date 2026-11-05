"""Validated, bounded source-description catalogue; no natural-language goal generation."""
import json
import os
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from knowledge_foundry.retrieval.artifacts import evidence_id, file_hash
from knowledge_foundry.structural_chunks import stable_hash

DEFAULT_DATASET = Path(__file__).resolve().parents[3] / 'examples/reasoning-assistant/accepted'
DEFAULT_CHECKER = DEFAULT_DATASET.parents[2] / 'artifacts/reasoning-checker/oo-horn'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_dataset(directory=DEFAULT_DATASET):
    """Accept only separately reviewed exports, with review-to-content bindings.

    This does not relax the normal document-pipeline importer or promote drafts.
    Review decisions are assertions by identified reviewers, not proof of truth.
    """
    directory = Path(directory).resolve()
    manifest = json.loads((directory / 'manifest.json').read_text())
    require(manifest['status'] == 'accepted-bounded-source-descriptions', 'dataset is not accepted')
    names = {'bundle.json', 'mapping.json', 'catalogue.json', 'provenance.json',
             'grounding.json', 'formalization.json'}
    require(set(manifest['files']) == names, 'accepted manifest is incomplete')
    for name, digest in manifest['files'].items():
        require(file_hash(directory / name) == digest, 'accepted export hash mismatch: ' + name)
    data = {name.removesuffix('.json'): json.loads((directory / name).read_text()) for name in names}
    from knowledge_foundry.reasoning.adapter import compile_projection
    bundle, mapping, provenance = data['bundle'], data['mapping'], data['provenance']
    original = {c['id']: c for c in provenance['original_claims']}
    grounded = {j['claim_id']: j for j in data['grounding']['judgments']}
    semantic = {j['mapping_id']: j for j in data['formalization']['judgments']}
    require(data['grounding']['reviewer']['name'] != data['formalization']['reviewer']['name'],
            'grounding and formalization reviews must be separate')
    require(data['formalization']['proposal_sha256'] == provenance['proposal_sha256'], 'formalization proposal binding mismatch')
    require(data['formalization']['catalogue_sha256'] == provenance['catalogue_sha256'], 'catalogue review binding mismatch')
    require(data['grounding']['frozen_input_sha256'] == provenance['frozen_input_sha256'], 'grounding input binding mismatch')
    require(set(original) == {c['id'] for c in bundle['claims']} == {m['claim_id'] for m in mapping['mappings']},
            'accepted claim population mismatch')
    for claim in bundle['claims']:
        cid = claim['id']
        require(grounded[cid]['grounding'] == 'pass' and grounded[cid]['claim_sha256'] == stable_hash(original[cid]),
                'claim has no bound grounding pass')
        expected = {**original[cid], 'grounding_review_status': 'verified'}
        require(claim == expected, 'accepted claim differs from reviewed original')
    proposal_rows = {r['id']: r for r in provenance['proposal_mappings']}
    elements = {e['id']: e for e in bundle['elements']}
    docs = {d['id']: d for d in bundle['documents']}
    for row in mapping['mappings']:
        judgment = semantic[row['id']]
        require(judgment['claim_id'] == row['claim_id'] and all(
            judgment['dimensions'][k] == 'pass' for k in ('formalization', 'scope', 'vocabulary', 'answer')),
            'mapping lacks complete independent review')
        require(judgment['mapping_sha256'] == stable_hash(proposal_rows[row['id']]), 'mapping review binding mismatch')
        require(judgment['claim_sha256'] == stable_hash(original[row['claim_id']]), 'semantic claim review binding mismatch')
        expected = {**proposal_rows[row['id']], 'claim_sha256': stable_hash(next(
            c for c in bundle['claims'] if c['id'] == row['claim_id']))}
        require(row == expected, 'accepted semantics differ from reviewed proposal')
        for citation in judgment['evidence']:
            ref = citation['reference']
            element, doc = elements[ref['element_id']], docs[ref['document_id']]
            require(ref in original[row['claim_id']]['evidence'] and element['location'] == citation['location'] and
                    element['kind'] == citation['element_kind'] and
                    element['text'][ref['span']['start']:ref['span']['end']] == citation['exact_text'] and
                    doc['source_version'] == citation['source_version'] and
                    doc['content_sha256'] == citation['original_sha256'], 'accepted evidence differs from independent review')
    entries = data['catalogue']['questions']
    reviewed_questions = {j['question_id']: j for j in data['formalization']['judgments']}
    require(len({q['id'] for q in entries}) == len(entries) and
            len({q['question'] for q in entries}) == len(entries), 'duplicate catalogue entry')
    require({q['id'] for q in entries} == set(reviewed_questions), 'accepted question population mismatch')
    for entry in entries:
        require(entry['enabled'] is True and stable_hash(entry) == reviewed_questions[entry['id']]['question_sha256'],
                'catalogue entry differs from independent review')
        require(entry['language'] in {'en', 'fr'} and bool(entry['answer']), 'invalid catalogue explanation')
        selected = [r for r in mapping['mappings'] if r['id'] in entry['mapping_ids']]
        require({r['id'] for r in selected} == set(entry['mapping_ids']), 'unknown catalogue mapping')
        require(any(entry['goal'] in r['facts'] or entry['goal'] in [rule['head'] for rule in r['rules']]
                    for r in selected), 'catalogue goal is outside reviewed mappings')
        compile_projection(bundle, mapping, jurisdiction=entry['jurisdiction'], as_of=entry['as_of'])
    return {**data, 'directory': directory, 'digest': stable_hash(manifest)}


def accepted_projection(dataset):
    """Import only reviewed claim spans, never all chunks in their lineage bundle."""
    bundle = dataset['bundle']
    elements = {e['id']: e for e in bundle['elements']}
    documents = {d['id']: d for d in bundle['documents']}
    sources = {(s['id'], s['version']): s for s in bundle['sources']}
    evidence, records = {}, []
    for claim in bundle['claims']:
        ids = []
        for ref in claim['evidence']:
            key = evidence_id(ref)
            ids.append(key)
            element, doc = elements[ref['element_id']], documents[ref['document_id']]
            span = ref['span']
            memberships = [c['id'] for c in bundle['chunks'] if ref in c['evidence']]
            evidence[key] = {'id': key, 'reference': ref, 'text': element['text'][span['start']:span['end']],
                'source': sources[(doc['source_id'], doc['source_version'])], 'document': doc,
                'location': element['location'], 'element_kind': element['kind'], 'origin': 'accepted-claim',
                'original_chunk_ids': memberships, 'context_ids': []}
        records.append({'id': 'accepted:' + claim['id'], 'kind': 'passage', 'text': claim['text'],
                        'source_ids': sorted({r['source_id'] for r in claim['evidence']}),
                        'document_ids': sorted({r['document_id'] for r in claim['evidence']}),
                        'evidence_ids': ids, 'metadata': {'claim': claim, 'dataset_sha256': dataset['digest']},
                        'vector': None})
    # Keep qualifications and headings attached to every span of a claim.
    for record in records:
        for key in record['evidence_ids']:
            evidence[key]['context_ids'] = [k for k in record['evidence_ids'] if k != key]
    used_docs = {did for r in records for did in r['document_ids']}
    docs = [d for d in bundle['documents'] if d['id'] in used_docs]
    editions = {(d['source_id'], d['source_version']) for d in docs}
    identity = {'projection_version': 'reviewed-reasoning-subset-v1', 'chunk_build_id': 'accepted:' + dataset['digest'],
                'reasoning_dataset_sha256': dataset['digest'], 'review_manifest': dataset['provenance'],
                'accepted_export_manifest': json.loads((dataset['directory'] / 'manifest.json').read_text()),
                'grounding_review': dataset['grounding'], 'formalization_review': dataset['formalization'],
                'mapping': dataset['mapping'], 'builds': bundle['builds'], 'documents': docs,
                'sources': [s for s in bundle['sources'] if (s['id'], s['version']) in editions]}
    return records, list(evidence.values()), identity


@lru_cache(maxsize=8)
def _cached_engine(directory, digest, snapshot_id, jurisdiction, as_of, output_dir, checker, checker_digest):
    """One closure per exact dataset/snapshot/scope/checker; explain rechecks certificates."""
    from knowledge_foundry.reasoning.engine import ReasoningEngine
    dataset = load_dataset(directory)
    require(dataset['digest'] == digest, 'dataset changed during setup')
    engine = ReasoningEngine(dataset['bundle'], dataset['mapping'], jurisdiction=jurisdiction, as_of=as_of)
    engine.run(Path(output_dir) / uuid4().hex, checker=checker)
    return engine


def resolve_checker(checker=None):
    """Explicit path/environment wins; the project installation survives restarts."""
    checker = checker or os.environ.get('OO_HORN')
    if checker is None:
        if DEFAULT_CHECKER.is_file():
            manifest = json.loads(DEFAULT_CHECKER.with_name('manifest.json').read_text())
            from knowledge_foundry.reasoning.engine import UPSTREAM_COMMIT
            require(manifest['upstream_commit'] == UPSTREAM_COMMIT and
                    manifest['binary_sha256'] == file_hash(DEFAULT_CHECKER), 'installed checker binding mismatch')
            checker = DEFAULT_CHECKER
        else:
            from open_ontologies_lite import find_checker
            try:
                checker = find_checker()
            except RuntimeError:
                checker = '/missing/oo-horn'
    return str(Path(checker).resolve())


def get_engine(dataset, snapshot_id, question, *, output_dir, checker=None):
    # Checker replacement invalidates cached state, even if its pathname is reused.
    checker = resolve_checker(checker)
    digest = file_hash(checker) if Path(checker).is_file() else 'missing'
    return _cached_engine(str(dataset['directory']), dataset['digest'], snapshot_id,
                          question['jurisdiction'], question['as_of'], str(Path(output_dir).resolve()), checker, digest)
