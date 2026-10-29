"""Located citation support from immutable chunks, without changing membership."""

from collections import defaultdict

from knowledge_foundry.evidence import claim_evidence_references, evidence_key


SOURCE_CONTEXT_VERSION = 'original-neighbors-v1'


def uses_source_context(config):
    return config['steps']['claim_generation'].get('source_context') == SOURCE_CONTEXT_VERSION


def source_context_index(bundle):
    """Index independently citable spans; a spanless context is never promoted."""
    elements = {e['id']: e for e in bundle['elements']}
    references, flow = defaultdict(dict), defaultdict(list)
    notes, page_passages = defaultdict(list), defaultdict(list)
    for chunk in bundle['chunks']:
        for ref in claim_evidence_references(chunk, elements):
            if 'span' not in ref:
                continue
            entry = references[ref['element_id']].setdefault(
                evidence_key(ref), {'reference': ref, 'original_chunk_ids': []})
            entry['original_chunk_ids'].append(chunk['id'])
    for e in sorted(elements.values(), key=lambda e: (e['document_id'], e['ordinal'], e['id'])):
        if e['kind'] == 'footnote':
            notes[(e['document_id'], e['location'].get('page'))].append(e['id'])
        elif e['kind'] != 'table_cell':
            flow[e['document_id']].append(e['id'])
            if e['kind'] in {'paragraph', 'list_item', 'table'}:
                page_passages[(e['document_id'], e['location'].get('page'))].append(e['id'])
    positions = {eid: i for ids in flow.values() for i, eid in enumerate(ids)}
    return {'elements': elements, 'references': references, 'flow': flow,
            'positions': positions, 'notes': notes, 'page_passages': page_passages}


def supporting_passages(chunks, structural_chunks, index):
    """Bounded source neighbors and governing/note context, never semantic peers.

    Support may qualify a primary claim, but must not create claims on its own.
    Proximity is a reading aid, not proof that a qualification applies.
    """
    elements, targets = index['elements'], defaultdict(set)
    primary = {evidence_key(ref) for c in chunks
               for ref in claim_evidence_references(c, elements) if 'span' in ref}
    for record in structural_chunks:
        own = {m['reference']['element_id'] for m in record['evidence_memberships']
               if 'span' in m['reference']}
        for eid in record.get('context_element_ids', []):
            if elements[eid]['document_id'] == record['document_id']:
                targets[eid].add('governing_context')
        for eid in own:
            e = elements[eid]
            doc, location = e['document_id'], e['location']
            if e['kind'] == 'footnote' and location.get('page') is not None:
                # The rule can be the missing context when a note itself is
                # primary. Supply the page's exact body spans as reading aids;
                # do not guess a parent from an attached digit or proximity.
                for parent in index['page_passages'].get((doc, location['page']), ()):
                    targets[parent].add('same_page_note_parent_context')
            if e['kind'] in {'heading', 'figure', 'table_cell', 'footnote'}:
                continue
            if location.get('page') is not None:
                for note in index['notes'].get((doc, location['page']), ()):
                    targets[note].add('same_page_note')
            flow, position = index['flow'][doc], index['positions'][eid]
            for direction in (-1, 1):
                for distance in (1, 2):
                    at = position + direction * distance
                    if not 0 <= at < len(flow):
                        break
                    other = elements[flow[at]]
                    loc = other['location']
                    if (other['kind'] not in {'paragraph', 'list_item', 'table'} or
                            (location.get('article_id') and loc.get('article_id') and
                             location['article_id'] != loc['article_id']) or
                            abs(loc.get('page', 0) - location.get('page', 0)) > 1):
                        break
                    targets[other['id']].add('source_neighbor')
    result = []
    for eid in sorted(targets, key=lambda eid: (elements[eid]['document_id'], elements[eid]['ordinal'], eid)):
        for key, entry in sorted(index['references'].get(eid, {}).items()):
            if key not in primary:
                result.append({**entry, 'support_reasons': sorted(targets[eid])})
    return result
