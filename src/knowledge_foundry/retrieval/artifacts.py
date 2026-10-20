"""Validate frozen artifacts and map them to disposable search projections."""
import copy
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from knowledge_foundry.claim_context import load_preparation
from knowledge_foundry.embeddings import validate_response
from knowledge_foundry.evidence import evidence_key
from knowledge_foundry.structural_chunks import assemble_structural_chunks, stable_hash
from knowledge_foundry.summaries import load_summaries
from scripts.validate_artifact_examples import check

PROJECTION_VERSION = 'artifact-search-v1'


def read_json(path):
    return json.loads(Path(path).read_text())


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def profile(config):
    return {k: config[k] for k in ('provider', 'model', 'preprocessing_version')} | {
        'dimensions': config['dimensions'] or config['expected_dimensions']}


def evidence_id(reference):
    return 'ev:' + hashlib.sha256(evidence_key(reference).encode()).hexdigest()


def validate_outputs(directory, manifest):
    directory = Path(directory).resolve()
    for row in manifest['outputs']:
        path = (directory / row['path']).resolve()
        if not path.is_relative_to(directory) or file_hash(path) != row['sha256']:
            raise ValueError('artifact output hash mismatch: ' + row['path'])


def load_artifacts(*, artifact_path=None, preparation_dir=None, claims_dir=None,
                   summaries_dir=None, build_id=None):
    if artifact_path is None and preparation_dir is None:
        raise ValueError('provide artifact_path or preparation_dir')
    prepared = load_preparation(preparation_dir, verify_requests=False) if preparation_dir else None
    path = Path(artifact_path) if artifact_path else Path(preparation_dir) / 'artifact-bundle.json'
    bundle = read_json(path)
    check(bundle)
    if prepared and stable_hash(bundle) != stable_hash(prepared['bundle']):
        raise ValueError('explicit artifact and preparation bundle differ')
    chunk_builds = {c['build_id'] for c in bundle['chunks']}
    if build_id is None:
        if len(chunk_builds) != 1:
            raise ValueError('select exactly one chunk build with --build-id')
        build_id = next(iter(chunk_builds))
    if build_id not in chunk_builds:
        raise ValueError('selected chunk build is absent')
    selected_build = next(b for b in bundle['builds'] if b['id'] == build_id)
    if selected_build['status'] != 'complete':
        raise ValueError('selected chunk build is incomplete')
    if prepared and chunk_builds != {build_id}:
        raise ValueError('preparation must match the selected chunk build')
    identity = {'bundle_sha256': file_hash(path), 'chunk_build_id': build_id}
    claims = []
    if claims_dir:
        directory = Path(claims_dir)
        manifest = read_json(directory / 'build-manifest.json')
        if manifest.get('stage') != 'topic-classification':
            raise ValueError('claims must be a completed classification artifact')
        validate_outputs(directory, manifest)
        if read_json(directory / 'report.json').get('status') != 'complete':
            raise ValueError('claim classification is incomplete')
        output_paths = [r['path'] for r in manifest['outputs']]
        if len(output_paths) != len(set(output_paths)) or not {
                'artifact-bundle.json', 'report.json', 'claims.jsonl', 'chunks.jsonl'} <= set(output_paths):
            raise ValueError('claim classification manifest is incomplete')
        classified = read_json(directory / 'artifact-bundle.json')
        check(classified)
        if any(stable_hash(bundle[k]) != stable_hash(classified[k])
               for k in ('sources', 'documents', 'elements', 'chunks')):
            raise ValueError('claim branch is incompatible with selected passage snapshot')
        claims = classified['claims']
        if claims != [json.loads(l) for l in (directory / 'claims.jsonl').read_text().splitlines()] or \
                classified['chunks'] != [json.loads(l) for l in (directory / 'chunks.jsonl').read_text().splitlines()]:
            raise ValueError('claim classification exports differ from the validated bundle')
        if any(c['build_id'] != manifest['compilation_build_id'] for c in claims):
            raise ValueError('claim build identity mismatch')
        identity['claims_manifest_sha256'] = file_hash(directory / 'build-manifest.json')
        identity['claim_manifest'] = manifest
    elif bundle['claims']:
        claims = bundle['claims']
    summaries = []
    if summaries_dir:
        loaded = load_summaries(summaries_dir)
        if prepared is None or loaded['manifest']['preparation_manifest_sha256'] != prepared['manifest_sha256']:
            raise ValueError('summary branch requires the identical selected preparation')
        summaries = loaded['summaries']
        identity['summary_manifest_sha256'] = file_hash(Path(summaries_dir) / 'build-manifest.json')
        identity['summary_manifest'] = loaded['manifest']
    selected = copy.deepcopy(bundle)
    selected['chunks'] = [c for c in bundle['chunks'] if c['build_id'] == build_id]
    chunk_ids = {c['id'] for c in selected['chunks']}
    claims = [c for c in claims if set(c['chunk_ids']) <= chunk_ids]
    structural, links = assemble_structural_chunks(selected) if prepared is None else (
        prepared['structural_chunks'], [json.loads(l) for l in
                                       (Path(preparation_dir) / 'structural-links.jsonl').read_text().splitlines()])
    embeddings = []
    if prepared:
        identity['preparation_manifest_sha256'] = prepared['manifest_sha256']
        identity['preparation_manifest'] = prepared['manifest']
        embeddings = [json.loads(l) for l in (Path(preparation_dir) / 'embeddings.jsonl').read_text().splitlines()]
    return {'bundle': selected, 'claims': claims, 'summaries': summaries, 'structural': structural,
            'links': links, 'hierarchy': prepared['hierarchy'] if prepared else [],
            'embeddings': embeddings, 'identity': identity}


