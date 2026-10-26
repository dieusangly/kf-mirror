"""Source-evidenced corrections to native PDF structure, replayed by the audit.

The saved native parser document is never modified. All changes affect a copy
and produce a deterministic ledger bound to the original bytes. No document IDs,
page numbers, source names or expected passages participate in decisions.
"""
import copy
import ctypes
import hashlib
import json
import re
import statistics
import unicodedata
from functools import lru_cache
from collections import OrderedDict, defaultdict
from pathlib import Path

from knowledge_foundry.pdf_structure import pdf_page_characters


VERSION = 'pdf-source-v2'
_ADAPTED_CACHE = OrderedDict()


def key(value):
    return ''.join(unicodedata.normalize('NFKC', value).translate(str.maketrans(
        {'’': "'", '‘': "'", '‐': '-', '‑': '-', '\u00ad': ''})).split())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def bounds(part, height):
    b = part['bbox']
    return ([b['l'], b['t'], b['r'], b['b']] if b.get('coord_origin') == 'TOPLEFT'
            else [b['l'], height - b['t'], b['r'], height - b['b']])


def union(glyphs):
    return [min(c['bbox'][0] for c in glyphs), min(c['bbox'][1] for c in glyphs),
            max(c['bbox'][2] for c in glyphs), max(c['bbox'][3] for c in glyphs)]


def inside(chars, box, margin=.5):
    return [c for c in chars if c['text'].strip() and
            box[0] - margin <= (c['bbox'][0] + c['bbox'][2]) / 2 <= box[2] + margin and
            box[1] - margin <= (c['bbox'][1] + c['bbox'][3]) / 2 <= box[3] + margin]


def text(chars):
    """Read one physical line, retaining literal characters and word gaps."""
    chars = sorted(chars, key=lambda c: (c['bbox'][0], c['index']))
    value = ''
    for i, c in enumerate(chars):
        if i:
            prior = chars[i - 1]
            gap = c['bbox'][0] - prior['bbox'][2]
            size = max(c['bbox'][3] - c['bbox'][1], prior['bbox'][3] - prior['bbox'][1])
            if (c.get('space_before') or gap > size * .5) and not (c['text'] == prior['text'] == '.'):
                value += ' '
        value += c['text']
    return value


def lines(chars):
    groups = []
    for char in sorted((c for c in chars if c['text'].strip()),
                       key=lambda c: (c.get('line_y', (c['bbox'][1] + c['bbox'][3]) / 2), c['bbox'][0])):
        y = char.get('line_y', (char['bbox'][1] + char['bbox'][3]) / 2)
        size = char.get('line_size', char['bbox'][3] - char['bbox'][1])
        candidates = [g for g in groups if abs(g['y'] - y) <= min(g['size'], size) * .55]
        if len(candidates) == 1:
            g = candidates[0]
            g['chars'].append(char)
            g['y'] = statistics.median(c.get('line_y', (c['bbox'][1] + c['bbox'][3]) / 2) for c in g['chars'])
        else:
            groups.append({'y': y, 'size': size, 'chars': [char]})
    return [g['chars'] for g in sorted(groups, key=lambda g: g['y'])]


def joined_lines(groups):
    """Keep a literal short uppercase identifier hyphen across source lines."""
    value = ''
    for group in groups:
        fragment = text(group)
        identifier = re.search(r'\b[A-Z]{1,4}-$', value) and re.match(r'[A-Z]{1,4}\b', fragment)
        value += ('' if not value or identifier else '\n') + fragment
    return value


def provenance(page, box, value):
    return [{'page_no': page, 'bbox': dict(zip(('l', 't', 'r', 'b'), box), coord_origin='TOPLEFT'),
             'charspan': [0, len(value)]}]


def raster_display_title(value, box, page_height, words):
    """Distinguish large display lettering from ordinary scanned body prose."""
    return bool(words and 8 <= len(value.split()) <= 30 and
                box[3] - box[1] >= page_height * .1 and
                statistics.median(w['bbox'][3] - w['bbox'][1] for w in words) >= page_height * .025 and
                not value.rstrip().endswith(('.', ';', ':', '!', '?')))


def cell(value, row, column, box, *, header=False, span=1):
    return {'text': value, 'start_row_offset_idx': row, 'end_row_offset_idx': row + 1,
            'start_col_offset_idx': column, 'end_col_offset_idx': column + span,
            'row_span': 1, 'col_span': span, 'column_header': header, 'row_header': False,
            'row_section': False, 'fillable': False,
            'bbox': dict(zip(('l', 't', 'r', 'b'), box), coord_origin='TOPLEFT')}


