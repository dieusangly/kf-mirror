"""Conservative layout repairs for PDF text blocks; no document-specific rules."""

import re
import copy
from collections import Counter, defaultdict


NOTE_MARKER = re.compile(r"^(\d{1,3})\s+(?=\D)")
FILENAME_FOOTNOTE = re.compile(
    r"^\s*(?:\d+[.)]?\s+)?[^\s]+\.(?:pdf|docx?|xlsx?|pptx?|html?)\s*$", re.I)


def order_figure_blocks(items):
    """Order unambiguous single-column figure/caption units by source geometry."""
    by_page = defaultdict(list)
    for item in items:
        by_page[item['location']['page']].append(item)
    result = []
    for page_items in by_page.values():
        figures = [i for i in page_items if i['kind'] == 'figure' and i['location'].get('bbox')]
        owners = {}
        for node in page_items:
            if node['kind'] == 'figure' or not node['location'].get('bbox'):
                continue
            b = node['location']['bbox']
            candidates = [f for f in figures if node.get('parent_key') == f['locator_key'] or (
                node['kind'] == 'caption' and f['location']['bbox'][0] - 3 <= b[0] < f['location']['bbox'][2] and
                min(abs(b[3] - f['location']['bbox'][1]), abs(b[1] - f['location']['bbox'][3])) <= 30)]
            if len(candidates) == 1:
                owners[node['locator_key']] = candidates[0]['locator_key']
        groups = []
        for node in page_items:
            if node['locator_key'] in owners:
                continue
            group = [node, *(n for n in page_items if owners.get(n['locator_key']) == node['locator_key'])]
            boxes = [n['location'].get('bbox') for n in group]
            if not all(boxes):
                groups = []
                break
            box = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                   max(b[2] for b in boxes), max(b[3] for b in boxes)]
            captions = [n for n in group if n['kind'] == 'caption' and n['location']['bbox'][3] <= node['location']['bbox'][1] + 1]
            groups.append((box, captions + [n for n in group if n not in captions]))
        ordered = sorted(groups, key=lambda g: g[0][1])
        if (figures and ordered and all(b[0][1] >= a[0][3] - 1 and
                max(a[0][0], b[0][0]) < min(a[0][2], b[0][2]) for a, b in zip(ordered, ordered[1:]))):
            result.extend(n for _, group in ordered for n in group)
        else:
            result.extend(page_items)
    return result


def mark_prose_boxes(items, geometry, page_sizes):
    """Detach prose enclosed by a proven frame, preserving its paragraph order."""
    from knowledge_foundry.pdf_tables import table_rulings, _merge_lines, _coverage
    boxed_headings, framed = set(), set()
    for page, (width, height) in page_sizes.items():
        page_items = [i for i in items if i['location']['page'] == page]
        prose = [i for i in page_items if i['kind'] in {'paragraph', 'list_item'} and
                 i['details'].get('evidence_role') == 'authoritative' and i['location'].get('bbox')]
        if not prose:
            continue
        horizontal, vertical = table_rulings(geometry(page), [0, 0, width, height])
        vertical = _merge_lines(vertical)
        boxes = []
        for index, (left, top, bottom) in enumerate(vertical):
            for right, t, b in vertical[index + 1:]:
                if (right - left < width * .4 or abs(t-top) > 2 or abs(b-bottom) > 2 or bottom-top < 20 or
                        _coverage(horizontal, top, left, right) < right-left-2 or
                        _coverage(horizontal, bottom, left, right) < right-left-2):
                    continue
                box = [left, top, right, bottom]
                if any(i['kind'] in {'table', 'figure'} and i['location'].get('bbox') and
                       left <= i['location']['bbox'][0] <= i['location']['bbox'][2] <= right and
                       top <= i['location']['bbox'][1] <= i['location']['bbox'][3] <= bottom for i in page_items):
                    continue
                boxes.append(box)
        for node in prose:
            b = node['location']['bbox']
            matching = [box for box in boxes if box[0] <= b[0] and b[2] <= box[2] and box[1] <= b[1] and b[3] <= box[3]]
            if len(matching) == 1:
                node['details']['prose_box'] = {'page': page, 'bbox': matching[0]}
                framed.add(node['locator_key'])
                local_headings = [i for i in page_items if i['kind'] == 'heading' and
                                  i['location'].get('bbox') and matching[0][0] <= i['location']['bbox'][0] and
                                  i['location']['bbox'][2] <= matching[0][2] and
                                  matching[0][1] <= i['location']['bbox'][1] and
                                  i['location']['bbox'][3] <= matching[0][3]]
                boxed_headings.update(i['text'] for i in local_headings)
                framed.update(i['locator_key'] for i in local_headings)
                continuation = matching[0][1] <= height * .2 and any(
                    title in boxed_headings for title in node['details'].get('heading_path', []))
                if not local_headings and not continuation:
                    node['details']['heading_path'] = []
                    node['parent_key'] = None
    for position, node in enumerate(items):
        if node['locator_key'] in framed:
            continue
        path = node['details'].get('heading_path', [])
        closed = next((index for index, title in enumerate(path) if title in boxed_headings), None)
        if closed is not None:
            node['details']['heading_path'] = path[:closed] + ([node['text']] if node['kind'] == 'heading' else [])
            parent = next((i for i in items if i['locator_key'] == node.get('parent_key')), None)
            if parent is None or parent['kind'] == 'heading':
                node['parent_key'] = next((i['locator_key'] for i in reversed(items[:position])
                                           if i['kind'] == 'heading' and
                                           i['details'].get('heading_path') == node['details']['heading_path']), None)