def project(artifacts, embedding_config=None):
    """Keep original records and references; only the search text is a projection."""
    bundle = artifacts['bundle']
    elements = {e['id']: e for e in bundle['elements']}
    documents = {d['id']: d for d in bundle['documents']}
    sources = {(s['id'], s['version']): s for s in bundle['sources']}
    evidence, memberships = {}, defaultdict(set)
    for chunk in bundle['chunks']:
        for ref in chunk['evidence']:
            memberships[evidence_id(ref)].add(chunk['id'])

    def add_evidence(ref, origin='artifact'):
        key = evidence_id(ref)
        if key not in evidence:
            element = elements[ref['element_id']]
            doc = documents[ref['document_id']]
            source = sources[(doc['source_id'], doc['source_version'])]
            span = ref.get('span')
            text = element['text'][span['start']:span['end']] if span else element['text']
            evidence[key] = {'id': key, 'reference': ref, 'text': text,
                             'source': source, 'document': doc, 'location': element['location'],
                             'element_kind': element['kind'], 'origin': 'artifact' if memberships[key] else origin,
                             'original_chunk_ids': sorted(memberships[key]), 'context_ids': []}
        return key

    # A full-element projection permits exact preview of governing context while
    # keeping every original (including spanless) reference unchanged.
    by_element = {}
    for element in bundle['elements']:
        if not element['text'].strip():
            continue
        doc = documents[element['document_id']]
        ref = {'element_id': element['id'], 'element_revision': element['revision'],
               'document_id': doc['id'], 'source_id': doc['source_id'],
               'span': {'start': 0, 'end': len(element['text'])}}
        by_element[element['id']] = add_evidence(ref, 'full_element')
    parents = {h['element_id']: h['parent_element_id'] for h in artifacts['hierarchy']}
    # Only explicit, local governing/note edges. No semantic-cluster traversal,
    # same-section fan-out, or broad adjacency expansion.
    related = defaultdict(set)
    for row in artifacts['links']:
        ids = row['element_ids']
        if len(ids) != 2:
            continue
        a, b = ids
        if row['reason'] == 'same_page_unique_note_marker':
            if elements[a]['kind'] == 'footnote':
                a, b = b, a
            if elements[b]['kind'] == 'footnote' and elements[a]['document_id'] == elements[b]['document_id']:
                related[a].add(b)
        elif row['reason'] in {'ordered_list_introduction', 'unordered_list_introduction', 'governing_context_reference'}:
            if parents.get(b) == a:
                related[b].add(a)

    records = []
    vectors = {r['structural_chunk_id']: r for r in artifacts['embeddings']}
    wanted = profile(embedding_config) if embedding_config else None
    for kind, rows in (('passage', artifacts['structural']), ('claim', artifacts['claims']),
                       ('summary', artifacts['summaries'])):
        for row in rows:
            refs = ([m['reference'] for m in row['evidence_memberships']]
                    if kind == 'passage' else row['evidence'])
            ids = list(dict.fromkeys(add_evidence(r) for r in refs))
            if kind == 'passage':
                # Claims normalize spanless headings to a full-text span. Keep
                # both original and normalized identities for exact resolution.
                for ref in refs:
                    if 'span' not in ref and elements[ref['element_id']]['kind'] == 'heading':
                        full_id = by_element.get(ref['element_id'])
                        if full_id and full_id not in ids:
                            ids.append(full_id)
            saved = vectors.get(row['id'])
            vector = None
            if saved and wanted and profile(saved) == wanted:
                if saved['input_sha256'] != stable_hash(row['text']) or saved['vector_sha256'] != stable_hash(saved['vector']):
                    raise ValueError('saved vector text/hash mismatch')
                vector = validate_response({'data': [{'index': 0, 'embedding': saved['vector']}]}, 1, wanted['dimensions'])[0]
            records.append({'id': row['id'], 'kind': kind, 'text': row['text'],
                            'source_ids': sorted({r['source_id'] for r in refs}),
                            'document_ids': sorted({r['document_id'] for r in refs}),
                            'evidence_ids': ids, 'metadata': {'original': row}, 'vector': vector})
    for value in list(evidence.values()):
        eid = value['reference']['element_id']
        targets = related[eid] | ({parents[eid]} if parents.get(eid) else set())
        value['context_ids'] = sorted(by_element[t] for t in targets if t in by_element and
                                      elements[t]['document_id'] == value['document']['id'])
    return records, list(evidence.values())
