"""Separate retrieval smoke metrics from mechanical answer and support checks."""
import copy
import time

from haystack.core.errors import PipelineRuntimeError

from knowledge_foundry.retrieval.answering import generate


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


def matches_anchors(evidence, case):
    """Exact text checks complement draft page locators; they are not entailment."""
    if not case.get('text_anchors'):
        return None
    text = ' '.join(' '.join(e['text'].lower().split()) for e in evidence if matches_locator(e, case))
    return all(' '.join(anchor.lower().split()) in text for anchor in case['text_anchors'])


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


def evaluate(assistant, corpus, cases, *, methods=('keyword', 'vector', 'combined'),
             modes=('passage',), snapshot_id=None, sequential=False, check_excerpts=False):
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
                                 'passage_ids': result['passage_ids'],
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
                            'locator_hit': any(matches_locator(e, case) for e in answer['citations']),
                            'text_anchor_hit': matches_anchors(answer['citations'], case),
                            'citation_audit': audit_answer(answer, assistant.store)}
                    rows.append(row)
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
                            'hit_rate': sum(r['hit'] for r in completed) / len(completed) if completed else None})
    return {'snapshot_id': snapshot['id'], 'top_k': assistant.config['search']['top_k'],
            'label_status': 'draft smoke locators; source/page overlap is not semantic relevance',
            'sequential': sequential,
            'answer_support': 'unscored; exact text anchors and page overlap do not establish semantic support',
            'citation_correctness': 'mechanical resolution only when check_excerpts is enabled',
            'metrics': metrics, 'rows': rows}