def pdf_page_characters(path, page, *, styles=False):
    """Read literal PDF glyphs with top-left geometry, without inferring text."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_raw

    with pdfium.PdfDocument(path) as pdf:
        pdf_page = pdf[page - 1]
        try:
            height = pdf_page.get_height()
            text_page = pdf_page.get_textpage()
            try:
                characters = []
                for index in range(text_page.count_chars()):
                    left, bottom, right, top = text_page.get_charbox(index)
                    value = text_page.get_text_range(index, 1)
                    character = {'index': index, 'text': value,
                                 'bbox': [left, height - top, right, height - bottom]}
                    if styles and value.strip():
                        import ctypes
                        rgba = [ctypes.c_uint() for _ in range(4)]
                        if pdfium_raw.FPDFText_GetFillColor(text_page, index, *rgba):
                            character['color'] = [c.value for c in rgba]
                        character['font_weight'] = pdfium_raw.FPDFText_GetFontWeight(text_page, index)
                        if character['font_weight'] <= 0:
                            flags = ctypes.c_int()
                            length = pdfium_raw.FPDFText_GetFontInfo(text_page, index, None, 0, flags)
                            if 0 < length < 4096:
                                font_name = ctypes.create_string_buffer(length)
                                pdfium_raw.FPDFText_GetFontInfo(text_page, index, font_name, length, flags)
                                character['font_name'] = font_name.value.decode('utf-8', errors='replace')
                                if re.search(r'bold|black', character['font_name'], re.I):
                                    character['font_weight'] = 700
                                    character['font_weight_origin'] = 'source_font_name'
                        _, loose_bottom, _, loose_top = text_page.get_charbox(index, loose=True)
                        character['line_y'] = height - (loose_bottom + loose_top) / 2
                        character['line_size'] = loose_top - loose_bottom
                        character['space_before'] = bool(characters and characters[-1]['text'].isspace())
                    # PDFium can expose its internal hyphen sentinel instead of
                    # Unicode. Restore only when the source API proves the type.
                    if value == '\ufffe' and pdfium_raw.FPDFText_IsHyphen(text_page, index) == 1:
                        character.update(text='-', pdfium_hyphen=True, pdfium_text=value)
                    characters.append(character)
                for offset, character in enumerate(characters):
                    if not character.get('pdfium_hyphen'):
                        continue
                    following = next((c for c in characters[offset + 1:] if c['text'].strip()), None)
                    if (following and following['bbox'][1] > character['bbox'][3] + .5 and
                            following['bbox'][0] < character['bbox'][0]):
                        character['layout_break_after'] = True
                return characters
            finally:
                text_page.close()
        finally:
            pdf_page.close()


def _mentions(text, number):
    return bool(re.search(r"(?<![\w.\-])" + re.escape(number) + r"(?![\w°%\-]|\.\d)", text))


def separate_table_note_markers(cells, characters, native, page):
    """Bracket raised numeric note references, conserving source digits.

    Both the entire cell and the same-page note prefix must match literal PDF
    glyphs. A small digit alone is insufficient: require a raised baseline next
    to a full-size digit and a uniquely located note below the cell. Native and
    ruled adapters call this after establishing their cell geometry.
    """
    height = native.get('pages', {}).get(str(page), {}).get('size', {}).get('height')
    if not characters or not height:
        return cells

    def bounds(box):
        if isinstance(box, list):
            return box
        if not isinstance(box, dict):
            box = vars(box) if hasattr(box, '__dict__') else {
                k: getattr(box, k) for k in ('l', 't', 'r', 'b', 'coord_origin')}
        if str(box.get('coord_origin', 'TOPLEFT')).split('.')[-1] == 'TOPLEFT':
            return [box['l'], box['t'], box['r'], box['b']]
        return [box['l'], height - box['t'], box['r'], height - box['b']]

    def inside(box):
        return [c for c in characters if c['text'].strip() and
                box[0] - .5 <= (c['bbox'][0] + c['bbox'][2]) / 2 <= box[2] + .5 and
                box[1] - .5 <= (c['bbox'][1] + c['bbox'][3]) / 2 <= box[3] + .5]

    notes = defaultdict(list)
    for node in native.get('texts', []):
        marker = re.match(r'^(\d{1,3})\s+\S', node.get('text', ''))
        parts = node.get('prov', [])
        if not marker or node.get('label') != 'footnote' or len(parts) != 1:
            continue
        part = parts[0]
        if part.get('page_no') != page or not part.get('bbox'):
            continue
        box = bounds(part['bbox'])
        literal = ''.join(c['text'] for c in inside(box))
        # Verify the marker and opening words; later native line wrapping or
        # ligature normalization must not hide an otherwise literal reference.
        compact = lambda text: ''.join(text.translate(str.maketrans({'’': "'", '‘': "'"})).split())
        prefix = compact(node['text'])[:24]
        if compact(literal).startswith(prefix):
            notes[marker[1]].append({'docling_ref': node['self_ref'], 'page': page, 'bbox': box})

    result = []
    for cell in cells:
        cell = dict(cell)
        result.append(cell)
        if cell.get('bbox') is None:
            continue
        box = bounds(cell['bbox'])
        glyphs = inside(box)
        value = cell['text']
        positions = [i for i, c in enumerate(value) if not c.isspace()]
        if (not glyphs or ''.join(c['text'] for c in glyphs) !=
                ''.join(value.split()) or any(len(c['text']) != 1 for c in glyphs)):
            continue
        repairs = []
        index = 1
        while index < len(glyphs):
            prior, first = glyphs[index - 1], glyphs[index]
            a, b = prior['bbox'], first['bbox']
            size = a[3] - a[1]
            if (not prior['text'].isascii() or not prior['text'].isdigit() or
                    not first['text'].isascii() or not first['text'].isdigit() or size <= 0 or
                    not 0 < b[3] - b[1] <= size * .82 or
                    not size * .25 <= a[3] - b[3] <= size * .85 or
                    not -.5 <= b[0] - a[2] <= size):
                index += 1
                continue
            end = index + 1
            while end < len(glyphs):
                c, previous = glyphs[end], glyphs[end - 1]
                z = c['bbox']
                if (not c['text'].isascii() or not c['text'].isdigit() or
                        abs(z[3] - b[3]) > size * .15 or
                        z[3] - z[1] > size * .82 or
                        not -.5 <= z[0] - previous['bbox'][2] <= size * .6):
                    break
                end += 1
            marker = ''.join(c['text'] for c in glyphs[index:end])
            targets = [n for n in notes.get(marker, []) if n['bbox'][1] > box[3]]
            # A following same-line digit would make the number/marker split
            # ambiguous. Leave it unchanged for inspection.
            following = glyphs[end] if end < len(glyphs) else None
            if len(targets) == 1 and not (following and following['text'].isdigit() and
                    abs(following['bbox'][3] - a[3]) < size * .25):
                repairs.append((index, end, marker, targets[0]))
            index = end
        if not repairs:
            continue
        references = []
        for start, end, marker, target in reversed(repairs):
            left, right = positions[start], positions[end - 1] + 1
            value = value[:left].rstrip() + ' [' + marker + ']' + value[right:]
            selected = glyphs[start:end]
            references.insert(0, {'marker': marker, 'note': target,
                'bbox': [min(c['bbox'][0] for c in selected), min(c['bbox'][1] for c in selected),
                         max(c['bbox'][2] for c in selected), max(c['bbox'][3] for c in selected)],
                'glyph_indices': [c['index'] for c in selected]})
        cell.update(text=value, footnote_references=references)
    return result


def order_single_column_pages(flow, pages):
    """Restore geometric order only on unambiguous, nonoverlapping text pages.

    Do this before computing heading scope. Leave columns, nested visuals,
    tables, missing locations and multi-page nodes in native traversal order.
    A contiguous, single-column native list group can be ordered independently
    of unrelated pictures or columns elsewhere on the page.
    """
    by_page = defaultdict(list)
    blocked = set()
    text_labels = {'text', 'section_header', 'title', 'list_item', 'caption', 'footnote', 'table', 'document_index',
                   'page_header', 'page_footer', 'checkbox_selected', 'checkbox_unselected'}
    for index, (node, _) in enumerate(flow):
        parts = getattr(node, 'prov', []) or []
        if not parts:
            return list(flow)
        for part in parts:
            if len(parts) != 1 or str(getattr(node, 'label', '')).lower() not in text_labels:
                blocked.add(part.page_no)
        if len(parts) != 1:
            continue
        part = parts[0]
        if part.bbox is None or part.page_no not in pages:
            blocked.add(part.page_no)
            continue
        box = part.bbox.to_top_left_origin(pages[part.page_no].size.height)
        by_page[part.page_no].append((index, box))
    ordered = list(flow)
    for page, entries in by_page.items():
        if page in blocked:
            continue
        height = pages[page].size.height
        prose = [box for index, box in entries
                 if str(getattr(flow[index][0], 'label', '')).lower() in {'text', 'list_item'}
                 and box.r - box.l >= height * .15]
        if any(a.r <= b.l or b.r <= a.l for i, a in enumerate(prose) for b in prose[i + 1:]):
            continue
        geometric = sorted(entries, key=lambda entry: entry[1].t)
        if any(b.t < a.b - 1 for (_, a), (_, b) in zip(geometric, geometric[1:])):
            continue
        for (target, _), (source, _) in zip(entries, geometric):
            ordered[target] = flow[source]
    groups = defaultdict(list)
    for index, (node, _) in enumerate(ordered):
        parent = getattr(getattr(node, 'parent', None), 'cref', '')
        if parent.startswith('#/groups/'):
            groups[parent].append(index)
    for indices in groups.values():
        if len(indices) < 2 or indices != list(range(indices[0], indices[-1] + 1)):
            continue
        entries = []
        for index in indices:
            node = ordered[index][0]
            parts = getattr(node, 'prov', []) or []
            if (str(getattr(node, 'label', '')).lower() != 'list_item' or len(parts) != 1 or
                    parts[0].bbox is None or parts[0].page_no not in pages):
                break
            part = parts[0]
            entries.append((index, part.page_no,
                            part.bbox.to_top_left_origin(pages[part.page_no].size.height)))
        if len(entries) != len(indices) or len({page for _, page, _ in entries}) != 1:
            continue
        if max(b.l for _, _, b in entries) >= min(b.r for _, _, b in entries):
            continue
        geometric = sorted(entries, key=lambda e: e[2].t)
        # Two points allow slight detector-box overlap at adjoining lines,
        # without moving side-by-side alternatives or nested visual blocks.
        if any(a.t > b.t and a.t < b.b - 2
               for offset, (_, _, a) in enumerate(entries) for _, _, b in entries[offset + 1:]):
            continue
        original = list(ordered)
        for target, (source, _, _) in zip(indices, geometric):
            ordered[target] = original[source]
    # Preserve each column's native order, but require an upper panel to
    # precede a lower block spanning that panel's column. Sorting globally by
    # y would interleave ordinary two-column prose.
    regions = defaultdict(list)
    for index, (node, _) in enumerate(ordered):
        parts = getattr(node, 'prov', []) or []
        parent = getattr(getattr(node, 'parent', None), 'cref', '')
        label = str(getattr(node, 'label', '')).lower()
        if (len(parts) == 1 and parts[0].bbox is not None and parts[0].page_no in pages and
                label in text_labels and not parent.startswith('#/pictures/')):
            p = parts[0]
            regions[p.page_no].append((index, p.bbox.to_top_left_origin(pages[p.page_no].size.height)))
    for entries in regions.values():
        headings = [(i, b) for i, b in entries if str(getattr(ordered[i][0], 'label', '')).lower()
                    in {'section_header', 'title'}]
        panels = [(a, b) for j, (_, a) in enumerate(headings) for _, b in headings[j+1:]
                  if (a.r <= b.l or b.r <= a.l) and max(a.t, b.t) < min(a.b, b.b)]
        if not any(c.t >= max(a.b, b.b) and c.l <= min(a.l, b.l) and c.r >= max(a.r, b.r)
                   for a, b in panels for _, c in entries):
            continue
        predecessors = {i: set() for i, _ in entries}
        for i, a in entries:
            for j, b in entries:
                if a.b <= b.t - 1 and max(a.l, b.l) < min(a.r, b.r):
                    predecessors[j].add(i)
        pending = set(predecessors)
        sequence = []
        while pending:
            ready = [i for i in pending if not (predecessors[i] & pending)]
            if not ready:
                break
            chosen = min(ready)
            sequence.append(chosen); pending.remove(chosen)
        if not pending:
            original = list(ordered)
            for (target, _), source in zip(entries, sequence):
                ordered[target] = original[source]
    return ordered


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
            url_continuation = bool(re.search(r'(?:https?://|www\.)\S*$', active['text']))
            if (block['kind'] not in {'paragraph', 'footnote', 'list_item'} or
                    (block['details'].get('evidence_role') != 'authoritative' and not url_continuation) or
                    re.match(r'^\d{1,3}[.)]?\s', block['text']) or
                    re.fullmatch(r'\d{1,3}', block['text'].strip()) or
                    abs(box[0] - previous[0]) > height * .02 or
                    not 0 <= gap <= max(4, line_height * 1.5) or
                    line_height > height * .025):
                active = None
                continue
            merge(active, block, '' if url_continuation else '\n')
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
        if getattr(cell, 'column_header', False) and box and hasattr(box, 'b'):
            rows[(cell.start_row_offset_idx, getattr(cell, 'row_span', 1) or 1)].append(cell)
    replacements = {}
    for row in rows.values():
        vertical = [c for c in row if abs(c.bbox.b - c.bbox.t) > 3 * (c.bbox.r - c.bbox.l)]
        if not vertical or not characters:
            continue
        # A merged rotated label can be wider than it is tall. Include the
        # neighbouring headers in the same vertical band, even when their
        # native column indices are crossed. Horizontal headers stay native.
        top = min(c.bbox.t for c in vertical)
        bottom = max(c.bbox.b for c in vertical)
        vertical_ids = {id(c) for c in vertical}
        row = [c for c in row if id(c) in vertical_ids or
               c.bbox.b - c.bbox.t > (bottom - top) / 2]
        if len(row) < 2:
            continue
        box = copy.copy(row[0].bbox)
        box.l = min(c.bbox.l for c in row)
        box.r = max(c.bbox.r for c in row)
        box.t = min(c.bbox.t for c in row)
        box.b = max(c.bbox.b for c in row)
        groups = table_text_groups(box, characters)
        # Native column order is precisely what may be wrong. Prove full glyph
        # conservation independently of it; source geometry supplies the order.
        if Counter(comparable(''.join(c.text for c in row))) != Counter(
                comparable(''.join(g['text'] for g in groups))):
            continue
        local_groups = [table_text_groups(c.bbox, characters) for c in row]
        local = [g for parts in local_groups for g in parts]
        if (all(len(parts) == 1 for parts in local_groups) and
                [comparable(g['text']) for g in local] == [comparable(g['text']) for g in groups]):
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
            value = next(iter(values))
            # Extraction can lose only one separator of a compound identifier.
            # Existing separators must still occur at the source boundaries.
            pattern = r'[-‐‑]?'.join(re.escape(part) for part in re.split(r'[-‐‑]', value))
            text = re.sub(r'(?<![\w‐‑-])' + pattern + r'(?![\w‐‑-])',
                          lambda _: value, text)
    return text
