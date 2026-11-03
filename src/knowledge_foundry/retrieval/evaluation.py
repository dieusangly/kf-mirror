"""Separate retrieval smoke metrics from mechanical answer and support checks."""
import copy
import time
import unicodedata

from haystack.core.errors import PipelineRuntimeError

from knowledge_foundry.retrieval.answering import generate
from knowledge_foundry.structural_chunks import stable_hash


class SnapshotEvaluationStore:
    """Confine unpublished reads to one explicitly selected evaluation snapshot."""
    def __init__(self, store, corpus, snapshot_id):
        if not snapshot_id:
            raise ValueError('unpublished evaluation requires an explicit snapshot ID')
        self.store, self.corpus, self.snapshot_id = store, corpus, snapshot_id

    def snapshot(self, corpus, snapshot_id=None):
        if corpus != self.corpus or snapshot_id != self.snapshot_id:
            raise ValueError('evaluation cannot select another snapshot or the active corpus')
        return self.store.evaluation_snapshot(corpus, snapshot_id)

    def __getattr__(self, name):
        return getattr(self.store, name)


def matches_locator(evidence, case):
    return (evidence['source']['id'] == case['source_id']
            and (not case.get('pages') or evidence['location'].get('page') in case['pages'])
            and (not case.get('evidence_ids') or evidence['id'] in case['evidence_ids']))


def normalized_anchor_text(value):
    return unicodedata.normalize('NFC', ' '.join(value.lower().split())).translate(
        str.maketrans({'’': "'", '‘': "'"}))


def matches_anchors(evidence, case):
    """Typography-normalized text checks complement locators, not entailment.

    Keep word content and accents; only canonical Unicode composition,
    apostrophe typography, case and whitespace are equivalent here. Citation
    resolution separately compares the original saved evidence exactly.
    """
    if not case.get('text_anchors'):
        return None
    text = normalized_anchor_text(' '.join(e['text'] for e in evidence if matches_locator(e, case)))
    return all(normalized_anchor_text(anchor) in text for anchor in case['text_anchors'])


def evidence_coverage(evidence, case):
    """Require each grounding's locator and every anchor in located evidence.

    Context is part of the retrieved evidence supplied to the answerer. Anchors
    may occur in different elements, but cannot span artificially joined text.
    Empty/missing grounding lists use the legacy top-level reference.
    """
    groundings = []
    for index, reference in enumerate(case.get('grounding') or [case]):
        located = [e for e in evidence if matches_locator(e, reference)]
        texts = [normalized_anchor_text(e['text']) for e in located]
        anchors = reference.get('text_anchors') or []
        missing = [a for a in anchors if not any(normalized_anchor_text(a) in t for t in texts)]
        groundings.append({'index': index, 'source_id': reference['source_id'],
                           'pages': reference.get('pages', []),
                           'evidence_ids': reference.get('evidence_ids', []),
                           'text_anchors': anchors, 'locator_hit': bool(located),
                           'text_anchor_hit': not missing if anchors else None,
                           'missing_text_anchors': missing,
                           'covered': bool(located) and not missing})
    covered = sum(g['covered'] for g in groundings)
    required = len(groundings)
    return {'required_groundings': required, 'covered_groundings': covered,
            'coverage': covered / required,
            'all_groundings_hit': covered == required, 'groundings': groundings}


def coverage_metrics(rows, prefix, *, excerpts=False):
    results = [r['excerpts']['evidence_coverage'] if excerpts else r['retrieved_evidence_coverage']
               for r in rows]
    required = sum(r['required_groundings'] for r in results)
    covered = sum(r['covered_groundings'] for r in results)
    hits = sum(r['all_groundings_hit'] for r in results)
    cases = len(results)
    return {f'{prefix}_coverage_cases': cases,
            f'{prefix}_required_groundings': required,
            f'{prefix}_covered_groundings': covered,
            f'{prefix}_grounding_coverage': covered / required if required else None,
            f'{prefix}_all_groundings_hits': hits if cases else None,
            f'{prefix}_all_groundings_hit_rate': hits / cases if cases else None}


def audit_answer(answer, store):
    """Check citation resolution separately from semantic answer support."""
    citations = answer['citations']
    saved = store.evidence(answer['snapshot_id'], [e['id'] for e in citations])
    resolved = sum(e['id'] in saved and all(saved[e['id']][k] == e[k] for k in
                   ('reference', 'text', 'source', 'document', 'location')) for e in citations)
    extractive = answer['status'] == 'extractive'
    return {'citations': len(citations), 'resolved_exactly': resolved,
            'citation_resolution_rate': resolved / len(citations) if citations else None,
            'exact_quote_support_rate': sum(e['text'] in answer['text'] for e in citations) / len(citations)
                if citations and extractive else None,
            'semantic_answer_support': 'not measured; review generated answers against cited passages'}


