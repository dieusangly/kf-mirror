"""Admit complete proofs only when all original citations resolve in the snapshot."""
import json
from uuid import uuid4

from knowledge_foundry.reasoning.adapter import require
from knowledge_foundry.retrieval.answering import request_tokens
from knowledge_foundry.retrieval.context import assemble_context
from knowledge_foundry.retrieval.pipeline import document


def withheld_proof(proof, reason):
    """Keep actual scope/check verdict while withholding inadmissible conclusions."""
    return {**{k: proof[k] for k in ('goal', 'jurisdiction', 'as_of', 'certificate') if k in proof},
            'status': 'insufficient', 'reason': reason, 'conclusions': [], 'premises': [], 'rules': [], 'citations': []}


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
    proof = engine.explain(goal)
    if proof['status'] not in {'inferred', 'asserted'}:
        return {**context, 'reasoning': proof}
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
        return {**context, 'reasoning': withheld_proof(proof, 'complete proof evidence exceeds context limits or is non-citable')}
    proof['citations'] = [{**c, 'label': labels[c['id']],
                           'role': 'source_passage' if c['id'] in citable else 'governing_context'} for c in citations]
    data = json.loads(assembled['payload']['input'])
    # The full proof remains in retrieval telemetry; the prompt gets the steps,
    # rules, scope and labels, rather than duplicating all original source text.
    data['reasoning'] = {k: proof[k] for k in ('status', 'goal', 'conclusions', 'premises', 'rules', 'proof',
                                             'jurisdiction', 'as_of', 'claim_ids')}
    data['reasoning']['citation_labels'] = [labels[c['id']] for c in citations if c['id'] in citable]
    data['reasoning']['verdict'] = proof['certificate']['verdict']
    assembled['payload']['input'] = json.dumps(data, ensure_ascii=False)
    assembled['payload']['instructions'] += (
        '\nThe reasoning field contains a checked symbolic derivation within its explicit scope. '
        'Explain its conclusion, premises and rules and cite the original evidence labels. '
        'Supplied rules are reviewed assumptions; the certificate proves the deduction, '
        'not the truth or current legal applicability of those assumptions. '
        'Do not extend its jurisdiction, date, conditions or exceptions.'
    )
    if request_tokens(assembled['payload'], config) > config['budget']['input_tokens']:
        return {**context, 'reasoning': withheld_proof(proof, 'complete proof exceeds input token budget')}
    return {**assembled, 'reasoning': proof}
