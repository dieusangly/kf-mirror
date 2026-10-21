"""Admit ranked evidence spans with their complete governing context."""
from knowledge_foundry.retrieval.answering import request_payload, request_tokens


def location_key(e):
    r = e['reference']
    span = r.get('span', {'start': 0, 'end': len(e['text'])})
    return (r['document_id'], r['element_id'], r['element_revision'], span['start'], span['end'])


def assemble_context(store, snapshot_id, documents, question, history, config, source_ids=None, document_ids=None, *, query=None):
    accepted, seen, warnings = [], set(), []
    max_evidence = config['search']['max_context_evidence']
    base = request_payload(config, question, history, [])
    if request_tokens(base, config) > config['budget']['input_tokens']:
        raise ValueError('question/history and prompt exceed the complete request budget')
    seed_ids = list(dict.fromkeys(k for doc in documents for k in doc.meta['evidence_ids']))
    fetched = store.evidence(snapshot_id, seed_ids)
    if set(seed_ids) != set(fetched):
        raise ValueError('retrieval lineage is incomplete')
    list_groups = {}
    for rank, doc in enumerate(documents):
        for order, key in enumerate(doc.meta['evidence_ids']):
            fetched[key].setdefault('retrieval_passage', doc.id)
            fetched[key].setdefault('retrieval_rank', rank)
            fetched[key].setdefault('retrieval_order', order)
            if fetched[key]['element_kind'] == 'list_item':
                list_groups[key] = [k for k in doc.meta['evidence_ids']
                                    if fetched[k]['element_kind'] == 'list_item'
                                    and fetched[k]['context_ids'] == fetched[key]['context_ids']]
    list_seeds = [e for e in fetched.values() if e['element_kind'] == 'list_item']
    for key, siblings in (store.list_siblings(snapshot_id, list_seeds, max_evidence) if list_seeds else {}).items():
        seed = fetched[key]
        ids = list(dict.fromkeys([*list_groups[key], *(e['id'] for e in siblings)]))
        if len(siblings) > max_evidence:
            # Mark the group too large so atomic admission below excludes it.
            warnings.append('A complete list was excluded because its siblings exceed the evidence count budget.')
        for sibling in siblings:
            fetched.setdefault(sibling['id'], {**sibling, 'retrieval_passage': seed['retrieval_passage'],
                                               'retrieval_rank': seed['retrieval_rank'],
                                               'retrieval_order': len(fetched)})
        for member in ids:
            list_groups[member] = ids
    cached = dict(fetched)
    ranked = store.rank_evidence(snapshot_id, query if query is not None else '\n'.join(history + [question]),
                                 list(fetched.values()))
    ranked_by_id = {e['id']: e for e in ranked}
    cached.update(ranked_by_id)
    processed = set()
    for seed in ranked:
        if seed['id'] in processed:
            continue
        group = [ranked_by_id[k] for k in list_groups.get(seed['id'], [seed['id']])]
        processed.update(e['id'] for e in group)
        bundle = [(e, False) for e in group]
        frontier, visited = group, {e['id'] for e in group}
        for _ in range(config['search']['max_context_depth']):
            ids = sorted({k for e in frontier for k in e['context_ids']} - visited)
            if not ids:
                break
            missing = [k for k in ids if k not in cached]
            if missing:
                cached.update(store.evidence(snapshot_id, missing))
            more = {k: cached[k] for k in ids if k in cached}
            if set(ids) != set(more):
                raise ValueError('governing context lineage is incomplete')
            bundle.extend((more[k], True) for k in ids)
            visited.update(ids)
            frontier = list(more.values())
        if any(set(e['context_ids']) - visited for e in frontier):
            warnings.append('Evidence and its governing context were excluded because the context depth limit was reached.')
            continue
        additions, keys, promotions = [], set(), []
        for value, is_context in bundle:
            if source_ids and value['source']['id'] not in source_ids:
                continue
            if document_ids and value['document']['id'] not in document_ids:
                continue
            if not value['text'].strip():
                continue
            key = location_key(value)
            if not is_context:
                for prior in accepted:
                    if location_key(prior) == key:
                        promotions.append((prior, value))
            if key in seen or key in keys:
                continue
            keys.add(key)
            additions.append({**value, 'label': 'E' + str(len(accepted) + len(additions) + 1),
                              'is_context': is_context})
        trial = accepted + additions
        payload = request_payload(config, question, history, trial)
        if len(trial) > max_evidence:
            warnings.append('Evidence and its governing context were excluded because the evidence count budget was full.')
            continue
        if request_tokens(payload, config) > config['budget']['input_tokens']:
            warnings.append('Evidence and its governing context were excluded because the input token budget was full.')
            continue
        for prior, value in promotions:
            prior.update(is_context=False, lexical_score=value.get('lexical_score', 0))
            prior.update({k: value[k] for k in ('retrieval_passage', 'retrieval_rank', 'retrieval_order') if k in value})
            prior['context_ids'] = sorted(set(prior['context_ids']) | set(value['context_ids']))
        accepted, seen = trial, seen | keys
    return {'evidence': accepted, 'payload': request_payload(config, question, history, accepted),
            'warnings': sorted(set(warnings))}
