"""Separate retrieval smoke metrics from mechanical answer and support checks."""
import time


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
             modes=('passage',), snapshot_id=None):
    snapshot = assistant.store.snapshot(corpus, snapshot_id)
    rows = []
    for mode in modes:
        for method in methods:
            for case in cases:
                started = time.monotonic()
                try:
                    result = assistant.retrieve(corpus, case['question'], snapshot_id=snapshot['id'], method=method, mode=mode)
                    matches = []
                    for e in result['evidence']:
                        if e['source']['id'] != case['source_id'] or e['is_context']:
                            continue
                        if case.get('pages') and e['location'].get('page') not in case['pages']:
                            continue
                        # Optional reviewed exact evidence IDs permit stricter evaluation.
                        if case.get('evidence_ids') and e['id'] not in case['evidence_ids']:
                            continue
                        matches.append(e['id'])
                    rows.append({'id': case['id'], 'mode': mode, 'method': method,
                                 'hit': bool(matches), 'matched_evidence_ids': matches,
                                 'passage_ids': result['passage_ids'],
                                 'embedding_usage': result['embedding_usage'],
                                 'latency_seconds': round(time.monotonic() - started, 3)})
                except ValueError as exc:
                    rows.append({'id': case['id'], 'mode': mode, 'method': method, 'error': str(exc)})
    metrics = []
    for mode in modes:
        for method in methods:
            subset = [r for r in rows if r['mode'] == mode and r['method'] == method]
            completed = [r for r in subset if 'hit' in r]
            metrics.append({'mode': mode, 'method': method, 'completed': len(completed), 'cases': len(subset),
                            'hits': sum(r['hit'] for r in completed),
                            'hit_rate': sum(r['hit'] for r in completed) / len(completed) if completed else None})
    return {'snapshot_id': snapshot['id'], 'top_k': assistant.config['search']['top_k'],
            'label_status': 'draft smoke locators; source/page overlap is not semantic relevance',
            'answer_support': 'not measured by this retrieval-only evaluation',
            'citation_correctness': 'not measured by this retrieval-only evaluation',
            'metrics': metrics, 'rows': rows}
