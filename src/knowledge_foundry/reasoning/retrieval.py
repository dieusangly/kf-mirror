"""Validate bounded queries and admit complete proofs within snapshot evidence limits."""
import json
import re
from datetime import date
from uuid import uuid4

from knowledge_foundry.reasoning.adapter import atom, require
from knowledge_foundry.retrieval.answering import request_tokens
from knowledge_foundry.retrieval.context import assemble_context
from knowledge_foundry.retrieval.pipeline import document


VARIABLE = re.compile(r'\?[A-Za-z][A-Za-z0-9_]*\Z')
PLAN_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'status': {'type': 'string', 'enum': ['ready', 'clarify', 'unsupported']},
        'language': {'type': 'string', 'enum': ['fr', 'en']},
        'message': {'type': 'string'},
        'operation': {'type': 'string', 'enum': ['check', 'select']},
        'jurisdiction': {'type': 'string'},
        'as_of': {'type': 'string', 'pattern': r'^([0-9]{4}-[0-9]{2}-[0-9]{2})?$',
                  'description': 'ISO calendar date only: YYYY-MM-DD, without notes. Empty only for a non-ready plan with unknown scope.'},
        'patterns': {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'string'}}},
        'select': {'type': 'array', 'items': {'type': 'string'}},
    },
}
PLAN_SCHEMA['required'] = list(PLAN_SCHEMA['properties'])


class InvalidPlan(ValueError):
    """Malformed interpretation, distinct from insufficient knowledge coverage."""
    def __init__(self, message, *, plan):
        super().__init__(message)
        self.plan = plan


def validate_plan_shape(plan):
    from jsonschema import ValidationError, validate
    try:
        validate(plan, PLAN_SCHEMA)
    except ValidationError as exc:
        reason = ('invalid scope date; expected YYYY-MM-DD' if list(exc.absolute_path) == ['as_of'] else
                  'interpretation does not match the query plan schema')
        raise InvalidPlan(reason, plan=plan) from exc


def mapping_atoms(row):
    return [*row['facts'], *row['conditions'],
            *(a for r in row['rules'] for a in [*r['body'], r['head']]),
            *(a for e in row['exceptions'] for a in [e['when'], e['clearance']])]


def in_scope(row, jurisdiction, as_of):
    return (row['jurisdiction'] == jurisdiction and row['valid_from'] <= as_of and
            (row.get('valid_until') is None or as_of < row['valid_until']))


def knowledge_context(dataset, source_ids=None, document_ids=None, *, mapping_ids=None):
    """Ground interpretation in actual mappings/claim text and original excerpts.

    Never send catalogue goals/answers. Filters limit interpretation as well as
    proof admission. No silent sampling: a too-large context fails its budget.
    """
    bundle = dataset['bundle']
    claims = {c['id']: c for c in bundle['claims']}
    elements = {e['id']: e for e in bundle['elements']}
    docs = {d['id']: d for d in bundle['documents']}
    rows = []
    for mapping in dataset['mapping']['mappings']:
        if mapping_ids is not None and mapping['id'] not in mapping_ids:
            continue
        claim = claims[mapping['claim_id']]
        if any((source_ids and ref['source_id'] not in source_ids) or
               (document_ids and ref['document_id'] not in document_ids) for ref in claim['evidence']):
            continue
        passages = []
        for ref in claim['evidence']:
            doc, element = docs[ref['document_id']], elements[ref['element_id']]
            passages.append({'source_id': ref['source_id'], 'edition': doc['source_version'],
                             'location': element['location'],
                             'text': element['text'][ref['span']['start']:ref['span']['end']]})
        rows.append({'mapping': mapping, 'claim': claim['text'], 'original_passages': passages})
    used = {v for row in rows for a in mapping_atoms(row['mapping']) for v in a}
    vocabulary = [t for t in dataset.get('vocabulary', []) if t['iri'] in used]
    if dataset.get('format') == 'reviewed-reasoning-release-v1':
        from knowledge_foundry.structural_chunks import stable_hash
        # Citation arrays contain repeated IDs/hashes, not vocabulary meanings.
        # Keep every semantic field verbatim; full source support remains in the
        # snapshot and is mandatory when admitting any resulting proof.
        vocabulary = [{**{k: v for k, v in t.items() if k != 'supporting_source_references'},
                       'record_sha256': stable_hash(t),
                       'supporting_source_references_sha256': stable_hash(t['supporting_source_references'])}
                      for t in vocabulary]
    return {'trust': dataset.get('trust', 'accepted'), 'knowledge': rows,
            'vocabulary': vocabulary,
            'scope_boundary': 'Source-description snapshot; dates do not establish current legal applicability.'}


