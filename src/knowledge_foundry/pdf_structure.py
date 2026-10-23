"""Conservative layout repairs for PDF text blocks; no document-specific rules."""

import re
import copy
from collections import defaultdict


NOTE_MARKER = re.compile(r"^(\d{1,3})\s+(?=\D)")
FILENAME_FOOTNOTE = re.compile(
    r"^\s*(?:\d+[.)]?\s+)?[^\s]+\.(?:pdf|docx?|xlsx?|pptx?|html?)\s*$", re.I)


def pdf_page_characters(path, page):
    """Read literal PDF glyphs with top-left geometry, without inferring text."""
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(path) as pdf:
        pdf_page = pdf[page - 1]
        try:
            height = pdf_page.get_height()
            text_page = pdf_page.get_textpage()
            try:
                characters = []
                for index in range(text_page.count_chars()):
                    left, bottom, right, top = text_page.get_charbox(index)
                    characters.append({'index': index, 'text': text_page.get_text_range(index, 1),
                                       'bbox': [left, height - top, right, height - bottom]})
                return characters
            finally:
                text_page.close()
        finally:
            pdf_page.close()


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

    def note_role(block):
        details = block['details']
        filename = bool(FILENAME_FOOTNOTE.fullmatch(block['text']))
        details['evidence_role'] = 'structural' if filename else 'authoritative'
        if filename:
            details['link_text_role'] = 'non_substantive_filename'
        else:
            details.pop('link_text_role', None)

    def merge(first, second, separator):
        refs = first['details'].setdefault('docling_refs', [first['details']['docling_ref']])
        refs.extend(second['details'].get('docling_refs', [second['details']['docling_ref']]))
        first['text'] += separator + second['text']
        a, b = first['location']['bbox'], second['location']['bbox']
        first['location']['bbox'] = [min(a[0], b[0]), min(a[1], b[1]),
                                     max(a[2], b[2]), max(a[3], b[3])]
        removed.add(second['locator_key'])

    for page, blocks in pages.items():
        height = page_sizes.get(page)
        if not height:
            continue
        blocks.sort(key=lambda b: (b['location']['bbox'][1], b['location']['bbox'][0]))
        # A separately extracted note number belongs to the following native
        # note, never to the preceding note's continuation. Require both body
        # reference and nearby same-column note geometry.
        for index, block in enumerate(blocks[:-1]):
            marker = re.fullmatch(r'\d{1,3}', block['text'].strip())
            following = blocks[index + 1]
            a, b = block['location']['bbox'], following['location']['bbox']
            if (marker and block['kind'] == 'paragraph' and a[1] >= height * .5 and
                    following['kind'] == 'footnote' and not NOTE_MARKER.match(following['text']) and
                    abs(a[0] - b[0]) <= height * .02 and
                    0 <= b[1] - a[3] <= max(4, (b[3] - b[1]) * 1.5) and
                    any(_mentions(prior['text'], marker[0]) for prior in blocks
                        if prior['location']['bbox'][3] < a[1] and
                        prior['kind'] not in {'footnote', 'heading'})):
                merge(block, following, ' ')
                block['kind'] = 'footnote'
        blocks = [block for block in blocks if block['locator_key'] not in removed]
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
                note_role(block)
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
                    re.fullmatch(r'\d{1,3}', block['text'].strip()) or
                    abs(box[0] - previous[0]) > height * .02 or
                    not 0 <= gap <= max(4, line_height * 1.5) or
                    line_height > height * .025):
                active = None
                continue
            merge(active, block, '\n')
            note_role(active)
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


def rotated_header_rows(cells, characters, comparable):
    """Join wrapped rotated headers across native cell boundaries from glyphs."""
    rows = defaultdict(list)
    for cell in cells:
        box = getattr(cell, 'bbox', None)
        if (getattr(cell, 'column_header', False) and box and hasattr(box, 'b') and
                abs(box.b - box.t) > 3 * (box.r - box.l)):
            rows[(cell.start_row_offset_idx, getattr(cell, 'row_span', 1) or 1)].append(cell)
    replacements = {}
    for row in rows.values():
        if len(row) < 2 or not characters:
            continue
        box = copy.copy(row[0].bbox)
        box.l = min(c.bbox.l for c in row)
        box.r = max(c.bbox.r for c in row)
        box.t = min(c.bbox.t for c in row)
        box.b = max(c.bbox.b for c in row)
        groups = table_text_groups(box, characters)
        if comparable(''.join(c.text for c in row)) != comparable(''.join(g['text'] for g in groups)):
            continue
        local = [g for c in row for g in table_text_groups(c.bbox, characters)]
        if [comparable(g['text']) for g in local] == [comparable(g['text']) for g in groups]:
            continue
        rewritten = []
        for index, group in enumerate(groups):
            cell = copy.copy(row[0])
            cell.text = ' '.join(group['text'].split())
            cell.start_col_offset_idx = min(c.start_col_offset_idx for c in row) + index
            cell.col_span = 1
            cell.bbox = copy.copy(box)
            cell.bbox.l, cell.bbox.r = group['left'], group['right']
            rewritten.append(cell)
        for cell in row:
            replacements[id(cell)] = []
        replacements[id(row[0])] = rewritten
    return [item for cell in cells for item in replacements.get(id(cell), [cell])], bool(replacements)


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
