"""Conservative document-local links; detection never changes chunk membership."""

from collections import defaultdict
import hashlib
import json
import re

from knowledge_foundry.evidence import evidence_key

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


def detect_links(bundle):
    """Return deterministic derived records and auditable accepted link reasons."""
    chunks = sorted(bundle['chunks'], key=lambda c: (c['document_id'], c['sequence'], c['id']))
    elements = {e['id']: e for e in bundle['elements']}
    owners = defaultdict(set)
    for index, chunk in enumerate(chunks):
        for ref in chunk['evidence']:
            if 'span' in ref:
                owners[ref['element_id']].add(index)
    links = {}

    def link(a, b, reason, strength, element_ids):
        if a == b or chunks[a]['document_id'] != chunks[b]['document_id']:
            return
        a, b = sorted((a, b))
        key = (a, b, reason, tuple(element_ids))
        links[key] = {'original_chunk_ids': [chunks[a]['id'], chunks[b]['id']],
                      'element_ids': element_ids, 'reason': reason, 'strength': strength}

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
        family = None
        for item in flow[i + 1:]:
            marker = re.match(r'^\s*(?:([-–—•▪*])|([\w]+)[.)°])\s+', item['text'])
            item_family = ('bullet' if marker and marker[1] else
                           'number' if marker and marker[2].isdigit() else 'letter')
            a, b = intro['location'], item['location']
            if (item['document_id'] != intro['document_id'] or b.get('article_id') != a.get('article_id')
                    or item['kind'] != 'list_item' or marker is None
                    or (family is not None and family != item_family)
                    or (a.get('bbox') and b.get('bbox') and b['bbox'][0] < a['bbox'][0] - 2)):
                break
            family = item_family
            link_elements(intro['id'], item['id'],
                          'unordered_list_introduction' if family == 'bullet' else 'ordered_list_introduction')
            if i and flow[i - 1]['document_id'] == intro['document_id'] and \
                    flow[i - 1]['kind'] == 'list_item' and flow[i - 1]['text'].rstrip().endswith(':'):
                link_elements(flow[i - 1]['id'], intro['id'], 'nested_list_introduction')
    # A sentence can be split between a table/box and a body-text block on the
    # same page. Require adjacent source flow, unfinished syntax and matching
    # geometry; never join across a heading or figure just because wording fits.
    continuation_flow = sorted((e for e in bundle['elements'] if e['kind'] not in {'footnote', 'table_cell'}),
                               key=lambda e: (e['document_id'], e['ordinal'], e['id']))
    for previous, current in zip(continuation_flow, continuation_flow[1:]):
        a, b = previous['location'], current['location']
        left, right = previous['text'].rstrip(), current['text'].lstrip()
        if (previous['document_id'] != current['document_id'] or previous['kind'] not in {'paragraph', 'table'}
                or current['kind'] != 'paragraph' or a['format'] != 'pdf' or b['format'] != 'pdf'
                or a['page'] != b['page'] or not a.get('bbox') or not b.get('bbox')
                or not left or left.endswith(('.', ';', ':', '!', '?', '…')) or not right or not right[0].islower()):
            continue
        ab, bb = a['bbox'], b['bbox']
        # A table's enclosing box may include border/padding below its last text
        # cell. Allow a continuation line to overlap that edge, but not to sit
        # wholly inside the table or overlap an ordinary prose block.
        minimum_gap = -(bb[3] - bb[1]) if previous['kind'] == 'table' and bb[3] >= ab[3] else 0
        if (ab[0] - 2 <= bb[0] < bb[2] <= ab[2] + 2
                and minimum_gap <= bb[1] - ab[3] <= max(12, (bb[3] - bb[1]) * 1.5)):
            link_elements(previous['id'], current['id'], 'adjacent_sentence_continuation')
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
    return [links[k] for k in sorted(links)]
