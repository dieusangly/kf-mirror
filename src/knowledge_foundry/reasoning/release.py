"""Versioned content releases, external review gates and exact evidence inventories.

Content inspection is deliberately separate from runtime admission. Inspection
never promotes original draft statuses or asserts that a missing review passed.
"""
import hashlib
import json
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from jsonschema import Draft202012Validator, FormatChecker

from knowledge_foundry.reasoning.adapter import ROOT, require
from knowledge_foundry.retrieval.artifacts import evidence_id, file_hash as _file_hash
from knowledge_foundry.structural_chunks import stable_hash
from scripts.validate_artifact_examples import check_claim_evidence

FORMAT = 'reviewed-reasoning-release-v1'
EVALUATION_LABEL = 'Runtime review pending — isolated evaluation'
_dependencies = ContextVar('release_validation_dependencies', default=None)
_ADMITTED = object()


def _record(path, digest):
    observed = _dependencies.get()
    if observed is not None:
        path = Path(path).absolute()
        binding = (str(path.resolve()), digest)
        require(str(path) not in observed or observed[str(path)] == binding,
                'release dependency changed during validation: ' + str(path))
        observed[str(path)] = binding


def file_hash(path):
    digest = _file_hash(path)
    _record(path, digest)
    return digest


def _bytes(path):
    raw = Path(path).read_bytes()
    _record(path, hashlib.sha256(raw).hexdigest())
    return raw