def invisible_objects(path, page_no, glyphs):
    """Prove zero rendering contribution, on a private in-memory document."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    from PIL import ImageChops
    pointers = set()
    with pdfium.PdfDocument(path) as pdf:
        page = pdf[page_no - 1]
        tp = page.get_textpage()
        try:
            for c in glyphs:
                obj = raw.FPDFText_GetTextObject(tp, c['index'])
                if not obj:
                    return False
                pointers.add(ctypes.cast(obj, ctypes.c_void_p).value)
            # Never remove an object that also carries text outside this group.
            for index in range(tp.count_chars()):
                if index in {c['index'] for c in glyphs} or not tp.get_text_range(index, 1).strip():
                    continue
                obj = raw.FPDFText_GetTextObject(tp, index)
                if obj and ctypes.cast(obj, ctypes.c_void_p).value in pointers:
                    return False
        finally:
            tp.close()
        objects = [o for o in page.get_objects()
                   if ctypes.cast(o.raw, ctypes.c_void_p).value in pointers]
        if len(objects) != len(pointers) or not objects:
            page.close()
            return False
        before = page.render(scale=2).to_pil().convert('RGB').copy()
        for obj in objects:
            page.remove_obj(obj)
        after = page.render(scale=2).to_pil().convert('RGB').copy()
        unchanged = ImageChops.difference(before, after).getbbox() is None
        for obj in objects:
            obj.close()
        page.close()
        return unchanged


def contents_cells(table, chars, height):
    """Reconstruct explicit dotted-leader entry/page pairs, conserving glyphs."""
    box = bounds(table['prov'][0], height)
    selected = inside(chars, box, 2)
    physical = lines(selected)
    if sum(bool(re.search(r'(?:\.\s*){4,}', text(g))) for g in physical) < 5:
        return None
    entries, pending = [], []
    for group in physical:
        value = text(group)
        match = re.search(r'\d{1,3}\s*$', value)
        digits = []
        if match:
            count = len(match[0].strip())
            digits = sorted(group, key=lambda c: c['bbox'][0])[-count:]
        end = bool(digits and (re.search(r'(?:\.\s*){3,}\d+\s*$', value) or
                   re.fullmatch(r'\d+', value.strip()) and pending or
                   min(c['bbox'][0] for c in digits) >= box[2] - 25))
        if not end:
            pending.append(group)
            continue
        title_group = [c for c in group if c not in digits]
        title = pending + ([title_group] if title_group else [])
        pending = []
        if not title or not any(re.search(r'[^\W\d_]', text(g)) for g in title):
            return None
        entries.append((title, digits))
    if pending or len(entries) < 5:
        return None
    consumed = [c['index'] for title, digits in entries for c in [*sum(title, []), *digits]]
    if sorted(consumed) != sorted(c['index'] for c in selected):
        return None
    result = []
    for row, (title, digits) in enumerate(entries):
        value = joined_lines(title)
        result.extend([cell(value, row, 0, union(sum(title, []))),
                       cell(text(digits), row, 1, union(digits))])
    return result


def complete_final_row(table, chars, height):
    """Recover a fully populated last row of an unheaded, aligned native table."""
    raw = table['data']['table_cells']
    if not raw or any(c['column_header'] or c['row_span'] != 1 or c['col_span'] != 1 or not c.get('bbox') for c in raw):
        return None
    columns = sorted({c['start_col_offset_idx'] for c in raw})
    rows = sorted({c['start_row_offset_idx'] for c in raw})
    if len(columns) < 2 or len(rows) < 2 or len(raw) != len(columns) * len(rows):
        return None
    selected = inside(chars, bounds(table['prov'][0], height))
    owned = set()
    centers = defaultdict(list)
    row_centers = defaultdict(dict)
    last_sizes = {}
    bottom = 0
    for c in raw:
        b = bounds({'bbox': c['bbox']}, height)
        glyphs = inside(selected, b)
        literal = ''.join(g['text'] for g in sorted(glyphs, key=lambda g: g['index']))
        if key(c['text']) != key(literal) and not (c['text'].strip() == '-' and literal.strip() in {'—', '–'}):
            return None
        ids = {g['index'] for g in glyphs}
        if owned & ids:
            return None
        owned |= ids
        bottom = max(bottom, b[3])
        centers[c['start_col_offset_idx']].append((b[0] + b[2]) / 2)
        row_centers[c['start_col_offset_idx']][c['start_row_offset_idx']] = (b[1] + b[3]) / 2
        if c['start_row_offset_idx'] == max(rows) and glyphs:
            last_sizes[c['start_col_offset_idx']] = statistics.median(g['bbox'][3] - g['bbox'][1] for g in glyphs)
    remaining = [g for g in selected if g['index'] not in owned]
    if not remaining or min(g['bbox'][1] for g in remaining) < bottom:
        return None
    physical = lines(remaining)
    if len(physical) != 1:
        return None
    grouped = defaultdict(list)
    for g in remaining:
        center = (g['bbox'][0] + g['bbox'][2]) / 2
        distances = sorted((abs(center - statistics.median(v)), col) for col, v in centers.items())
        if len(distances) > 1 and distances[1][0] - distances[0][0] < 3:
            return None
        grouped[distances[0][1]].append(g)
    if set(grouped) != set(columns):
        return None
    result = copy.deepcopy(raw)
    for col in columns:
        glyphs = grouped[col]
        b = union(glyphs)
        if abs((b[0] + b[2]) / 2 - statistics.median(centers[col])) > 12 or not re.search(r'[^\W\d_]', text(glyphs)):
            return None
        baseline = [row_centers[col][r] for r in rows]
        step = statistics.median(z - a for a, z in zip(baseline, baseline[1:]))
        gap = (b[1] + b[3]) / 2 - baseline[-1]
        size = statistics.median(g['bbox'][3] - g['bbox'][1] for g in glyphs)
        if step <= 0 or not step * .5 <= gap <= step * 1.5 or not last_sizes.get(col) or not .85 <= size / last_sizes[col] <= 1.2:
            return None  # Small-print notes or a separate lower block are not rows.
        result.append(cell(text(glyphs), max(rows) + 1, col, b))
    return result


def coarsen_columns(table, chars, height, geometry):
    """Use a painted full-height divider and header witnesses on open tables."""
    raw = table['data']['table_cells']
    headers = [c for c in raw if c.get('column_header')]
    if (not headers or len({c['start_row_offset_idx'] for c in headers}) != 1 or
            any(c['col_span'] != 1 for c in headers)):
        return None  # Grouped/rotated leaf headers retain their explicit adapter.
    box = bounds(table['prov'][0], height)
    from knowledge_foundry.pdf_tables import _merge_lines
    vertical = _merge_lines([(s[0], min(s[1], s[3]), max(s[1], s[3])) for s in geometry['strokes']
                             if abs(s[0] - s[2]) < .01])
    dividers = sorted({round(x, 2) for x, top, bottom in vertical if
                       box[0] + 3 < x < box[2] - 3 and
                       abs(top - box[1]) < 3 and abs(bottom - box[3]) < 3})
    if not dividers:
        return None
    edges = [box[0], *dividers, box[2]]
    if len(edges) - 1 >= table['data']['num_cols']:
        return None
    selected = inside(chars, box)
    groups = defaultdict(list)
    owned = set()
    for c in raw:
        if not c.get('bbox') or c['row_span'] != 1:
            return None
        b = bounds({'bbox': c['bbox']}, height)
        glyphs = inside(selected, b)
        literal = ''.join(g['text'] for g in sorted(glyphs, key=lambda g: g['index']))
        if key(c['text']) != key(literal) and not (c['text'].strip() == '-' and literal.strip() in {'—', '–'}):
            return None
        ids = {g['index'] for g in glyphs}
        if ids & owned:
            return None
        owned |= ids
        cols = [i for i, (a, z) in enumerate(zip(edges, edges[1:])) if a <= b[0] and b[2] <= z]
        if len(cols) != 1:
            return None
        groups[c['start_row_offset_idx'], cols[0]].append((c, glyphs))
    if owned != {g['index'] for g in selected}:
        return None
    result = []
    for (row, col), members in sorted(groups.items()):
        glyphs = sum((g for _, g in members), [])
        if not glyphs:
            continue
        if row == 0 and (len(members) != 1 or not members[0][0]['column_header']):
            return None
        result.append(cell(joined_lines(lines(glyphs)), row, col, union(glyphs),
                           header=all(c['column_header'] for c, _ in members)))
    if {c['start_col_offset_idx'] for c in result if c['column_header']} != set(range(len(edges) - 1)):
        return None
    return result


@lru_cache(maxsize=8)
def _ocr_engine(command, tessdata_path, languages):
    import subprocess
    version = subprocess.run([command, '--version'], capture_output=True, text=True,
                             timeout=5, check=True).stdout.splitlines()[0]
    args = [command, '--list-langs']
    if tessdata_path:
        args += ['--tessdata-dir', tessdata_path]
    listing = subprocess.run(args, capture_output=True, text=True, timeout=5, check=True).stdout
    match = re.search(r'"([^"]+)"', listing)
    if not match:
        raise ValueError('cannot locate configured OCR language models')
    root = Path(match[1])
    return version, {lang: hashlib.sha256((root / (lang + '.traineddata')).read_bytes()).hexdigest()
                     for lang in languages}


def ocr_region(path, page_no, box, source_hash, options):
    """Bounded, local verification of source pixels using the configured engine.

    OCR caches are source/crop/settings/version specific and are never approvals.
    Tests replace this function; no network or neural PDF conversion is involved.
    """
    import io
    import subprocess
    import csv
    import pypdfium2 as pdfium
    command = options.get('tesseract_cmd', 'tesseract')
    settings = {'engine': command, 'languages': options.get('languages', ['fra', 'eng']),
                'scale': options.get('scale', 3.0), 'psm': 6,
                'tessdata_path': options.get('tessdata_path')}
    try:
        version, models = _ocr_engine(command, settings['tessdata_path'], tuple(settings['languages']))
        settings['version'] = version
        settings['traineddata_sha256'] = models
        identity = {'source_sha256': source_hash, 'page': page_no, 'bbox': box, 'settings': settings}
        cache = Path('artifacts/cache/pdf-source-verification') / (digest(identity) + '.json')
        if cache.is_file():
            saved = json.loads(cache.read_text())
            if saved.get('identity') == identity and saved.get('payload_sha256') == digest(saved.get('result')):
                return saved['result']
        with pdfium.PdfDocument(path) as pdf:
            page = pdf[page_no - 1]
            scale = settings['scale']
            image = page.render(scale=scale).to_pil()
            crop = image.crop(tuple(round(v * scale) for v in box))
            buffer = io.BytesIO()
            crop.save(buffer, format='PNG')
            page.close()
        args = [command, 'stdin', 'stdout', '-l', '+'.join(settings['languages']), '--psm', '6']
        if settings['tessdata_path']:
            args += ['--tessdata-dir', settings['tessdata_path']]
        result = subprocess.run([*args, 'tsv'], input=buffer.getvalue(), capture_output=True, timeout=30, check=True)
        words = []
        for row in csv.DictReader(io.StringIO(result.stdout.decode()), delimiter='\t'):
            if row['text'].strip() and int(row['level']) == 5:
                words.append({'text': row['text'], 'confidence': float(row['conf']),
                    'line': [int(row[k]) for k in ['block_num', 'par_num', 'line_num']],
                    'bbox': [box[0] + int(row['left']) / scale, box[1] + int(row['top']) / scale,
                             box[0] + (int(row['left']) + int(row['width'])) / scale,
                             box[1] + (int(row['top']) + int(row['height'])) / scale]})
        evidence = {'settings': settings, 'crop': box, 'words': words}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({'identity': identity, 'result': evidence, 'payload_sha256': digest(evidence)}))
        return evidence
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def adapt_native(native, path, ocr=None):
    """Return a corrected working copy plus source-bound, replayable evidence."""
    from collections import Counter
    from knowledge_foundry.pdf_tables import pdf_page_geometry
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    if not Path(path).is_file() or not native.get('pages'):
        return native, None
    source_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    cache_key = (str(Path(path).resolve()), source_hash, digest(native), digest(ocr or {'mode': 'off'}))
    if cache_key in _ADAPTED_CACHE:
        _ADAPTED_CACHE.move_to_end(cache_key)
        return copy.deepcopy(_ADAPTED_CACHE[cache_key])
    adapted = copy.deepcopy(native)
    repairs, uncertainties, char_cache, geometry_cache, image_cache = [], [], {}, {}, {}
    ocr = ocr or {'mode': 'off'}
    pages = adapted['pages']

    def chars(page):
        if page not in char_cache:
            char_cache[page] = pdf_page_characters(path, page, styles=True)
        return char_cache[page]

    def geometry(page):
        if page not in geometry_cache:
            geometry_cache[page] = pdf_page_geometry(path, page)
        return geometry_cache[page]

    def image_boxes(page):
        if page not in image_cache:
            with pdfium.PdfDocument(path) as pdf:
                pg = pdf[page - 1]
                h = pg.get_height()
                image_cache[page] = [[l, h - t, r, h - b] for obj in pg.get_objects()
                                     if obj.type == raw.FPDF_PAGEOBJ_IMAGE for l, b, r, t in [obj.get_bounds()]]
                pg.close()
        return image_cache[page]

    def record(node, before, method, evidence):
        if node == before:
            return
        repairs.append({'ref': node['self_ref'], 'method': method, 'before_sha256': digest(before),
                        'after_sha256': digest(node), 'evidence': copy.deepcopy(evidence)})

    def add_text(value, label, page, box, before_ref):
        ref = '#/texts/' + str(len(adapted['texts']))
        node = {'self_ref': ref, 'parent': {'$ref': '#/body'}, 'children': [],
                'content_layer': 'body', 'label': label, 'text': value, 'orig': value,
                'prov': provenance(page, box, value)}
        adapted['texts'].append(node)
        children = adapted.get('body', {}).get('children', [])
        at = next((i for i, c in enumerate(children) if c['$ref'] == before_ref), len(children))
        children.insert(at, {'$ref': ref})
        return node

    for table in adapted.get('tables', []):
        parts = table.get('prov', [])
        if (len(parts) != 1 or not parts[0].get('bbox') or
                not {'num_rows', 'num_cols', 'table_cells'} <= table.get('data', {}).keys()):
            continue
        page = parts[0]['page_no']
        height = pages[str(page)]['size']['height']
        before = copy.deepcopy(table)
        repaired = contents_cells(table, chars(page), height)
        method = 'source_contents_entries'
        if repaired is None:
            repaired = complete_final_row(table, chars(page), height)
            method = 'source_final_table_row'
        if repaired is None:
            repaired = coarsen_columns(table, chars(page), height, geometry(page))
            method = 'source_ruled_columns'
        if repaired is not None:
            table['data']['table_cells'] = repaired
            table['data']['num_rows'] = max(c['end_row_offset_idx'] for c in repaired)
            table['data']['num_cols'] = max(c['end_col_offset_idx'] for c in repaired)
            record(table, before, method, {'page': page, 'bbox': bounds(parts[0], height),
                                          'glyph_sha256': digest(chars(page))})
            continue
        # A caption can be split between a spanning title cell and a column
        # heading. Recover it only from a literal line and complete conservation.
        selected = inside(chars(page), bounds(parts[0], height))
        expected = Counter(key(''.join(c['text'] for c in table['data']['table_cells'])))
        if expected != Counter(key(''.join(c['text'] for c in selected))):
            continue
        for group in lines(selected):
            value = text(group)
            match = re.search(r'\b(?:Table|Tableau)\s+\d+\s*$', value, re.I)
            if not match:
                continue
            compact = key(match[0])
            ordered = sorted(group, key=lambda c: c['bbox'][0])
            caption = ordered[-len(compact):]
            caption_box = union(caption)
            changed = False
            for c in table['data']['table_cells']:
                if not c.get('bbox'):
                    continue
                b = bounds({'bbox': c['bbox']}, height)
                fragments = [g for g in caption if b[0] - .5 <= (g['bbox'][0] + g['bbox'][2]) / 2 <= b[2] + .5 and
                             b[1] - .5 <= (g['bbox'][1] + g['bbox'][3]) / 2 <= b[3] + .5]
                if not fragments:
                    continue
                # Native cells may overestimate a box and include a neighbour's
                # caption word; remove only an exact prefix/suffix of its text.
                for fragment in [text(fragments), match[0].strip(), match[0].split()[0], match[0].split()[-1]]:
                    if c['text'].startswith(fragment + ' '):
                        c['text'] = c['text'][len(fragment):].lstrip(); changed = True; break
                    if c['text'].endswith(' ' + fragment):
                        c['text'] = c['text'][:-len(fragment)].rstrip(); changed = True; break
            actual = Counter(key(''.join(c['text'] for c in table['data']['table_cells']) + text(caption)))
            if changed and actual == expected:
                added = add_text(text(caption), 'caption', page, caption_box, table['self_ref'])
                record(table, before, 'source_table_caption', {'page': page, 'glyph_indices': [g['index'] for g in caption],
                                                              'caption': added})
            else:
                table.clear(); table.update(before)
            break

    # Split merged parenthesized notes using physical source-line starts. This
    # preserves native refs by appending explicit source-derived text nodes.
    for node in list(adapted.get('texts', [])):
        parts = node.get('prov', [])
        if node.get('label') != 'footnote' or len(parts) != 1 or not parts[0].get('bbox'):
            continue
        page = parts[0]['page_no']; height = pages[str(page)]['size']['height']
        box = bounds(parts[0], height)
        selected = inside(chars(page), box)
        ordered = sorted(selected, key=lambda c: c['index'])
        if key(node['text']) != key(''.join(c['text'] for c in ordered)):
            continue
        physical = lines(selected)
        starts = [i for i, g in enumerate(physical) if re.match(r'^\(\s*\d{1,3}\s*\)\s*', text(g)) and
                  abs(union(g)[0] - box[0]) <= 2]
        if len(starts) < 2 or starts[0] != 0:
            continue
        before = copy.deepcopy(node); fragments = []
        for begin, end in zip(starts, starts[1:] + [len(physical)]):
            glyphs = sum(physical[begin:end], [])
            value = joined_lines(physical[begin:end])
            fragments.append((value, union(glyphs), glyphs))
        node.update(text=fragments[0][0], orig=fragments[0][0], prov=provenance(page, fragments[0][1], fragments[0][0]))
        added = [add_text(v, 'footnote', page, b, node['self_ref']) for v, b, _ in fragments[1:]]
        children = adapted.get('body', {}).get('children', [])
        for child in added:
            children.remove({'$ref': child['self_ref']})
        at = next((i for i, c in enumerate(children) if c['$ref'] == node['self_ref']), -1)
        children[at + 1:at + 1] = [{'$ref': child['self_ref']} for child in added]
        record(node, before, 'source_note_units', {'page': page, 'fragments': added,
                                                  'glyph_indices': [c['index'] for c in selected]})

    # A caption and a multi-line heading may share an oversized native box.
    texts = list(adapted.get('texts', []))
    for a, b in zip(texts, texts[1:]):
        if a.get('label') != 'section_header' or b.get('label') != 'section_header':
            continue
        pa, pb = a.get('prov', []), b.get('prov', [])
        if len(pa) != 1 or len(pb) != 1 or pa[0]['page_no'] != pb[0]['page_no']:
            continue
        page = pa[0]['page_no']; height = pages[str(page)]['size']['height']
        ba, bb = bounds(pa[0], height), bounds(pb[0], height)
        if max(ba[1], bb[1]) >= min(ba[3], bb[3]):
            continue
        selected = inside(chars(page), [min(ba[0], bb[0]), min(ba[1], bb[1]), max(ba[2], bb[2]), max(ba[3], bb[3])])
        if Counter(key(a['text'] + b['text'])) != Counter(key(''.join(c['text'] for c in selected))):
            continue
        physical = lines(selected)
        captions = [g for g in physical if re.fullmatch(r'(?:Infographic|Infographie|Figure|Table|Tableau)\s+\d+', text(g), re.I)]
        if len(captions) != 1 or len(physical) < 3:
            continue
        caption = captions[0]; heading = [g for g in physical if g is not caption]
        before_a, before_b = copy.deepcopy(a), copy.deepcopy(b)
        v = joined_lines(heading); a.update(text=v, orig=v, prov=provenance(page, union(sum(heading, [])), v))
        v = text(caption); b.update(label='caption', text=v, orig=v, prov=provenance(page, union(caption), v))
        b.pop('level', None)
        children = adapted.get('body', {}).get('children', [])
        ia = next((i for i, c in enumerate(children) if c['$ref'] == a['self_ref']), None)
        ib = next((i for i, c in enumerate(children) if c['$ref'] == b['self_ref']), None)
        if ia is not None and ib is not None:
            children.pop(ib); children.insert(ia, {'$ref': b['self_ref']})
        record(a, before_a, 'source_heading_lines', {'page': page, 'glyph_indices': [g['index'] for g in sum(heading, [])]})
        record(b, before_b, 'source_caption_line', {'page': page, 'glyph_indices': [g['index'] for g in caption]})

    # Separate mixed text/OCR provenance before classifying fragments. A real
    # native text span must not make an unrelated decorative span authoritative.
    for node in list(adapted.get('texts', [])):
        parts = node.get('prov', [])
        if (node.get('label') != 'text' or len(parts) < 2 or
                node.get('parent', {}).get('$ref') != '#/body' or
                len({p['page_no'] for p in parts}) != 1 or not all(p.get('bbox') for p in parts)):
            continue
        page = parts[0]['page_no']; height = pages[str(page)]['size']['height']
        fragments = []
        for part in parts:
            span = part.get('charspan', [])
            if len(span) != 2 or not 0 <= span[0] < span[1] <= len(node['text']):
                break
            value = node['text'][span[0]:span[1]].strip()
            box = bounds(part, height)
            selected = inside(chars(page), box)
            fragments.append((value, box, selected))
        if (len(fragments) != len(parts) or not any(not g and len(key(v)) <= 4 for v, _, g in fragments) or
                not any(g and key(v) == key(''.join(c['text'] for c in sorted(g, key=lambda c: c['index'])))
                        for v, _, g in fragments)):
            continue
        before = copy.deepcopy(node)
        value, box, _ = fragments[0]
        node.update(text=value, orig=value, prov=provenance(page, box, value))
        added = [add_text(v, 'text', page, b, node['self_ref']) for v, b, _ in fragments[1:]]
        children = adapted.get('body', {}).get('children', [])
        for child in added:
            children.remove({'$ref': child['self_ref']})
        at = next(i for i, c in enumerate(children) if c['$ref'] == node['self_ref'])
        children[at + 1:at + 1] = [{'$ref': child['self_ref']} for child in added]
        record(node, before, 'source_mixed_provenance', {'page': page, 'fragments': added})

    text_by_page = defaultdict(list)
    for node in adapted.get('texts', []):
        parts = node.get('prov', [])
        if len(parts) == 1 and parts[0].get('bbox'):
            text_by_page[parts[0]['page_no']].append(node)
    for page, nodes in text_by_page.items():
        height = pages[str(page)]['size']['height']
        ordered = sorted(nodes, key=lambda n: bounds(n['prov'][0], height)[1])
        for i, node in enumerate(ordered):
            marker = re.match(r'^\(\s*(\d{1,3})\s*\)\s+', node.get('text', ''))
            if not marker or node.get('label') != 'text':
                continue
            box = bounds(node['prov'][0], height)
            previous = ordered[i - 1] if i else None
            if (box[1] < height * .5 or previous is None or previous.get('label') != 'footnote' or
                    not 0 <= box[1] - bounds(previous['prov'][0], height)[3] <= 6):
                continue
            glyphs = inside(chars(page), box)
            source_order = sorted(glyphs, key=lambda c: c['index'])
            if key(node['text']) != key(''.join(c['text'] for c in source_order)):
                continue
            before = copy.deepcopy(node)
            node['label'] = 'footnote'
            # The marker's literal width establishes the hanging text margin.
            first_line = lines(glyphs)[0]
            start = len(key(marker[0]))
            source_line = sorted(first_line, key=lambda c: c['bbox'][0])
            content_left = source_line[start]['bbox'][0] if start < len(source_line) else None
            following = ordered[i + 1] if i + 1 < len(ordered) else None
            joined = None
            if following and following.get('label') == 'text' and content_left is not None:
                fb = bounds(following['prov'][0], height)
                fg = inside(chars(page), fb)
                if (0 <= fb[1] - box[3] <= 6 and abs(fb[0] - content_left) <= 2 and
                        not re.match(r'^\(\s*\d+\s*\)', following['text']) and
                        key(following['text']) == key(''.join(c['text'] for c in sorted(fg, key=lambda c: c['index'])))):
                    joined = copy.deepcopy(following)
                    value = node['text'] + '\n' + following['text']
                    node.update(text=value, orig=value, prov=provenance(page, union(glyphs + fg), value))
                    following['content_layer'] = 'furniture'
                    record(following, joined, 'source_note_continuation', {'page': page, 'target_ref': node['self_ref'],
                                                                         'glyph_indices': [c['index'] for c in fg]})
            record(node, before, 'source_note_role', {'page': page, 'marker': marker[1],
                                                     'content_left': content_left, 'continuation': joined})
    ocr_calls = defaultdict(int)
    for page, nodes in text_by_page.items():
        height = pages[str(page)]['size']['height']; width = pages[str(page)]['size']['width']
        for node in nodes:
            if node.get('content_layer') == 'furniture' or node.get('label') not in {'text', 'section_header', 'list_item'}:
                continue
            box = bounds(node['prov'][0], height)
            selected = inside(chars(page), box)
            before = copy.deepcopy(node)
            literal = ''.join(c['text'] for c in sorted(selected, key=lambda c: c['index']))
            # Visible repetitions are never removed. A duplicate must have no
            # rendering contribution and a complete longer same-page witness.
            if (node['label'] == 'text' and key(literal) == key(node['text']) and
                    15 <= len(key(literal)) <= 150 and selected and
                    all(c.get('font_weight', 0) >= 600 for c in selected) and any(
                    other is not node and len(key(other['text'])) > len(key(node['text'])) and
                    key(node['text']) in key(other['text']) for other in nodes)):
                if invisible_objects(path, page, selected):
                    node['content_layer'] = 'furniture'
                    record(node, before, 'occluded_duplicate', {'page': page, 'glyph_indices': [c['index'] for c in selected],
                                                               'render_scale': 2, 'changed_pixels': 0})
                    continue
            # A short unmatched suffix may be a real identifier outside an
            # underestimated native box. Glyph absence does not prove noise.
            if selected and key(node['text']) != key(literal) and key(literal) and len(key(literal)) >= 8:
                full = key(node['text'])
                if full.startswith(key(literal)) and len(full) - len(key(literal)) <= 4:
                    uncertainties.append({'code': 'source_text_edge_uncertain', 'ref': node['self_ref'],
                                          'page': page, 'bbox': box})
                    continue
            # Colored, full-width, sentence-free text above aligned body prose
            # is source section structure, rather than a diagram label.
            if node['label'] == 'text' and key(literal) == key(node['text']) and selected and not node['text'].endswith(('.', ';', ':', '!', '?')):
                colors = {tuple(c.get('color', [])) for c in selected}
                following = [n for n in nodes if n is not node and n.get('label') in {'text', 'list_item'} and
                             len(n['text'].split()) >= 12 and
                             0 <= bounds(n['prov'][0], height)[1] - box[3] <= 40 and
                             abs(bounds(n['prov'][0], height)[0] - box[0]) <= 3]
                if (len(colors) == 1 and next(iter(colors)) not in {(), (0, 0, 0, 255), (255, 255, 255, 255)} and
                        box[2] - box[0] > width * .6 and len(node['text'].split()) >= 8 and following):
                    body_chars = inside(chars(page), bounds(following[0]['prov'][0], height))
                    if body_chars and {tuple(c.get('color', [])) for c in body_chars} != colors:
                        node['label'] = 'section_header'; node['level'] = 1
                        record(node, before, 'source_colored_section', {'page': page, 'color': list(next(iter(colors))),
                               'following_ref': following[0]['self_ref'], 'glyph_indices': [c['index'] for c in selected]})
                        continue
            if selected or ocr.get('mode', 'off') == 'off':
                continue
            images = [b for b in image_boxes(page) if b[0] <= (box[0] + box[2]) / 2 <= b[2] and
                      b[1] <= (box[1] + box[3]) / 2 <= b[3]]
            # Outlined text can have no literal glyph layer. Digits and narrow
            # lettering require the same pixel verification as other OCR.
            sparse_raster = len(nodes) <= 12 and images and any((b[2] - b[0]) * (b[3] - b[1]) > width * height * .4 for b in images)
            small_graphic = (box[2] - box[0]) * (box[3] - box[1]) < width * height * .05
            if ocr_calls[page] >= 12 or not (sparse_raster or small_graphic or node['label'] == 'section_header'):
                continue
            crop = [max(0, box[0] - 3), max(0, box[1] - 3), min(width, box[2] + 3), min(height, box[3] + 3)]
            if images:
                image = min(images, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
                crop[0], crop[2] = max(0, min(crop[0], image[0])), min(width, max(crop[2], image[2]))
            verification = ocr_region(path, page, crop, source_hash, ocr); ocr_calls[page] += 1
            if verification is None:
                continue
            words = verification['words']
            if node['label'] != 'section_header' and len(node['text'].split()) <= 2 and not sparse_raster:
                words = [w for w in words if box[0] <= (w['bbox'][0] + w['bbox'][2]) / 2 <= box[2] and
                         box[1] <= (w['bbox'][1] + w['bbox'][3]) / 2 <= box[3]]
            reliable = words and all(w['confidence'] >= 85 for w in words)
            v = ' '.join(w['text'] for w in words)
            if (reliable and key(v) != key(node['text']) and any(c.isalpha() for c in v) and
                    (node['label'] == 'section_header' or sparse_raster)):
                node.update(text=v, orig=v, prov=provenance(page, union([{'bbox': w['bbox']} for w in words]), v))
                record(node, before, 'source_pixel_ocr', {'page': page, **verification})
                if images:
                    image = min(images, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
                    for picture in adapted.get('pictures', []):
                        parts = picture.get('prov', [])
                        if len(parts) != 1 or parts[0]['page_no'] != page:
                            continue
                        pb = bounds(parts[0], height)
                        if (image[0] <= pb[0] <= pb[2] <= image[2] and image[1] <= pb[1] <= pb[3] <= image[3] and
                                pb[2] - pb[0] >= (image[2] - image[0]) * .7 and
                                pb[3] - pb[1] >= (image[3] - image[1]) * .7 and box[3] <= pb[1]):
                            old_picture = copy.deepcopy(picture)
                            picture['prov'] = provenance(page, image, '')
                            record(picture, old_picture, 'source_image_extent', {'page': page, 'bbox': image,
                                                                                'title_ref': node['self_ref']})
            elif (not reliable and node['label'] != 'section_header' and key(v) != key(node['text']) and
                  (len(node['text'].split()) <= 1 or small_graphic and not images or
                   sparse_raster and node.get('parent', {}).get('$ref', '').startswith('#/pictures/') and
                   raster_display_title(node['text'], box, height, words))):
                uncertainties.append({'code': 'source_ocr_verification_uncertain', 'ref': node['self_ref'],
                                      'page': page, 'bbox': box, 'evidence': verification})
    result = (adapted, {'version': VERSION, 'source_sha256': source_hash,
                       'ocr': ocr, 'verification_limits': {'regions_per_page': 12, 'ocr_timeout_seconds': 30},
                       'repairs': repairs, 'uncertainties': uncertainties}) if repairs or uncertainties else (native, None)
    _ADAPTED_CACHE[cache_key] = copy.deepcopy(result)
    while len(_ADAPTED_CACHE) > 2:
        _ADAPTED_CACHE.popitem(last=False)
    return result
