"""Explicit semantic mappings, bound to reviewed claims and exact source spans.

No prose extraction happens here. A mapping is executable knowledge and needs
its own review, separately from claim grounding. Classification is not consumed.
"""
import copy
import json
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

import pyoxigraph as ox
from jsonschema import Draft202012Validator, FormatChecker

from knowledge_foundry.retrieval.artifacts import evidence_id
from knowledge_foundry.structural_chunks import stable_hash
from scripts.validate_artifact_examples import check_claim_evidence

KF = 'https://knowledge-foundry.local/ontology/'
RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
OWL = 'http://www.w3.org/2002/07/owl#'
TYPE = RDF + 'type'
CONFLICT = KF + 'Contradiction'
ROOT = Path(__file__).resolve().parents[3]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def term(value, *, variable=False):
    require(isinstance(value, str), 'RDF terms must be strings')
    if variable and re.fullmatch(r'\?[A-Za-z][A-Za-z0-9_]*', value):
        return value
    require(re.fullmatch(r'(?:https?://|urn:)[^\s<>"{}|^`\\]+', value) is not None,
            'expected an absolute HTTP(S)/URN IRI: ' + value)
    return str(ox.NamedNode(value))


def atom(value, *, variable=False):
    require(isinstance(value, list) and len(value) == 3, 'an atom needs three IRI terms')
    return tuple(term(t, variable=variable) for t in value)


def content_digest(mapping):
    return stable_hash({k: v for k, v in mapping.items() if k != 'review'})


def _date(value):
    require(isinstance(value, str) and date.fromisoformat(value).isoformat() == value,
            'expected an ISO date')
    return value


def _review(mapping):
    review = mapping.get('review', {})
    require(review.get('status') == 'verified', 'semantic mapping is not reviewed')
    require(review.get('content_sha256') == content_digest(mapping), 'stale semantic review')
    reviewer = review.get('reviewer', {})
    require(reviewer.get('kind') in {'human', 'agent'} and bool(reviewer.get('name', '').strip()),
            'semantic review requires reviewer identity')
    require(bool(review.get('notes', '').strip()), 'semantic review requires source inspection notes')
    require(datetime.fromisoformat(review['reviewed_at']).utcoffset() is not None,
            'semantic review timestamp needs a timezone')


def compile_projection(bundle, mapping, *, jurisdiction, as_of):
    """Compile only verified, complete claims in one explicit jurisdiction/date.

    Exceptions require POSITIVE clearance evidence. Its absence is unknown, not
    false. A clearance and its exception together produce a conflict witness.
    """
    return _compile_projection(bundle, mapping, jurisdiction=jurisdiction, as_of=as_of, experimental=False)