def bounded_knowledge_context(dataset, question, history=(), source_ids=None, document_ids=None,
                              *, input_tokens, tokenizer='cl100k_base'):
    """Rank source descriptions locally; admit complete backward dependency groups.

    The full scoped engine remains the proof/conflict authority. This selection
    bounds interpretation only. It never creates case facts or proves that an
    omitted mapping, exception or matching result does not exist.
    """
    import math
    from collections import Counter, defaultdict
    from knowledge_foundry.embeddings import token_count
    require(type(input_tokens) is int and input_tokens > 0, 'selection input budget exceeded')
    claims = {c['id']: c for c in dataset['bundle']['claims']}
    vocabulary = {t['iri']: t for t in dataset.get('vocabulary', [])}
    rows = {}
    words = lambda s: set(re.findall(r'[^\W_]{2,}', s.casefold(), re.UNICODE))
    # Scope dates remain verbatim in the model input/validator; their repeated
    # digits are not topical vocabulary for lexical ranking.
    query = words(re.sub(r'\b\d{4}-\d{2}-\d{2}\b', ' ', question + ' ' + ' '.join(history)))
    stop = words('the a an is are does do what which who how in on at of and or to for '
                 'le la les un une des du de et ou en au aux est sont que quel quelle quels quelles '
                 'source sources snapshot state states this cette indique elle instantané documentaire does dans')
    query -= stop
    elements = {e['id']: e for e in dataset['bundle']['elements']}
    documents, descriptions, frequencies = {}, {}, Counter()
    producers = defaultdict(list)
    for row in dataset['mapping']['mappings']:
        claim = claims[row['claim_id']]
        refs = list(claim['evidence'])
        used = {t for a in mapping_atoms(row) for t in a if not VARIABLE.fullmatch(t)}
        refs += [ref for iri in used if iri in vocabulary
                 for ref in vocabulary[iri]['supporting_source_references']]
        if any((source_ids and ref['source_id'] not in source_ids) or
               (document_ids and ref['document_id'] not in document_ids) for ref in refs):
            continue
        rows[row['id']] = row
        # Match acronym and number boundaries as well as ordinary camel case.
        # Original excerpts carry the source language when claim text does not.
        names = ' '.join(re.sub(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|'
                               r'(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])', ' ',
                               t.rsplit(':', 1)[-1].rsplit('/', 1)[-1]) for t in used)
        passages = ' '.join(elements[r['element_id']]['text'][r['span']['start']:r['span']['end']]
                            for r in claim['evidence'])
        descriptions[row['id']] = words(claim['text'] + ' ' + names + ' ' + passages)
        definitions = words(' '.join(vocabulary[t]['definition'] for t in used if t in vocabulary))
        documents[row['id']] = descriptions[row['id']] | definitions
        frequencies.update(documents[row['id']] & query)
        for head in [*row['facts'], *(r['head'] for r in row['rules'])]:
            producers[head[1]].append((row['id'], head))

    def dependencies(mid):
        found, pending = set(), [mid]
        while pending:
            current = pending.pop()
            if current in found:
                continue
            found.add(current)
            row = rows[current]
            bodies = [[*rule['body'], *row['conditions'], *(ex['clearance'] for ex in row['exceptions'])]
                      for rule in row['rules']]
            # Exception conflicts have their own conjunction, independent of
            # the rule guards, just as in the full engine's projection.
            bodies += [[ex['when'], ex['clearance']] for ex in row['exceptions']]
            for body in bodies:
                alternatives = [[owner for owner, head in producers[pattern[1]]
                                 if all(VARIABLE.fullmatch(a) or VARIABLE.fullmatch(b) or a == b
                                        for a, b in zip(pattern, head))] for pattern in body]
                # A conjunction with an unproducible premise cannot fire from
                # stored knowledge. Keep its complete description and vocabulary
                # above, without expanding unrelated alternatives for other
                # premises. The missing premise stays unknown, never false.
                if all(alternatives):
                    pending.extend(owner for owners in alternatives for owner in owners)
        return found

    # Build source/vocabulary payloads once. Trying a smaller complete group
    # after an oversized one must not repeatedly hash the entire vocabulary.
    payload = knowledge_context(dataset, source_ids, document_ids, mapping_ids=set(rows))
    payload_rows = {r['mapping']['id']: r for r in payload['knowledge']}
    payload_terms = {t['iri']: t for t in payload['vocabulary']}
    row_order = {mid: i for i, mid in enumerate(rows)}
    term_order = {iri: i for i, iri in enumerate(payload_terms)}
    used_terms = {mid: {t for a in mapping_atoms(row) for t in a} & payload_terms.keys()
                  for mid, row in rows.items()}

    def context(ids):
        used = set().union(*(used_terms[mid] for mid in ids))
        return {**payload, 'knowledge': [payload_rows[mid] for mid in sorted(ids, key=row_order.get)],
                'vocabulary': [payload_terms[t] for t in sorted(used, key=term_order.get)]}

    weights = {w: math.log(1 + len(rows) / frequencies[w]) for w in query if frequencies[w]}
    selected, covered = set(), Counter()
    remaining = {mid for mid in rows if documents[mid] & query}
    ranked = []
    reason = 'no_lexical_coverage'
    budget_omissions = False
    while remaining:
        # Diminishing gains prevent repeated descriptions of one query facet
        # from crowding out another facet of a conjunction or a rare term.
        if not ranked:
            ranked = sorted(remaining, key=lambda mid: (-sum(
                weights[w] * (1 if w in descriptions[mid] else .25) / (1 + covered[w])
                for w in documents[mid] & query) / math.sqrt(max(1, len(descriptions[mid]))), mid), reverse=True)
        mid = ranked.pop()
        remaining.remove(mid)
        if mid in selected:
            continue
        group = dependencies(mid)
        proposal = selected | group
        candidate = context(proposal)
        # Reserve room for selection metadata as well as the caller's model
        # instructions/schema/history reserve. Actual requests are checked again.
        if token_count(json.dumps(candidate, ensure_ascii=False), tokenizer) + 256 > input_tokens:
            budget_omissions = True
            continue
        for owner in proposal - selected:
            covered.update(documents[owner] & query)
        selected = proposal
        ranked = []  # Marginal scores change only when a group is admitted.
        reason = 'bounded_selection'
    if budget_omissions:
        reason = 'dependency_budget'
    result = context(selected)
    result['selection'] = {'version': 'dependency-selection-v2', 'candidate_mapping_count': len(rows),
                           'total_mapping_count': len(dataset['mapping']['mappings']),
                           'selected_mapping_ids': sorted(selected), 'omitted_mapping_count': len(rows) - len(selected),
                           'dependency_complete': True, 'reason': reason,
                           'exhaustive_for_corpus': len(selected) == len(dataset['mapping']['mappings']),
                           'boundary': 'Supported matches in selected knowledge only; omissions are unknown.'}
    require(token_count(json.dumps(result, ensure_ascii=False), tokenizer) <= input_tokens,
            'selection metadata exceeds input budget')
    return result


