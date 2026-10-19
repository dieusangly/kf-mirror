"""Focused structural chunks with explicit, non-merging document context."""

from collections import defaultdict

from knowledge_foundry._structural_chunks_v1 import footnote_links, stable_hash
from knowledge_foundry.document_structure import assemble_document_hierarchy, ancestor_ids
from knowledge_foundry.evidence import claim_evidence_references, evidence_key

VERSION = "structural-chunks-v3"
MAX_ORIGINAL_CHUNKS = 4
MAX_ASSEMBLED_CHARACTERS = 12000


def assemble_structural_chunks(bundle, structure=None):
    """Preserve unique original membership; relationships never imply union.

    Only adjacent fragments and proven sentence/table continuations may merge,
    with hard component bounds. Section/list/footnote links remain inspectable
    relationships. Original chunks and their evidence are never rewritten.
    """
    from knowledge_foundry._structural_chunks_v1 import assemble_structural_chunks as legacy_assemble

    chunks = sorted(bundle['chunks'], key=lambda c: (c['document_id'], c['sequence'], c['id']))
    elements = {e['id']: e for e in bundle['elements']}
    hierarchy = {r['element_id']: r for r in assemble_document_hierarchy(bundle, structure)}
    owners = defaultdict(set)
    for i, chunk in enumerate(chunks):
        for ref in chunk['evidence']:
            if 'span' in ref:
                owners[ref['element_id']].add(i)
    by_chunk = {c['id']: i for i, c in enumerate(chunks)}
    parents = list(range(len(chunks)))
    members = {i: {i} for i in parents}

    def root(i):
        while parents[i] != i:
            i = parents[i]
        return i

    def can_merge(a, b):
        group = members[root(a)] | members[root(b)]
        ordered = sorted(group)
        return (len(group) <= MAX_ORIGINAL_CHUNKS and
                ordered == list(range(ordered[0], ordered[-1] + 1)) and
                sum(len(chunks[i]['text']) for i in group) <= MAX_ASSEMBLED_CHARACTERS and
                len({elements[r['element_id']]['kind'] == 'footnote'
                     for i in group for r in chunks[i]['evidence'] if 'span' in r}) <= 1)

    # Reuse the proven geometric continuation detectors, but not their union
    # result. In particular a note marker or repeated context cannot union groups.
    _, detected = legacy_assemble(bundle)
    links = []
    merge_reasons = {'shared_element_fragments', 'adjacent_sentence_continuation', 'adjacent_table_continuation'}
    for row in detected:
        a, b = (by_chunk[cid] for cid in row['original_chunk_ids'])
        merge = (row['reason'] in merge_reasons and abs(a - b) == 1 and can_merge(a, b))
        if row['reason'] == 'governing_context_reference':
            # Repeated context is a relationship only when the source hierarchy
            # supports it or the references are local in source flow.
            context = row['element_ids'][0]
            content = [r['element_id'] for i in (a, b)
                       if any(ref['element_id'] == context and 'span' not in ref for ref in chunks[i]['evidence'])
                       for r in chunks[i]['evidence'] if 'span' in r]
            location = elements[context]['location']
            if not any(context in ancestor_ids(eid, hierarchy) or
                       (abs(elements[eid]['ordinal'] - elements[context]['ordinal']) <= 1 and
                        elements[eid]['location'].get('article_id') == location.get('article_id') and
                        abs(elements[eid]['location'].get('page', 0) - location.get('page', 0)) <= 1)
                       for eid in content):
                continue
        if row['reason'] in {'ordered_list_introduction', 'unordered_list_introduction', 'nested_list_introduction'}:
            parent, child = row['element_ids']
            if parent not in ancestor_ids(child, hierarchy):
                continue
        links.append({**row, 'merges': merge})
        if merge and root(a) != root(b):
            ra, rb = root(a), root(b)
            parents[rb] = ra
            members[ra] |= members.pop(rb)
    # Explicit parent relationships also link chunks when parent and child are
    # separately chunked. The element hierarchy remains authoritative even if a
    # heading has only spanless references, or both endpoints share one chunk.
    for eid, row in hierarchy.items():
        parent = row['parent_element_id']
        if parent is None:
            continue
        for a in sorted(owners[parent]):
            for b in sorted(owners[eid]):
                if a != b:
                    links.append({'original_chunk_ids': [chunks[a]['id'], chunks[b]['id']],
                                  'element_ids': [parent, eid], 'reason': 'document_parent',
                                  'strength': 'heuristic', 'merges': False})
    groups = defaultdict(list)
    for i, chunk in enumerate(chunks):
        groups[root(i)].append(chunk)
    records = []
    for group in groups.values():
        refs = {}
        body_ids = {ref['element_id'] for chunk in group for ref in chunk['evidence'] if 'span' in ref}
        governing = {eid for child in body_ids for eid in ancestor_ids(child, hierarchy)}
        for chunk in group:
            saved_headings = {ref['element_id'] for ref in chunk['evidence']
                              if 'span' not in ref and elements[ref['element_id']]['kind'] == 'heading'}
            for ref in claim_evidence_references(chunk, elements):
                # Preserve original references in the canonical bundle, but do
                # not carry stale parser heading context into derived content.
                if governing and ref['element_id'] in saved_headings and ref['element_id'] not in governing | body_ids:
                    continue
                item = refs.setdefault(evidence_key(ref), {'reference': ref, 'original_chunk_ids': []})
                if chunk['id'] not in item['original_chunk_ids']:
                    item['original_chunk_ids'].append(chunk['id'])
        refs = sorted(refs.values(), key=lambda r: (elements[r['reference']['element_id']]['ordinal'],
                      r['reference'].get('span', {}).get('start', -1), evidence_key(r['reference'])))
        context_ids = sorted({parent for item in refs if 'span' in item['reference']
                              for parent in ancestor_ids(item['reference']['element_id'], hierarchy)},
                             key=lambda eid: (len(ancestor_ids(eid, hierarchy)), elements[eid]['ordinal'], eid))
        # Note hints supply local context without pulling notes (and their
        # neighbours) into the structural component or expanding its evidence.
        own_passages = {item['reference']['element_id'] for item in refs if 'span' in item['reference']}
        context_ids = sorted(set(context_ids) | {row['element_ids'][1] for row in detected
            if row['reason'] == 'same_page_unique_note_marker' and row['element_ids'][0] in own_passages},
            key=lambda eid: (len(ancestor_ids(eid, hierarchy)), elements[eid]['ordinal'], eid))
        own_elements = {item['reference']['element_id'] for item in refs}
        context_ids = [eid for eid in context_ids if eid not in own_elements]
        parts = []
        for item in refs:
            ref = item['reference']
            text = elements[ref['element_id']]['text']
            span = ref.get('span')
            parts.append(text[span['start']:span['end']] if span else text)
        ids = [c['id'] for c in group]
        # Context participates in identity, without becoming citable evidence.
        identity = [VERSION, ids, refs, [[eid, elements[eid]['revision']] for eid in context_ids]]
        records.append({'id': 'structural:' + stable_hash(identity),
                        'document_id': group[0]['document_id'], 'original_chunk_ids': ids,
                        'text': '\n\n'.join(parts), 'evidence_memberships': refs,
                        'context_element_ids': context_ids})
    unique_links = {stable_hash(row): row for row in links}
    return sorted(records, key=lambda r: r['id']), sorted(unique_links.values(), key=lambda r: (
        r['original_chunk_ids'], r['reason'], r['element_ids']))
