"""Assemble original chunks using document-local structural evidence."""

from collections import defaultdict
import hashlib
import json
import re

from knowledge_foundry.evidence import claim_evidence_references, evidence_key

VERSION = "structural-chunks-v1"


def stable_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def footnote_links(chunks, elements):
    """Conservative same-page, unique numbered-note hints; never identity links."""
    notes, passages = defaultdict(dict), []
    for chunk in chunks:
        for ref in chunk['evidence']:
            if 'span' not in ref:
                continue
            element = elements[ref['element_id']]
            if element['location']['format'] != 'pdf':
                continue
            key = (ref['document_id'], element['location']['page'])
            text = element['text'][ref['span']['start']:ref['span']['end']]
            match = re.match(r'^(\d+)\s+', text)
            if element['kind'] == 'footnote' and match:
                notes[key][evidence_key(ref)] = (match[1], ref)
            elif element['kind'] not in {'footnote', 'heading'}:
                passages.append((key, text, ref))
    result = []
    for key, text, parent in passages:
        by_number = defaultdict(list)
        for number, note in notes[key].values():
            by_number[number].append(note)
        for number, refs in by_number.items():
            if len(refs) != 1 or elements[parent['element_id']]['ordinal'] >= elements[refs[0]['element_id']]['ordinal'] or re.match(r'^\s*' + number + r'[.)]\s', text):
                continue
            # Decimal/article identifiers and enumerated list labels are not note markers.
            if re.search(r'(?<![\w.\-])' + number + r'(?![\w°%\-]|\.\d)', text):
                result.append((parent, refs[0]))
    return result


def assemble_structural_chunks(bundle):
    """Return deterministic derived records and auditable accepted link reasons."""
    chunks = sorted(bundle['chunks'], key=lambda c: (c['document_id'], c['sequence'], c['id']))
    elements = {e['id']: e for e in bundle['elements']}
    owners = defaultdict(set)
    for index, chunk in enumerate(chunks):
        for ref in chunk['evidence']:
            if 'span' in ref:
                owners[ref['element_id']].add(index)
    parents = list(range(len(chunks)))
    links = {}

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    def link(a, b, reason, strength, element_ids):
        if a == b or chunks[a]['document_id'] != chunks[b]['document_id']:
            return
        a, b = sorted((a, b))
        key = (a, b, reason, tuple(element_ids))
        links[key] = {'original_chunk_ids': [chunks[a]['id'], chunks[b]['id']],
                      'element_ids': element_ids, 'reason': reason, 'strength': strength}
        parents[root(b)] = root(a)

    def link_elements(a, b, reason, strength='heuristic'):
        for i in sorted(owners[a]):
            for j in sorted(owners[b]):
                link(i, j, reason, strength, [a, b])

    for element_id, indices in sorted(owners.items()):
        if elements[element_id]['kind'] in {'paragraph', 'list_item', 'table', 'footnote'}:
            indices = sorted(indices)
            for i in indices[1:]:
                link(indices[0], i, 'shared_element_fragments', 'identity', [element_id])
    for i, chunk in enumerate(chunks):
        for ref in chunk['evidence']:
            if 'span' not in ref and elements[ref['element_id']]['kind'] != 'heading':
                for j in sorted(owners[ref['element_id']]):
                    link(i, j, 'governing_context_reference', 'identity', [ref['element_id']])
    # Consider the full element flow: intervening headings/content stop a list.
    flow = sorted((e for e in bundle['elements'] if e['kind'] not in {'footnote', 'figure', 'table_cell'}),
                  key=lambda e: (e['document_id'], e['ordinal'], e['id']))
    for i, intro in enumerate(flow):
        if intro['kind'] not in {'paragraph', 'list_item'} or not intro['text'].rstrip().endswith(':'):
            continue
        for item in flow[i + 1:]:
            if item['document_id'] != intro['document_id'] or item['location'].get('article_id') != intro['location'].get('article_id') or item['kind'] != 'list_item' or not re.match(
                    r'^[a-z][.)]\s', item['text'], re.I):
                break
            link_elements(intro['id'], item['id'], 'ordered_list_introduction')
            if i and flow[i - 1]['document_id'] == intro['document_id'] and \
                    flow[i - 1]['kind'] == 'list_item' and flow[i - 1]['text'].rstrip().endswith(':'):
                link_elements(flow[i - 1]['id'], intro['id'], 'nested_list_introduction')
    for previous, current in zip(flow, flow[1:]):
        a, b = previous['location'], current['location']
        if previous['kind'] != 'table' or current['kind'] != 'table' or \
                previous['document_id'] != current['document_id'] or a['format'] != 'pdf' or \
                b['format'] != 'pdf' or b['page'] != a['page'] + 1:
            continue
        left, right = previous['text'].splitlines()[-1].split('|'), current['text'].splitlines()[0].split('|')
        # Require an unfinished cell AND a lowercase continuation, not equal column counts alone.
        if len(left) >= 2 and len(right) >= 2 and len(right) <= len(left) and \
                re.match(r'^[a-zà-ÿ]', right[-1].lstrip()) and \
                left[-1].strip() and not left[-1].rstrip().endswith(('.', ';', ':', '!', '?')):
            link_elements(previous['id'], current['id'], 'adjacent_table_continuation')
    for passage, note in footnote_links(chunks, elements):
        link_elements(passage['element_id'], note['element_id'], 'same_page_unique_note_marker')
    groups = defaultdict(list)
    for i, chunk in enumerate(chunks):
        groups[root(i)].append(chunk)
    records = []
    for group in groups.values():
        refs = {}
        for chunk in group:
            for ref in claim_evidence_references(chunk, elements):
                key = evidence_key(ref)
                refs.setdefault(key, {'reference': ref, 'original_chunk_ids': []})
                refs[key]['original_chunk_ids'].append(chunk['id'])
        refs = sorted(refs.values(), key=lambda r: (elements[r['reference']['element_id']]['ordinal'],
                      r['reference'].get('span', {}).get('start', -1), evidence_key(r['reference'])))
        # Spanless context is retained in provenance, but is not promoted to evidence.
        parts = []
        for item in refs:
            ref = item['reference']
            text = elements[ref['element_id']]['text']
            span = ref.get('span')
            parts.append(text[span['start']:span['end']] if span else text)
        ids = [c['id'] for c in group]
        records.append({'id': 'structural:' + stable_hash([VERSION, ids, refs]),
                        'document_id': group[0]['document_id'], 'original_chunk_ids': ids,
                        'text': '\n\n'.join(parts), 'evidence_memberships': refs})
    return sorted(records, key=lambda r: r['id']), [links[k] for k in sorted(links)]