def compact_row(row):
    """Keep evaluation outcomes and usage without repeated source traces."""
    compact = {k: v for k, v in row.items() if k not in {
        'matched_evidence_ids', 'passage_ids', 'navigation_ids', 'resolution',
        'context_decisions', 'evidence_ids'}}
    if row.get('ranking') is not None:
        compact['ranking'] = {k: v for k, v in row['ranking'].items()
                             if k not in {'candidate_ids', 'admitted_ids', 'selected_ids'}}
    if 'excerpts' in row:
        compact['excerpts'] = {k: v for k, v in row['excerpts'].items() if k != 'text'}
    return compact


def evaluate(assistant, corpus, cases, *, methods=('keyword', 'vector', 'combined'),
             modes=('passage',), snapshot_id=None, sequential=False, check_excerpts=False, trace=False):
    snapshot = assistant.store.snapshot(corpus, snapshot_id)
    rows = []
    for mode in modes:
        for method in methods:
            history = []
            for case in cases:
                started = time.monotonic()
                try:
                    result = assistant.retrieve(corpus, case['question'], snapshot_id=snapshot['id'], method=method, mode=mode,
                                                history=case.get('history', history if sequential else []))
                    matches = []
                    for e in result['evidence']:
                        if not matches_locator(e, case) or e['is_context']:
                            continue
                        matches.append(e['id'])
                    row = {'id': case['id'], 'mode': mode, 'method': method,
                                 'hit': bool(matches), 'matched_evidence_ids': matches,
                                 'text_anchor_hit': matches_anchors(result['evidence'], case),
                                 'retrieved_evidence_coverage': evidence_coverage(result['evidence'], case),
                                 'passage_ids': result['passage_ids'],
                                 'navigation_ids': result.get('navigation_ids'),
                                 'resolution': result.get('resolution'),
                                 'context_decisions': result.get('context_decisions'),
                                 'evidence_ids': [e['id'] for e in result['evidence']],
                                 'query': result['query'], 'history_used': result.get('history_used'),
                                 'warnings': result['warnings'],
                                 'embedding_usage': result['embedding_usage'],
                                 'query_rewrite': result.get('query_rewrite'), 'ranking': result.get('ranking'),
                                 'latency_seconds': round(time.monotonic() - started, 3)}
                    if check_excerpts:
                        config = copy.deepcopy(assistant.config)
                        config['answer']['provider'] = 'extractive'
                        answer = list(generate(config, result['payload'], result['evidence']))[-1]['answer']
                        answer['snapshot_id'] = snapshot['id']
                        row['excerpts'] = {'status': answer['status'], 'text': answer['text'],
                            'warnings': answer['warnings'],
                            'locator_hit': any(matches_locator(e, case) for e in answer['citations']),
                            'text_anchor_hit': matches_anchors(answer['citations'], case),
                            'evidence_coverage': evidence_coverage(answer['citations'], case),
                            'citation_audit': audit_answer(answer, assistant.store)}
                    rows.append(row if trace else compact_row(row))
                except (ValueError, PipelineRuntimeError) as exc:
                    rows.append({'id': case['id'], 'mode': mode, 'method': method, 'error': str(exc)})
                if sequential:
                    history.append(case['question'])
    metrics = []
    for mode in modes:
        for method in methods:
            subset = [r for r in rows if r['mode'] == mode and r['method'] == method]
            completed = [r for r in subset if 'hit' in r]
            anchored = [r for r in completed if r['text_anchor_hit'] is not None]
            metrics.append({'mode': mode, 'method': method, 'completed': len(completed), 'cases': len(subset),
                            'hits': sum(r['hit'] for r in completed),
                            'text_anchor_cases': len(anchored),
                            'text_anchor_hits': sum(r['text_anchor_hit'] for r in anchored) if anchored else None,
                            'excerpt_anchor_hits': sum(r['excerpts']['text_anchor_hit'] for r in anchored)
                                if check_excerpts and anchored else None,
                            'hit_rate': sum(r['hit'] for r in completed) / len(completed) if completed else None,
                            **coverage_metrics(completed, 'retrieved'),
                            **coverage_metrics(completed if check_excerpts else [], 'extractive', excerpts=True)})
    return {'snapshot_id': snapshot['id'], 'top_k': assistant.config['search']['top_k'],
            'configuration': copy.deepcopy(assistant.config), 'cases_sha256': stable_hash(cases),
            'trace': trace,
            'label_status': 'draft smoke locators; source/page overlap is not semantic relevance',
            'sequential': sequential,
            'answer_support': 'unscored; exact text anchors and page overlap do not establish semantic support',
            'citation_correctness': 'mechanical resolution only when check_excerpts is enabled',
            'metrics': metrics, 'rows': rows}
