"""Assemble exact evidence with atomic context bundles and a full request budget."""
from knowledge_foundry.retrieval.answering import request_payload, request_tokens


def location_key(e):
    r = e['reference']
    span = r.get('span', {'start': 0, 'end': len(e['text'])})
    return (r['document_id'], r['element_id'], r['element_revision'], span['start'], span['end'])


def assemble_context(store, snapshot_id, documents, question, history, config, source_ids=None, document_ids=None):
    accepted, seen, warnings = [], set(), []
    max_evidence = config['search']['max_context_evidence']
    base = request_payload(config, question, history, [])
    if request_tokens(base, config) > config['budget']['input_tokens']:
        raise ValueError('question/history and prompt exceed the complete request budget')
    for doc in documents:
        seed_ids = doc.meta['evidence_ids']
        fetched = store.evidence(snapshot_id, seed_ids)
        if set(seed_ids) != set(fetched):
            raise ValueError('retrieval lineage is incomplete')
        bundle = [(fetched[k], False) for k in seed_ids]
        frontier, visited = list(fetched.values()), set(fetched)
        for _ in range(config['search']['max_context_depth']):
            ids = sorted({k for e in frontier for k in e['context_ids']} - visited)
            if not ids:
                break
            more = store.evidence(snapshot_id, ids)
            if set(ids) != set(more):
                raise ValueError('governing context lineage is incomplete')
            bundle.extend((more[k], True) for k in ids)
            visited.update(ids)
            frontier = list(more.values())
        if any(set(e['context_ids']) - visited for e in frontier):
            warnings.append('Context expansion reached the configured depth limit.')
        additions, keys = [], set()
        for value, is_context in bundle:
            if source_ids and value['source']['id'] not in source_ids:
                continue
            if document_ids and value['document']['id'] not in document_ids:
                continue
            if not value['text'].strip():
                continue
            key = location_key(value)
            if key in seen or key in keys:
                continue
            keys.add(key)
            additions.append({**value, 'label': 'E' + str(len(accepted) + len(additions) + 1),
                              'is_context': is_context})
        trial = accepted + additions
        payload = request_payload(config, question, history, trial)
        if len(trial) > max_evidence or request_tokens(payload, config) > config['budget']['input_tokens']:
            warnings.append('A passage and its governing context were excluded because the request budget was full.')
            continue
        accepted, seen = trial, seen | keys
    return {'evidence': accepted, 'payload': request_payload(config, question, history, accepted),
            'warnings': sorted(set(warnings))}