def validate_plan(plan, context, limits):
    validate_plan_shape(plan)
    require(len(plan['message']) <= 1500, 'interpretation message too long')
    if plan['status'] != 'ready':
        require(not plan['patterns'] and not plan['select'], 'non-ready plans must not contain queries')
        return plan
    try:
        valid_date = date.fromisoformat(plan['as_of']).isoformat() == plan['as_of']
    except ValueError:
        valid_date = False
    if not valid_date:
        raise InvalidPlan('invalid scope date; expected YYYY-MM-DD', plan=plan)
    rows = [v['mapping'] for v in context['knowledge'] if in_scope(v['mapping'], plan['jurisdiction'], plan['as_of'])]
    require(bool(rows), 'no knowledge in requested jurisdiction/date/filter scope')
    patterns = plan['patterns']
    require(0 < len(patterns) <= limits['max_patterns'], 'query pattern budget exceeded')
    atoms = [a for row in rows for a in mapping_atoms(row)]
    roles = [{a[i] for a in atoms if not VARIABLE.fullmatch(a[i])} for i in range(3)]
    variables, prior_terms = set(), set()
    for pattern in patterns:
        atom(pattern, variable=True)
        require(not VARIABLE.fullmatch(pattern[1]), 'predicate variables are unsupported')
        for i, value in enumerate(pattern):
            if VARIABLE.fullmatch(value):
                variables.add(value)
            else:
                require(value in roles[i], 'unknown term or incorrect term role')
        # Prevent unanchored scans and unrelated Cartesian products.
        require(any(not VARIABLE.fullmatch(v) for i, v in enumerate(pattern) if i != 1),
                'each pattern needs a known subject or object')
        endpoints = {pattern[0], pattern[2]}
        require(not prior_terms or bool(prior_terms & endpoints), 'disconnected query patterns')
        prior_terms.update(endpoints)
    require(len(plan['select']) == len(set(plan['select'])), 'duplicate selected variables')
    if plan['operation'] == 'check':
        require(not plan['select'] and not variables, 'checks require ground patterns')
    else:
        require(bool(plan['select']) and set(plan['select']) == variables, 'unbound or hidden result variables')
    return plan