def _compile_projection(bundle, mapping, *, jurisdiction, as_of, experimental):
    """Shared structural compiler; only the isolated harness bypasses REVIEW, never integrity.

    This function does not change statuses or content hashes. Accepted callers
    must enter through compile_projection, which enforces the semantic review.
    """
    check_claim_evidence(bundle)
    mapping = copy.deepcopy(mapping)
    schema = json.loads((ROOT / 'schemas/reasoning-mapping-schema-v1.0.0.json').read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(mapping)
    require(mapping.get('bundle_sha256') == stable_hash(bundle), 'semantic bundle binding mismatch')
    if not experimental:
        _review(mapping)
        mapped_claims = {r['claim_id'] for r in mapping['mappings']}
        require(all(c['grounding_review_status'] == 'verified' for c in bundle['claims']
                    if c['id'] in mapped_claims),
                'mapped claim must have verified grounding')
    return _assemble_projection(bundle, mapping, jurisdiction=jurisdiction, as_of=as_of,
                                experimental=experimental)


def compile_release_projection(dataset, *, jurisdiction, as_of, evaluation=False):
    """Compile validated sidecars without promoting original record statuses.

    Evaluation is an explicit offline entry point. Production requires either
    full admission or a loader-issued snapshot with rechecked content bindings.
    """
    from knowledge_foundry.reasoning.release import inspect_release, validated_release, release_evidence
    fresh = inspect_release(dataset['directory']) if evaluation else validated_release(dataset)
    require(fresh['release_identity'] == dataset['release_identity'], 'release changed before compilation')
    reviews = {e['mapping_id']: {'historical_judgments': e['historical_judgments'],
               'new_assessment': fresh['content_reviews'].get(e['mapping_id']),
               'runtime_review_files': fresh.get('runtime_contract', {}).get('reviews', {}),
               'runtime_review_pending': evaluation} for e in fresh['ledger'] if e['disposition'] == 'eligible'}
    mapping = {'mappings': fresh['mapping']['mappings'], 'review': {'release_identity': fresh['release_identity']}}
    projection = _assemble_projection(fresh['bundle'], mapping, jurisdiction=jurisdiction, as_of=as_of,
                                      experimental=evaluation, reviews=reviews)
    evidence, by_mapping = release_evidence(fresh)
    links = []
    # Vocabulary support is required proof evidence, including qualifications
    # which are not repeated in the mapped claim's own citation list.
    for row in projection['active']:
        node = 'urn:kf:mapping:' + quote(row['id'], safe='')
        for key in by_mapping[row['id']]:
            projection['citations'][key] = {k: evidence[key][k] for k in
                ('id', 'reference', 'text', 'source', 'document', 'location', 'element_kind')}
            links.append(f'{ox.NamedNode(node)} {ox.NamedNode(KF + "evidence")} {ox.NamedNode("urn:kf:evidence:" + key)} .')
    projection['provenance'] += '\n' + '\n'.join(links)
    projection.update(version='reviewed-release-v1' if not evaluation else 'release-evaluation-v1',
                      release_identity=fresh['release_identity'], evaluation_only=evaluation)
    return projection


def _assemble_projection(bundle, mapping, *, jurisdiction, as_of, experimental, reviews=None):
    """Shared atom/guard/provenance compiler after the caller's integrity/review gate."""
    from open_ontologies_lite.horn.rules import AtomPat, RulePattern

    _date(as_of)
    require(isinstance(jurisdiction, str) and jurisdiction.strip(), 'jurisdiction is required')
    claims = {c['id']: c for c in bundle['claims']}
    builds = {b['id']: b for b in bundle['builds']}
    elements = {e['id']: e for e in bundle['elements']}
    documents = {d['id']: d for d in bundle['documents']}
    sources = {(s['id'], s['version']): s for s in bundle['sources']}
    facts, owners, rules, rule_owners, citations = set(), {}, [], {}, {}
    metadata, active, excluded, seen = [], [], [], set()

    def meta(subject, predicate, value, iri=False):
        obj = ox.NamedNode(value) if iri else ox.Literal(value)
        metadata.append(f'{ox.NamedNode(subject)} {ox.NamedNode(predicate)} {obj} .')

    for row in mapping['mappings']:
        require(row['id'] not in seen, 'duplicate semantic mapping ID')
        seen.add(row['id'])
        claim = claims.get(row['claim_id'])
        require(claim is not None and stable_hash(claim) == row['claim_sha256'], 'stale or missing mapped claim')
        require(builds[claim['build_id']]['status'] == 'complete', 'mapped claim build is incomplete')
        start, end = _date(row['valid_from']), row.get('valid_until')
        if end is not None:
            _date(end)
            require(start < end, 'valid_until must be after valid_from (exclusive)')
        if row['jurisdiction'] != jurisdiction or as_of < start or end is not None and as_of >= end:
            excluded.append(row['id'])
            continue
        node = 'urn:kf:mapping:' + quote(row['id'], safe='')
        meta(node, TYPE, KF + ('UnreviewedClaim' if experimental else 'ReviewedClaim'), True)
        meta(node, KF + 'claimId', claim['id'])
        meta(node, KF + 'claimHash', row['claim_sha256'])
        meta(node, KF + 'claimText', claim['text'])
        meta(node, KF + 'buildId', claim['build_id'])
        meta(node, KF + 'jurisdiction', jurisdiction)
        meta(node, KF + 'validFrom', start)
        if end:
            meta(node, KF + 'validUntil', end)
        meta(node, KF + 'mapping', json.dumps(row, ensure_ascii=False, sort_keys=True))
        meta(node, KF + 'review', json.dumps(reviews[row['id']] if reviews is not None else mapping['review'],
                                           ensure_ascii=False, sort_keys=True))
        for ref in claim['evidence']:
            element, doc = elements[ref['element_id']], documents[ref['document_id']]
            source = sources[(doc['source_id'], doc['source_version'])]
            span = ref['span']
            key = evidence_id(ref)
            citations[key] = {'id': key, 'reference': ref, 'text': element['text'][span['start']:span['end']],
                             'source': source, 'document': doc, 'location': element['location'],
                             'element_kind': element['kind']}
            meta(node, KF + 'evidence', 'urn:kf:evidence:' + key, True)
            meta('urn:kf:evidence:' + key, KF + 'citation', json.dumps(citations[key], ensure_ascii=False, sort_keys=True))
            for predicate, value in (
                    ('sourceId', source['id']), ('sourceVersion', source['version']),
                    ('documentId', doc['id']), ('documentSHA256', doc['content_sha256']),
                    ('elementId', element['id']), ('elementRevision', element['revision']),
                    ('spanStart', span['start']), ('spanEnd', span['end']),
                    ('location', json.dumps(element['location'], ensure_ascii=False, sort_keys=True))):
                meta('urn:kf:evidence:' + key, KF + predicate, value)
            meta('urn:kf:evidence:' + key, KF + 'sourceURI', source['uri'], True)
        conditions = [atom(a, variable=True) for a in row.get('conditions', [])]
        exceptions = [(atom(e['when'], variable=True), atom(e['clearance'], variable=True))
                      for e in row.get('exceptions', [])]
        require(not row.get('facts') or not conditions and not exceptions,
                'conditional facts must be represented as guarded rules')
        for value in row.get('facts', []):
            fact = atom(value)
            facts.add(fact)
            owners.setdefault(fact, set()).add(row['id'])
            assertion = 'urn:kf:assertion:' + stable_hash(fact)
            meta(node, KF + 'assertion', assertion, True)
            for pred, obj in zip(('subject', 'predicate', 'object'), value):
                meta(assertion, RDF + pred, obj, True)
        for i, value in enumerate(row.get('rules', [])):
            name = row['id'] + ':' + str(i)
            body = [atom(a, variable=True) for a in value['body']] + conditions + [c for _, c in exceptions]
            require(bool(body), 'rules require at least one premise')
            head = atom(value['head'], variable=True)
            rules.append(RulePattern(name, tuple(AtomPat(*a) for a in body), AtomPat(*head)))
            rule_owners[name] = row['id']
            meta(node, KF + 'rule', name)
        for i, (when, clearance) in enumerate(exceptions):
            name = row['id'] + ':exception-conflict:' + str(i)
            rules.append(RulePattern(name, (AtomPat(*when), AtomPat(*clearance)),
                                     AtomPat(term(node), term(TYPE), term(CONFLICT))))
            rule_owners[name] = row['id']
        require(row.get('facts') or row.get('rules'), 'mapping must contain facts or rules')
        active.append(row)
    return {'version': 'reviewed-semantics-v1', 'bundle_sha256': stable_hash(bundle),
            'mapping_sha256': stable_hash(mapping), 'jurisdiction': jurisdiction, 'as_of': as_of,
            'asserted': facts, 'owners': owners, 'rules': rules, 'rule_owners': rule_owners,
            'citations': citations, 'active': active, 'excluded': excluded,
            'turtle': '\n'.join(' '.join(f) + ' .' for f in sorted(facts)),
            'provenance': '\n'.join(metadata)}
