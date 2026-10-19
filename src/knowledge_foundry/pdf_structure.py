"""Conservative layout repairs for PDF text blocks; no document-specific rules."""

import re
from collections import defaultdict


NOTE_MARKER = re.compile(r"^(\d{1,3})\s+(?=\D)")


def _mentions(text, number):
    return bool(re.search(r"(?<![\w.\-])" + re.escape(number) + r"(?![\w°%\-]|\.\d)", text))


def repair_footnotes(items, page_sizes):
    """Recover bottom-note runs and merge same-page continuation fragments.

    Require a recognised note or at least two body-referenced numbered notes.
    Geometry bounds the run; numerical prefixes alone never establish a note.
    Retain every native reference when fragments are joined.
    """
    pages = defaultdict(list)
    for item in items:
        if item['location'].get('bbox') and not item['details'].get('visual_annotation'):
            pages[item['location']['page']].append(item)
    removed = set()
    for page, blocks in pages.items():
        height = page_sizes.get(page)
        if not height:
            continue
        blocks.sort(key=lambda b: (b['location']['bbox'][1], b['location']['bbox'][0]))
        starts = []
        for block in blocks:
            box = block['location']['bbox']
            marker = NOTE_MARKER.match(block['text'])
            if box[1] < height * .5 or not marker:
                continue
            if block['kind'] == 'footnote':
                starts.append(block)
            elif (block['kind'] == 'paragraph' and box[3] - box[1] <= height * .025 and
                  block['details'].get('evidence_role') == 'authoritative' and
                  any(_mentions(prior['text'], marker[1]) for prior in blocks
                      if prior['location']['bbox'][3] < box[1] and
                      prior['kind'] not in {'footnote', 'heading'})):
                starts.append(block)
        if not starts or (len(starts) < 2 and not any(b['kind'] == 'footnote' for b in starts)):
            continue
        top = min(b['location']['bbox'][1] for b in starts)
        supported = {b['locator_key'] for b in starts}
        active = None
        for block in blocks:
            box = block['location']['bbox']
            if box[1] < top:
                continue
            if block['locator_key'] in supported:
                block['kind'] = 'footnote'
                block['parent_key'] = None
                block['details']['heading_path'] = []
                block['details']['layout_repair'] = 'bottom_note_run'
                active = block
                continue
            if active is None:
                continue
            previous = active['location']['bbox']
            gap = box[1] - previous[3]
            line_height = box[3] - box[1]
            # Do not cross a column, figure, furniture, or a new numbered block.
            if (block['kind'] != 'paragraph' or
                    block['details'].get('evidence_role') != 'authoritative' or
                    re.match(r'^\d{1,3}[.)]?\s', block['text']) or
                    abs(box[0] - previous[0]) > height * .02 or
                    not 0 <= gap <= max(4, line_height * 1.5) or
                    line_height > height * .025):
                active = None
                continue
            refs = active['details'].setdefault('docling_refs', [active['details']['docling_ref']])
            refs.extend(block['details'].get('docling_refs', [block['details']['docling_ref']]))
            active['text'] += '\n' + block['text']
            active['location']['bbox'] = [min(previous[0], box[0]), previous[1],
                                          max(previous[2], box[2]), box[3]]
            removed.add(block['locator_key'])
    return [item for item in items if item['locator_key'] not in removed]


def table_text_groups(box, characters):
    """Split a merged vertical header or marker cell using PDF glyph positions.

    Keep neighbouring lines of one rotated label together. Return source-order
    characters, not text inferred from column names or evenly spaced columns.
    """
    if box is None or not characters:
        return []
    selected = [c for c in characters if c['text'].strip() and
                box.l - 1 <= (c['bbox'][0] + c['bbox'][2]) / 2 <= box.r + 1 and
                box.t - 1 <= (c['bbox'][1] + c['bbox'][3]) / 2 <= box.b + 1]
    if not selected:
        return []
    widths = sorted(c['bbox'][2] - c['bbox'][0] for c in selected)
    tolerance = widths[min(len(widths) - 1, int(len(widths) * .9))] * .7
    groups = []
    for char in sorted(selected, key=lambda c: c['bbox'][0]):
        left, _, right, _ = char['bbox']
        if not groups or left > groups[-1]['right'] + tolerance:
            groups.append({'left': left, 'right': right, 'characters': []})
        groups[-1]['right'] = max(groups[-1]['right'], right)
        groups[-1]['characters'].append(char)
    for group in groups:
        indices = {c['index'] for c in group['characters']}
        first, last = min(indices), max(indices)
        group['text'] = ''.join(c['text'] for c in characters if c['index'] in indices or
                               first <= c['index'] <= last and not c['text'].strip())
    return groups


def restore_numeric_identifiers(text, box, characters):
    """Restore separators dropped by extraction only when local glyphs prove them.

    Do not guess a separator from a digit pattern, another block, or an ambiguous
    pair of source identifiers with the same concatenated digits.
    """
    if not box or not characters:
        return text
    local = ''.join(c['text'] for c in characters
                    if box[0] - 1 <= (c['bbox'][0] + c['bbox'][2]) / 2 <= box[2] + 1
                    and box[1] - 1 <= (c['bbox'][1] + c['bbox'][3]) / 2 <= box[3] + 1)
    candidates = defaultdict(set)
    for match in re.finditer(r'(?<!\w)\d+(?:[-‐‑]\s*\d+)+(?!\w)', local):
        value = re.sub(r'\s+', '', match[0])
        candidates[re.sub(r'[-‐‑]', '', value)].add(value)
    for compact, values in candidates.items():
        if len(values) == 1:
            text = re.sub(r'(?<![\w-])' + re.escape(compact) + r'(?![\w-])',
                          lambda _: next(iter(values)), text)
    return text