def execute_plan(engine, plan, limits):
    """Enumerate finite closure matches, returning ground goals for proof admission.

    No match is an open-world unknown. Never silently truncate matches or claim
    completeness for an over-budget query. Each returned triple still needs
    engine.explain and the snapshot evidence gates.
    """
    require(engine.report is not None, 'engine has not run')
    require(engine.report['status'] == 'ready', 'reasoning closure is unavailable or unchecked')
    triples = set(engine.projection['asserted']) | {s.conclusion for s in engine.result.steps}
    require(len(triples) <= limits['max_facts'], 'closure fact budget exceeded')
    triples = sorted(tuple(v[1:-1] for v in triple) for triple in triples)
    matches, work = [({}, [])], 0
    for pattern in plan['patterns']:
        next_matches = []
        for binding, goals in matches:
            for triple in triples:
                work += 1
                require(work <= limits['max_work'], 'query work budget exceeded')
                candidate = dict(binding)
                for term, value in zip(pattern, triple):
                    if VARIABLE.fullmatch(term) and (term not in candidate or candidate[term] == value):
                        candidate[term] = value
                    elif term != value:
                        break
                else:
                    next_matches.append((candidate, [*goals, list(triple)]))
                    require(len(next_matches) <= limits['max_results'], 'result budget exceeded; narrow the question')
        matches = next_matches
    return {'bindings': [b for b, _ in matches],
            'goals': [list(g) for g in dict.fromkeys(tuple(g) for _, goals in matches for g in goals)],
            'work': work}


def withheld_proof(proof, reason, reason_code='missing_citations'):
    """Keep actual scope/check verdict while withholding inadmissible conclusions."""
    return {**{k: proof[k] for k in ('goal', 'jurisdiction', 'as_of', 'certificate') if k in proof},
            'status': 'insufficient', 'reason': reason, 'reason_code': reason_code,
            'conclusions': [], 'premises': [], 'rules': [], 'citations': []}


def configured_engine(config):
    from knowledge_foundry.reasoning.engine import ReasoningEngine
    from knowledge_foundry.retrieval.config import ROOT
    settings = config['reasoning']
    require(settings.get('enabled') is True, 'reasoning must be explicitly enabled')
    engine = ReasoningEngine.from_files(ROOT / settings['bundle'], ROOT / settings['mapping'],
                                        jurisdiction=settings['jurisdiction'], as_of=settings['as_of'])
    engine.run(ROOT / settings['output_dir'] / uuid4().hex, checker=settings.get('checker'),
               timeout=settings.get('timeout_seconds', 30.), max_iterations=settings.get('max_iterations', 100))
    return engine