def _encode(dataset):
    return json.dumps({**dataset, 'directory': str(dataset['directory'])},
                      ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


class _ValidatedRelease(dict):
    """Per-load receipt; immutable canonical bytes never enter a global cache."""
    __slots__ = ('_receipt',)

    def __init__(self, dataset, receipt):
        super().__init__(dataset)
        self._receipt = receipt

    def __deepcopy__(self, memo):
        # Copies are ordinary data and confer no reusable admission authority.
        import copy
        return copy.deepcopy(dict(self), memo)


def validated_release(dataset):
    """Verify a loader-issued receipt or fully admit an ordinary dictionary.

    Every reuse rehashes dependency contents (including the sealed archive) and
    checks canonical data, then materializes an independent view. No timestamps,
    caller flags, shared mutable payloads or path-only cache keys grant trust.
    """
    if type(dataset) is not _ValidatedRelease:
        fresh = load_release(dataset['directory'])
        require(_encode(dataset) == fresh._receipt['payload'], 'supplied release differs from admitted content')
        return fresh
    receipt = dataset._receipt
    require(receipt['seal'] is _ADMITTED and _encode(dataset) == receipt['payload'],
            'validated release data was mutated')
    for path, (target, digest) in receipt['dependencies'].items():
        require(str(Path(path).resolve()) == target and file_hash(path) == digest,
                'validated release dependency changed: ' + path)
    return _materialize(receipt)


def _materialize(receipt):
    data = json.loads(receipt['payload'])
    data['directory'] = Path(data['directory'])
    return _ValidatedRelease(data, receipt)


class ReviewRequired(ValueError):
    """Content is inspectable, but required independent runtime reviews are absent."""


def read(path):
    return json.loads(_bytes(path))


def local_file(directory, name):
    path = (Path(directory) / name).resolve()
    require(path.is_relative_to(Path(directory).resolve()) and path.is_file(),
            'release file is missing or outside its directory: ' + name)
    return (Path(directory) / name).absolute()


def mapping_terms(row):
    from knowledge_foundry.reasoning.retrieval import mapping_atoms, VARIABLE
    return {v for atom in mapping_atoms(row) for v in atom if not VARIABLE.fullmatch(v)}


def _historical_reviews(directory, provenance, ledger):
    """Resolve original judgment bytes and identities; never substitute reviewers."""
    from knowledge_foundry.provenance import ProvenanceArchive
    package = (ROOT / provenance['input_package']).resolve()
    require(package.is_relative_to(ROOT), 'source package outside repository')
    for source in provenance['original_sources']:
        require(file_hash(local_file(package, source['package_path'])) == source['sha256'],
                'original source edition hash mismatch')
    pins = read(directory / 'checks/input-pins.json')
    for name, digest in pins['current_file_sha256'].items():
        require(file_hash(local_file(ROOT, name)) == digest, 'frozen formalization input changed: ' + name)
    cache = {}
    package_manifest = read(package / 'manifest.json')
    read(package / 'seal.json')
    read(package / 'provenance/index.json')
    with ProvenanceArchive(package) as archive:
        require(archive.manifest == package_manifest, 'provenance manifest changed during validation')
        _record(archive.path, archive.manifest['output_file_sha256']['provenance/history.zip'])
        for entry in ledger:
            for ref in entry['historical_judgments']:
                if 'archive_path' in ref:
                    key = ref['archive_path'].split('!/repository/', 1)[1]
                    if key not in cache:
                        raw = archive.read(key, expected_sha256=ref['file_sha256'])
                        cache[key] = (json.loads(raw), hashlib.sha256(raw).hexdigest())
                    document, digest = cache[key]
                    require(digest == ref['file_sha256'], 'historical review reference hash mismatch')
                else:
                    path = local_file(ROOT, ref['path'])
                    require(file_hash(path) == ref['file_sha256'], 'historical review file changed')
                    document = read(path)
                if 'pointer' in ref:
                    judgment = document
                    for segment in ref['pointer'].strip('/').split('/'):
                        judgment = judgment[segment.replace('~1', '/').replace('~0', '~')]
                elif 'collection' in ref:
                    key = 'candidate_id' if ref['collection'] == 'claims' else 'mapping_id'
                    judgment = next(j for j in document[ref['collection']] if j[key] == ref[key])
                else:
                    judgment = next(j for j in document['mappings'] if j['mapping_id'] == ref['mapping_id'])
                require(stable_hash(judgment) == ref['judgment_sha256'] and
                        judgment.get('reviewer', document.get('reviewer')) == ref['reviewer'],
                        'historical judgment or reviewer binding mismatch')
                expected_time = document['completed_at'] if ref.get('acceptance_decision') else judgment.get('reviewed_at')
                require(ref.get('reviewed_at') == expected_time, 'historical judgment timestamp changed')


def inspect_release(directory):
    """Inspect without runtime admission or a reusable admission receipt."""
    return _inspect_release(directory)[0]


def _inspect_release(directory):
    """Verify the frozen candidate and explicit decision without runtime admission.

    The current manifest may track mutable implementation outputs; the preserved
    original manifest is the authority for immutable candidate files. Source code
    input pins are historical and are never rebound to changed implementation.
    """
    directory = Path(directory).resolve()
    manifest = read(directory / 'manifest.json')
    frozen_record = read(local_file(directory, manifest['integration']['frozen_candidate_record']))
    raw = frozen_record['files']['manifest.json']['content'].encode('utf-8')
    frozen_digest = hashlib.sha256(raw).hexdigest()
    require(frozen_digest == frozen_record['original_manifest_sha256'] ==
            manifest['integration']['original_frozen_manifest_sha256'], 'frozen release manifest binding mismatch')
    frozen = json.loads(raw)
    for name, record in frozen_record['files'].items():
        data = record['content'].encode('utf-8')
        require(hashlib.sha256(data).hexdigest() == record['sha256'] and len(data) == record['bytes'],
                'preserved original bytes changed: ' + name)
    for name, digest in frozen['files'].items():
        if name in {'plan.md', 'handoff.md'}:
            actual = frozen_record['files'][name]['sha256']
        else:
            actual = file_hash(local_file(directory, name))
        require(actual == digest, 'frozen candidate file changed: ' + name)
    acceptance = read(directory / 'acceptance.json')
    original_acceptance = json.loads(frozen_record['files']['acceptance.json']['content'])
    require(acceptance['proposal'] == original_acceptance['proposal'], 'frozen acceptance proposal changed')
    require(stable_hash(acceptance['proposal']) == acceptance['proposal_canonical_sha256'] ==
            frozen['frozen_acceptance_proposal_canonical_sha256'], 'acceptance proposal binding mismatch')
    decision = acceptance.get('decision') or {}
    require(acceptance['content_acceptance_granted'] is True and decision.get('decision') == 'accept',
            'release content has not been explicitly accepted')
    require(decision['frozen_manifest_sha256'] == frozen_digest and
            decision['proposal_canonical_sha256'] == acceptance['proposal_canonical_sha256'],
            'content decision is not bound to the frozen candidate')
    ledger = [json.loads(x) for x in _bytes(directory / 'ledger.jsonl').splitlines() if x]
    require(len(ledger) == len({x['mapping_id'] for x in ledger}), 'duplicate release ledger mapping')
    eligible = {x['mapping_id']: x for x in ledger if x['disposition'] == 'eligible'}
    population = [{k: x[k] for k in ('mapping_id', 'mapping_sha256', 'claim_id', 'claim_sha256')}
                  for x in ledger if x['disposition'] == 'eligible']
    require(stable_hash(population) == frozen['eligible_population_canonical_sha256'] ==
            decision['eligible_population_canonical_sha256'], 'accepted population binding mismatch')
    require(decision['ledger_file_sha256'] == file_hash(directory / 'ledger.jsonl') and
            decision['accepted_mapping_count'] == len(eligible), 'accepted ledger population mismatch')
    provenance = read(directory / 'dataset/provenance.json')
    _historical_reviews(directory, provenance, ledger)
    bundle = read(directory / 'dataset/bundle.json')
    from scripts.validate_artifact_examples import SCHEMA
    require(read(ROOT / 'schemas/artifact-schema-v1.0.0.json') == SCHEMA,
            'artifact schema changed since validator import')
    check_claim_evidence(bundle)
    bundle_hash = stable_hash(bundle)
    schema = read(ROOT / 'schemas/reasoning-mapping-schema-v1.0.0.json')
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    sidecars, rows = [], []
    require(len(provenance['mapping_files']) == len(set(provenance['mapping_files'])), 'duplicate release mapping file')
    for name in provenance['mapping_files']:
        require('dataset/' + name in frozen['files'], 'mapping file is not frozen')
        side = read(local_file(directory / 'dataset', name))
        validator.validate(side)  # Preserve the per-file maximum of 100.
        require(side['bundle_sha256'] == bundle_hash and
                side['review']['content_sha256'] == stable_hash({k: v for k, v in side.items() if k != 'review'}),
                'release sidecar content binding mismatch')
        sidecars.append(side)
        rows.extend(side['mappings'])
    require(len(rows) == len({r['id'] for r in rows}) == len(eligible) and
            {r['id'] for r in rows} == set(eligible), 'release mapping population mismatch')
    require(set(provenance['included_mapping_ids']) == set(eligible), 'provenance mapping inventory mismatch')
    claims = {c['id']: c for c in bundle['claims']}
    require(set(claims) == {r['claim_id'] for r in rows}, 'release claim population mismatch')
    vocabulary = read(directory / 'dataset/vocabulary.json')['terms']
    require(decision['accepted_claim_count'] == len(claims) and
            decision['accepted_vocabulary_term_count'] == len(vocabulary), 'accepted claim/vocabulary counts differ')
    terms = {t['iri']: t for t in vocabulary}
    require(len(terms) == len(vocabulary), 'duplicate vocabulary term')
    reviews = [json.loads(x) for x in _bytes(directory / 'reviews.jsonl').splitlines() if x]
    new_reviews = {r['mapping_id']: r for r in reviews}
    require(len(new_reviews) == len(reviews), 'duplicate release assessment')
    term_hashes = {iri: stable_hash(value) for iri, value in terms.items()}
    for row in rows:
        entry, claim = eligible[row['id']], claims[row['claim_id']]
        require(stable_hash(row) == entry['mapping_sha256'] and
                stable_hash(claim) == row['claim_sha256'] == entry['claim_sha256'], 'release mapping/claim binding mismatch')
        require(claim['evidence'] == entry['evidence'], 'ledger evidence differs from claim')
        dependencies = {d['iri']: d['sha256'] for d in entry['vocabulary_dependencies']}
        require(set(dependencies) == mapping_terms(row), 'mapping vocabulary dependency omission')
        require(all(term_hashes.get(iri) == digest for iri, digest in dependencies.items()),
                'release vocabulary dependency binding mismatch')
        current = new_reviews.get(row['id'], {})
        if current:
            require(current['mapping_sha256'] == entry['mapping_sha256'] and
                    current['claim_sha256'] == entry['claim_sha256'], 'release assessment record binding mismatch')
        dimensions = {**entry['reused_dimensions'],
                      **{k: v['judgment'] for k, v in current.get('dimensions', {}).items()}}
        require(set(dimensions) == {'grounding', 'formalization', 'scope', 'vocabulary'} and
                all(v == 'pass' for v in dimensions.values()), 'eligible mapping lacks passing content dimensions')
    require(set(terms) == set().union(*(mapping_terms(r) for r in rows)), 'release vocabulary population mismatch')
    exclusions = decision['exclusions']
    withheld_source = local_file(ROOT, exclusions['withheld_inventory_source'])
    require(file_hash(withheld_source) == exclusions['withheld_inventory_file_sha256'] and
            exclusions['withheld_date_mapping_ids'] == read(withheld_source)['exclusions']['withheld_date_mapping_ids'],
            'withheld inventory binding mismatch')
    require(set(exclusions['blocked_mapping_ids']) == {e['mapping_id'] for e in ledger if e['disposition'] == 'blocked'} and
            len(exclusions['withheld_date_mapping_ids']) == exclusions['withheld_date_mapping_count'] ==
            frozen['population']['withheld_date_excluded'], 'release exclusion population mismatch')
    require(not set(exclusions['blocked_mapping_ids']) & set(eligible) and
            not set(exclusions['withheld_date_mapping_ids']) & set(eligible), 'excluded mapping in release')
    identity = {'frozen_manifest_sha256': frozen_digest,
                'proposal_canonical_sha256': acceptance['proposal_canonical_sha256'],
                'eligible_population_canonical_sha256': frozen['eligible_population_canonical_sha256'],
                'bundle_canonical_sha256': bundle_hash,
                'vocabulary_canonical_sha256': stable_hash(vocabulary),
                'ledger_file_sha256': file_hash(directory / 'ledger.jsonl'),
                'mapping_file_sha256': {n: file_hash(directory / 'dataset' / n) for n in provenance['mapping_files']}}
    dataset = {'format': FORMAT, 'directory': directory, 'bundle': bundle, 'sidecars': sidecars,
               'mapping': {'mappings': rows}, 'vocabulary': vocabulary, 'ledger': ledger,
               'provenance': provenance, 'content_reviews': new_reviews, 'acceptance': acceptance,
               'release_identity': identity, 'catalogue': {'questions': []},
               'trust': 'content_accepted_runtime_review_pending', 'digest': stable_hash(identity)}
    # Validate all claim AND vocabulary source references, not just proof goals.
    return dataset, release_evidence(dataset)


def release_evidence(dataset):
    """Return every exact required span and its owning mapping/vocabulary records."""
    if type(dataset) is _ValidatedRelease:
        require(_encode(dataset) == dataset._receipt['payload'], 'validated release data was mutated')
        return tuple(json.loads(dataset._receipt['evidence']))
    bundle = dataset['bundle']
    elements = {x['id']: x for x in bundle['elements']}
    documents = {x['id']: x for x in bundle['documents']}
    sources = {(x['id'], x['version']): x for x in bundle['sources']}
    claims = {x['id']: x for x in bundle['claims']}
    terms = {x['iri']: x for x in dataset['vocabulary']}
    chunks = {}
    for chunk in bundle['chunks']:
        for ref in chunk['evidence']:
            chunks.setdefault(evidence_id(ref), set()).add(chunk['id'])
    evidence, mapping_ids = {}, {}

    def references(refs):
        ids = set()
        for ref in refs:
            key = evidence_id(ref)
            ids.add(key)
            if key in evidence:
                require(evidence[key]['reference'] == ref, 'conflicting evidence identity')
                continue
            element, doc = elements[ref['element_id']], documents[ref['document_id']]
            span = ref['span']
            require(element['document_id'] == doc['id'] and doc['source_id'] == ref['source_id'] and
                    element['revision'] == ref['element_revision'] and
                    0 <= span['start'] < span['end'] <= len(element['text']), 'required release evidence span mismatch')
            source = sources[(doc['source_id'], doc['source_version'])]
            evidence[key] = {'id': key, 'reference': ref, 'text': element['text'][span['start']:span['end']],
                            'source': source, 'document': doc, 'location': element['location'], 'element_kind': element['kind'],
                            'origin': 'release-claim-or-vocabulary', 'original_chunk_ids': sorted(chunks.get(key, [])),
                            'context_ids': []}
        return ids

    claim_ids, term_ids = {}, {}
    for row in dataset['mapping']['mappings']:
        cid = row['claim_id']
        if cid not in claim_ids:
            claim_ids[cid] = references(claims[cid]['evidence'])
        ids = set(claim_ids[cid])
        for iri in sorted(mapping_terms(row)):
            if iri not in term_ids:
                term_ids[iri] = references(terms[iri]['supporting_source_references'])
            ids.update(term_ids[iri])
        mapping_ids[row['id']] = sorted(ids)
    # Mapping-specific complete evidence lists keep headings from joining
    # unrelated articles into an unbounded context graph.
    return evidence, mapping_ids


def _review_file(dataset, contract, dimension):
    spec = contract.get('reviews', {}).get(dimension)
    if not spec:
        raise ReviewRequired('Independent export review required: ' + dimension)
    path = local_file(dataset['directory'], spec['file'])
    require(file_hash(path) == spec['sha256'], 'runtime review file hash mismatch')
    review = read(path)
    require(review['binding'] == dataset['release_identity'], 'runtime review candidate binding mismatch')
    who = review['reviewer']
    require(who['kind'] in {'human', 'agent'} and bool(who['name'].strip()), 'reviewer identity required')
    require(datetime.fromisoformat(review['reviewed_at']).utcoffset() is not None and bool(review['notes'].strip()),
            'runtime review needs timezone and source inspection notes')
    return review


def admit_release(dataset):
    """Require actual separately identified export reviews; never accept a flag."""
    return _admit_release(dataset, release_evidence(dataset))


def _admit_release(dataset, evidence_index):
    contract = read(local_file(dataset['directory'], 'integration.json'))
    require(contract['format'] == FORMAT and contract['binding'] == dataset['release_identity'],
            'runtime release contract binding mismatch')
    require(contract.get('catalogue', []) == [], 'release v1 supports typed questions without a catalogue')
    grounding = _review_file(dataset, contract, 'grounding')
    semantic = _review_file(dataset, contract, 'formalization')
    require(grounding['reviewer']['name'] != semantic['reviewer']['name'],
            'grounding and formalization reviews must be separate')
    claims = {c['id']: c for c in dataset['bundle']['claims']}
    rows = {r['id']: r for r in dataset['mapping']['mappings']}
    grounded = {j['claim_id']: j for j in grounding['judgments']}
    mapped = {j['mapping_id']: j for j in semantic['judgments']}
    require(len(grounded) == len(grounding['judgments']) and set(grounded) == set(claims),
            'runtime grounding population mismatch')
    require(len(mapped) == len(semantic['judgments']) and set(mapped) == set(rows),
            'runtime semantic population mismatch')
    evidence, mapping_evidence = evidence_index
    ledger = {r['mapping_id']: r for r in dataset['ledger']}
    for cid, claim in claims.items():
        j = grounded[cid]
        require(j['grounding'] == 'pass' and j['claim_sha256'] == stable_hash(claim) and
                j['evidence_sha256'] == stable_hash(claim['evidence']) and bool(j['notes'].strip()),
                'claim lacks exact independent grounding pass')
    for mid, row in rows.items():
        j = mapped[mid]
        require(j['claim_id'] == row['claim_id'] and j['mapping_sha256'] == stable_hash(row) and
                j['claim_sha256'] == row['claim_sha256'] and
                j['vocabulary_dependencies_sha256'] == stable_hash(ledger[mid]['vocabulary_dependencies']) and
                j['evidence_sha256'] == stable_hash([evidence[k] for k in mapping_evidence[mid]]) and
                all(j['dimensions'].get(k) == 'pass' for k in ('formalization', 'scope', 'vocabulary')) and
                bool(j['notes'].strip()), 'mapping lacks exact independent semantic pass')
    return {**dataset, 'trust': 'accepted', 'runtime_reviews': {'grounding': grounding, 'formalization': semantic},
            'runtime_contract': contract,
            'digest': stable_hash({'identity': dataset['release_identity'], 'contract': contract})}


def load_release(directory):
    # Capture the contents actually inspected, not later timestamps or sizes.
    observed = {}
    token = _dependencies.set(observed)
    try:
        contract = read(local_file(directory, 'integration.json'))
        for dimension in ('grounding', 'formalization'):
            if not contract.get('reviews', {}).get(dimension):
                raise ReviewRequired('Independent export review required: ' + dimension)
        dataset, evidence_index = _inspect_release(directory)
        admitted = _admit_release(dataset, evidence_index)
        receipt = MappingProxyType({'seal': _ADMITTED, 'payload': _encode(admitted),
            'dependencies': MappingProxyType(dict(observed)),
            'evidence': json.dumps(evidence_index, ensure_ascii=False, separators=(',', ':')).encode()})
        return _ValidatedRelease(admitted, receipt)
    finally:
        _dependencies.reset(token)


def release_projection(dataset, *, evaluation=False):
    """Project all required evidence into an immutable, isolated-ready snapshot."""
    require(evaluation or dataset.get('trust') == 'accepted', 'runtime review required before release import')
    if not evaluation:
        dataset = validated_release(dataset)
    evidence, by_mapping = release_evidence(dataset)
    claims = {c['id']: c for c in dataset['bundle']['claims']}
    ledger = {r['mapping_id']: r for r in dataset['ledger']}
    runtime = dataset.get('runtime_reviews', {})
    grounding = {j['claim_id']: j for j in runtime.get('grounding', {}).get('judgments', [])}
    semantic = {j['mapping_id']: j for j in runtime.get('formalization', {}).get('judgments', [])}
    records = []
    for row in dataset['mapping']['mappings']:
        ids = by_mapping[row['id']]
        records.append({'id': 'release:' + row['id'], 'kind': 'passage', 'text': claims[row['claim_id']]['text'],
                        'source_ids': sorted({evidence[k]['source']['id'] for k in ids}),
                        'document_ids': sorted({evidence[k]['document']['id'] for k in ids}),
                        'evidence_ids': ids, 'metadata': {'mapping_id': row['id'], 'claim_id': row['claim_id'],
                        'dataset_sha256': dataset['digest'], 'review_provenance': {
                            'historical_judgments': ledger[row['id']]['historical_judgments'],
                            'new_assessment': dataset['content_reviews'].get(row['id']),
                            'runtime_grounding': grounding.get(row['claim_id']),
                            'runtime_formalization': semantic.get(row['id'])}}, 'vector': None})
    used_docs = {value['document']['id'] for value in evidence.values()}
    documents = [doc for doc in dataset['bundle']['documents'] if doc['id'] in used_docs]
    editions = {(doc['source_id'], doc['source_version']) for doc in documents}
    identity = {'projection_version': FORMAT, 'reasoning_dataset_sha256': dataset['digest'],
                'chunk_build_id': 'release-evidence:' + dataset['digest'],
                'knowledge_status': 'runtime_review_pending_evaluation' if evaluation else 'accepted',
                'release_identity': dataset['release_identity'], 'review_provenance': {
                    'ledger_sha256': dataset['release_identity']['ledger_file_sha256'],
                    'runtime_reviews': dataset.get('runtime_contract', {}).get('reviews', {}),
                    'runtime_reviewer_declarations': {key: {k: review[k] for k in
                        ('reviewer', 'reviewed_at', 'notes')} for key, review in runtime.items()}},
                'builds': dataset['bundle']['builds'], 'documents': documents,
                'unprojected_lineage_document_ids': sorted({d['id'] for d in dataset['bundle']['documents']} - used_docs),
                'sources': [s for s in dataset['bundle']['sources'] if (s['id'], s['version']) in editions]}
    return records, list(evidence.values()), identity