def attach_reasoning(engine, goal, context, *, store, snapshot, documents, question, history, config,
                     source_ids=None, document_ids=None):
    goals = goal if goal and isinstance(goal[0], list) else [goal]
    proofs = [engine.explain(item) for item in goals]
    failed = next((p for p in proofs if p['status'] not in {'inferred', 'asserted'}), None)
    if failed is not None:
        return {**context, 'reasoning': failed}
    if len(proofs) == 1:
        proof = proofs[0]
    else:
        proof = {**proofs[0], 'goal': goals,
                 'status': 'inferred' if any(p['status'] == 'inferred' for p in proofs) else 'asserted',
                 'results': [{'goal': p['goal'], 'status': p['status']} for p in proofs]}
        for key in ('conclusions', 'premises', 'rules', 'proof', 'claim_ids', 'mappings', 'citations'):
            values = [v for p in proofs for v in p[key]]
            proof[key] = list({json.dumps(v, sort_keys=True): v for v in values}.values())
    citations = proof['citations']
    ids = [c['id'] for c in citations]
    fetched = store.evidence(snapshot['id'], ids)
    compatible = bool(ids) and set(ids) == set(fetched)
    for expected in citations:
        actual = fetched.get(expected['id'], {})
        compatible &= all(actual.get(k) == expected[k] for k in
                          ('reference', 'text', 'source', 'document', 'location', 'element_kind'))
        compatible &= not source_ids or expected['source']['id'] in source_ids
        compatible &= not document_ids or expected['document']['id'] in document_ids
    if not compatible:
        return {**context, 'reasoning': withheld_proof(proof, 'proof evidence is outside the selected snapshot or filters')}
    seed = document({'id': 'reasoning:premises', 'kind': 'passage', 'text': '', 'metadata': {},
                     'evidence_ids': ids, 'source_ids': sorted({c['source']['id'] for c in citations}),
                     'document_ids': sorted({c['document']['id'] for c in citations})})
    assembled = assemble_context(store, snapshot['id'], [seed, *documents], question, history, config,
                                 source_ids, document_ids, query=question)
    labels = {c['id']: c['label'] for c in assembled['evidence']}
    citable = {c['id'] for c in assembled['evidence'] if c['element_kind'] != 'heading'}
    if not set(ids) <= set(labels) or not set(ids) & citable:
        return {**context, 'reasoning': withheld_proof(proof, 'complete proof evidence exceeds context limits or is non-citable', 'evidence_budget')}
    proof['citations'] = [{**c, 'label': labels[c['id']],
                           'role': 'source_passage' if c['id'] in citable else 'governing_context'} for c in citations]
    data = json.loads(assembled['payload']['input'])
    # The full proof remains in retrieval telemetry; the prompt gets the steps,
    # rules, scope and labels, rather than duplicating all original source text.
    data['reasoning'] = {k: proof[k] for k in ('status', 'goal', 'conclusions', 'premises', 'rules', 'proof',
                                             'jurisdiction', 'as_of', 'claim_ids')}
    if 'results' in proof:
        data['reasoning']['results'] = proof['results']
    data['reasoning']['citation_labels'] = [labels[c['id']] for c in citations if c['id'] in citable]
    data['reasoning']['verdict'] = proof['certificate']['verdict']
    assembled['payload']['input'] = json.dumps(data, ensure_ascii=False)
    assembled['payload']['instructions'] += (
        '\nThe reasoning field contains checked symbolic results within their explicit scope. '
        'For status asserted, the conclusions match stored source assertions: no new deduction is established, '
        'and no rule or inferred step is required. Combining several stored assertions remains asserted. '
        'For status inferred only: The reasoning field contains a checked symbolic derivation. '
        'Explain only the deductions established by the supplied proof steps, premises '
        'and rules. Supplied rules are reviewed assumptions. Cite the original evidence labels. '
        'A certificate checks symbolic results, not source truth, natural-language interpretation, '
        'wording or current legal applicability. '
        'Do not extend its jurisdiction, date, conditions or exceptions.'
    )
    if request_tokens(assembled['payload'], config) > config['budget']['input_tokens']:
        return {**context, 'reasoning': withheld_proof(proof, 'complete proof exceeds input token budget', 'evidence_budget')}
    return {**assembled, 'reasoning': proof}
