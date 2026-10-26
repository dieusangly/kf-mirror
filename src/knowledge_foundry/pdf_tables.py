"""Source-geometry repairs for the Docling PDF adapter; no model inference.

Coordinates are PDF points in top-left origin. Native tables are never mutated.
A repair requires a closed rectangular partition and complete glyph conservation.
"""

from collections import Counter, defaultdict
import ctypes
import hashlib
import re
import unicodedata


TOLERANCE = 1.0


def text_key(value):
    value = unicodedata.normalize('NFKC', value).translate(str.maketrans(
        {'’': "'", '‘': "'", '‐': '-', '‑': '-', '\u00ad': ''}))
    # Docling normalises prose/bullet en dashes to ASCII hyphens. Retain the
    # literal source glyph in output; compare only these lexical contexts alike.
    value = re.sub(r'(?m)^(\s*)–(?=\s)|(?<=[^\W\d_])(\s+)–(?=\s)',
                   lambda m: (m[1] or m[2] or '') + '-', value)
    return ''.join(value.split())


def _native_partition_witness(native_cells, cells, selected):
    """Keep a source-verified native grid when incomplete paint merges its cells.

    Verify unique glyph ownership over the entire candidate table first. Separate
    columns require independent first-row leaf headers; separate number rows
    require an aligned nonnumeric anchor in another column. Neither native indices
    alone establish a logical boundary.
    """
    located = []
    for cell in native_cells:
        if not {'row', 'column', 'row_span', 'col_span'} <= cell.keys():
            return False
        box = cell.get('bbox')
        if box is None:
            if cell['text'].strip():
                return False
            continue
        box = box if isinstance(box, list) else [box.l, box.t, box.r, box.b]
        if box[1] >= box[3] or box[0] >= box[2]:
            return False
        located.append((cell, box, []))
    for char in selected:
        x, y = (char['bbox'][0] + char['bbox'][2]) / 2, (char['bbox'][1] + char['bbox'][3]) / 2
        owners = [entry for entry in located if entry[1][0] - .5 <= x <= entry[1][2] + .5 and
                  entry[1][1] - .5 <= y <= entry[1][3] + .5]
        if len(owners) != 1:
            return False
        owners[0][2].append(char)
    if any(text_key(cell['text']) != text_key(''.join(c['text'] for c in chars))
           for cell, _, chars in located):
        return False
    header_row = min((c['row'] for c, _, _ in located if c['header'] and c['text'].strip()), default=-1)
    leaf_columns = {c['column'] for c, _, _ in located if c['header'] and c['row'] == header_row and
                    c['col_span'] == 1 and c['text'].strip()}
    for repaired in cells:
        box = repaired['bbox']
        entries = [(c, b, chars) for c, b, chars in located if chars and
                   box[0] <= (b[0] + b[2]) / 2 <= box[2] and
                   box[1] <= (b[1] + b[3]) / 2 <= box[3]]
        columns = {c['column'] for c, _, _ in entries}
        if len(columns) > 1 and columns <= leaf_columns and all(c['col_span'] == 1 for c, _, _ in entries):
            return True
        if len({c['row'] for c, _, _ in entries}) < 2 or len(columns) != 1:
            continue
        if not all(re.fullmatch(r'\d+', c['text'].strip()) for c, _, _ in entries):
            continue  # Wrapped prose cannot establish independently numbered rows.
        witnessed = True
        for cell, b, chars in entries:
            center = sum((c['bbox'][1] + c['bbox'][3]) / 2 for c in chars) / len(chars)
            anchors = [(a, bounds) for a, bounds, glyphs in located if glyphs and
                       a['column'] != cell['column'] and a['row'] == cell['row'] and
                       a['row_span'] == 1 and re.search(r'[^\W\d_]', a['text']) and
                       bounds[1] - .5 <= center <= bounds[3] + .5]
            if cell['row_span'] != 1 or len(anchors) != 1:
                witnessed = False
                break
        if witnessed:
            return True
    return False


def pdf_page_geometry(path, page):
    """Read painted axis-aligned paths, including composed Form transforms."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    strokes, fills, unsupported, backgrounds = [], [], [], []
    with pdfium.PdfDocument(path) as pdf:
        pg = pdf[page - 1]
        try:
            if pg.get_rotation():
                return {'strokes': [], 'fills': [], 'unsupported': True}
            height = pg.get_height()
            for obj in pg.get_objects():
                if obj.type == raw.FPDF_PAGEOBJ_IMAGE:
                    l, b, r, t = obj.get_bounds()
                    unsupported.append([l, height - t, r, height - b])
                    continue
                if obj.type != raw.FPDF_PAGEOBJ_PATH:
                    continue
                fill_mode, stroke = ctypes.c_int(), ctypes.c_int()
                raw.FPDFPath_GetDrawMode(obj, fill_mode, stroke)
                rgba = [ctypes.c_uint() for _ in range(4)]
                raw.FPDFPageObj_GetFillColor(obj, *rgba)
                color = tuple(v.value for v in rgba)
                stroke_rgba = [ctypes.c_uint() for _ in range(4)]
                raw.FPDFPageObj_GetStrokeColor(obj, *stroke_rgba)
                visible_stroke = stroke.value and stroke_rgba[3].value > 0
                subpaths, points, curved = [], [], False
                for index in range(raw.FPDFPath_CountSegments(obj)):
                    segment = raw.FPDFPath_GetPathSegment(obj, index)
                    x, y = ctypes.c_float(), ctypes.c_float()
                    raw.FPDFPathSegment_GetPoint(segment, x, y)
                    point = (x.value, y.value)
                    owner = obj
                    while owner is not None:
                        point = owner.get_matrix().on_point(*point)
                        owner = owner.container
                    point = (point[0], height - point[1])
                    kind = raw.FPDFPathSegment_GetType(segment)
                    if kind == raw.FPDF_SEGMENT_MOVETO:
                        if points:
                            subpaths.append(points)
                        points = [point]
                    elif kind == raw.FPDF_SEGMENT_LINETO:
                        points.append(point)
                    else:
                        curved = True
                        points.append(point)
                    if raw.FPDFPathSegment_GetClose(segment) and points:
                        if points[-1] != points[0]:
                            points.append(points[0])
                        subpaths.append(points)
                        points = []
                if points:
                    subpaths.append(points)
                for points in subpaths:
                    xs, ys = {p[0] for p in points}, {p[1] for p in points}
                    # PDF fill operations close subpaths implicitly. An open
                    # four-corner rectangle is still a literal filled border;
                    # its stroke, however, must retain the open source path.
                    fill_points = points + [points[0]] if points[-1] != points[0] else points
                    rectangle = (len(xs) == len(ys) == 2 and len(fill_points) == 5 and
                                 len(set(fill_points[:-1])) == 4 and all(
                                     a[0] == b[0] or a[1] == b[1]
                                     for a, b in zip(fill_points, fill_points[1:])))
                    page_background = rectangle and not strokes and not fills and not unsupported
                    if curved or fill_mode.value and color[3] > 0 and (
                            not rectangle or color[3] != 255 and not page_background):
                        unsupported.append([min(xs), min(ys), max(xs), max(ys)])
                        continue
                    if visible_stroke:
                        for a, b in zip(points, points[1:]):
                            if abs(a[0] - b[0]) < .01 or abs(a[1] - b[1]) < .01:
                                strokes.append([*a, *b])
                    if fill_mode.value and color[3] > 0 and rectangle:
                        if color[3] != 255:
                            # Uniform first paint over the white page. It cannot
                            # occlude later rules or add an internal table edge.
                            color = tuple(round(v * color[3] / 255 + 255 - color[3])
                                          for v in color[:3]) + (255,)
                            backgrounds.append([min(xs), min(ys), max(xs), max(ys)])
                        fills.append({'bbox': [min(xs), min(ys), max(xs), max(ys)],
                                      'color': list(color)})
        finally:
            pg.close()
    return {'strokes': strokes, 'fills': fills, 'unsupported': False, 'unsupported_regions': unsupported,
            'initial_translucent_backgrounds': backgrounds}


def _cluster(values):
    groups = []
    for value in sorted(values):
        if (not groups or value - groups[-1][-1] > TOLERANCE or
                value - groups[-1][0] > 2 * TOLERANCE):
            groups.append([value])
        else:
            groups[-1].append(value)
    return [sum(g) / len(g) for g in groups]


def _coverage(lines, position, start, end):
    intervals = sorted((max(a, start), min(b, end)) for p, a, b in lines
                       if abs(p - position) <= TOLERANCE and a < end and b > start)
    covered, cursor = 0., start
    for a, b in intervals:
        if a > cursor + TOLERANCE:
            covered += max(0, b - a)
        else:
            covered += max(0, b - cursor)
        cursor = max(cursor, b)
    return min(end - start, covered)


def _merge_lines(lines):
    """Join contiguous painted pieces before testing their grid endpoints."""
    coordinates = _cluster([p for p, _, _ in lines])
    groups = defaultdict(list)
    for p, a, b in lines:
        coordinate = min(coordinates, key=lambda x: abs(x - p))
        groups[coordinate].append((a, b))
    merged = []
    for p, intervals in groups.items():
        runs = []
        for a, b in sorted(intervals):
            if runs and a <= runs[-1][1] + TOLERANCE:
                runs[-1][1] = max(b, runs[-1][1])
            else:
                runs.append([a, b])
        merged.extend((p, a, b) for a, b in runs)
    return merged


def table_rulings(geometry, box):
    """Use strokes and visible fill transitions; same-color edges are invisible."""
    l, t, r, b = box
    fills = [f for f in geometry['fills'] if f['bbox'][0] < r + 3 and f['bbox'][2] > l - 3
             and f['bbox'][1] < b + 3 and f['bbox'][3] > t - 3]
    horizontal, vertical = [], []

    def add(a, z):
        if abs(a[1] - z[1]) < .01:
            y, lo, hi = a[1], min(a[0], z[0]), max(a[0], z[0])
            if t - 3 <= y <= b + 3 and hi > l and lo < r:
                horizontal.append((y, max(l - 3, lo), min(r + 3, hi)))
        elif abs(a[0] - z[0]) < .01:
            x, lo, hi = a[0], min(a[1], z[1]), max(a[1], z[1])
            if l - 3 <= x <= r + 3 and hi > t and lo < b:
                vertical.append((x, max(t - 3, lo), min(b + 3, hi)))

    for segment in geometry['strokes']:
        add(segment[:2], segment[2:])

    # Eliminate redundant same-colour rectangles before the visibility sweep.
    painted = []
    for fill in fills:
        a = fill['bbox']
        if a[2] - a[0] <= 1.5 and a[3] - a[1] > 2:
            add(((a[0] + a[2]) / 2, a[1]), ((a[0] + a[2]) / 2, a[3]))
            continue
        if a[3] - a[1] <= 1.5 and a[2] - a[0] > 2:
            add((a[0], (a[1] + a[3]) / 2), (a[2], (a[1] + a[3]) / 2))
            continue
        if min(a[2] - a[0], a[3] - a[1]) <= 1.5:
            continue
        covering = next((i for i in range(len(painted) - 1, -1, -1)
                         if painted[i]['bbox'][0] <= a[0] and painted[i]['bbox'][1] <= a[1]
                         and painted[i]['bbox'][2] >= a[2] and painted[i]['bbox'][3] >= a[3]), None)
        if covering is not None and painted[covering]['color'] == fill['color'] and all(
                f['color'] == fill['color'] or f['bbox'][2] <= a[0] or f['bbox'][0] >= a[2]
                or f['bbox'][3] <= a[1] or f['bbox'][1] >= a[3] for f in painted[covering + 1:]):
            continue
        painted.append(fill)

    def color(x, y):
        return next((f['color'] for f in reversed(painted) if
                     f['bbox'][0] < x < f['bbox'][2] and f['bbox'][1] < y < f['bbox'][3]),
                    [255, 255, 255, 255])

    for fill in painted:
        left, top, right, bottom = fill['bbox']
        for axis, position, lo, hi in [(0, left, top, bottom), (0, right, top, bottom),
                                       (1, top, left, right), (1, bottom, left, right)]:
            cuts = sorted({lo, hi, *(f['bbox'][i] for f in painted for i in
                                    ((1, 3) if axis == 0 else (0, 2)) if lo < f['bbox'][i] < hi)})
            for start, end in zip(cuts, cuts[1:]):
                if end - start < .01:
                    continue
                mid = (start + end) / 2
                a = (position - .02, mid) if axis == 0 else (mid, position - .02)
                z = (position + .02, mid) if axis == 0 else (mid, position + .02)
                if color(*a) != color(*z):
                    add((position, start) if axis == 0 else (start, position),
                        (position, end) if axis == 0 else (end, position))
    return horizontal, vertical


def native_page_fragment(native, ref):
    """Identify an adjacent native frame continuing at the next page's top."""
    tables = native.get('tables', [])
    current = next((t for t in tables if t['self_ref'] == ref), None)
    def located(table):
        parts = table.get('prov', [])
        return (len(parts) == 1 and isinstance(parts[0].get('bbox'), dict) and
                {'l', 't', 'r', 'b'} <= parts[0]['bbox'].keys() and
                'height' in native.get('pages', {}).get(str(parts[0]['page_no']), {}).get('size', {}))

    if not current or not located(current):
        return False
    page = current['prov'][0]['page_no']

    def bounds(table):
        p = table['prov'][0]
        height = native['pages'][str(p['page_no'])]['size']['height']
        b = p['bbox']
        return ([b['l'], b['t'], b['r'], b['b']] if b['coord_origin'] == 'TOPLEFT'
                else [b['l'], height - b['t'], b['r'], height - b['b']]), height

    prior = [t for t in tables if located(t) and t['prov'][0]['page_no'] == page - 1]
    same = [t for t in tables if located(t) and t['prov'][0]['page_no'] == page]
    if not prior or min(same, key=lambda t: bounds(t)[0][1]) != current:
        return False
    previous = max(prior, key=lambda t: bounds(t)[0][3])
    a, prior_height = bounds(previous)
    b, height = bounds(current)
    return (b[1] <= height * .1 and a[3] >= prior_height * .8 and
            all(abs(a[i] - b[i]) <= 2 * TOLERANCE for i in [0, 2]))


def ruled_cells(native_cells, characters, geometry, box, *, normalize_text=lambda value: value,
                page_fragment=False):
    """Return (cells, evidence, uncertainty); abstain instead of inventing a grid."""
    horizontal, vertical = table_rulings(geometry, box)
    if not horizontal or not vertical:
        return None, None, None  # Borderless / OCR-only: retain the native adapter.
    horizontal, vertical = _merge_lines(horizontal), _merge_lines(vertical)
    if native_cells and all('column' in c and 'col_span' in c for c in native_cells) and max(
            c['column'] + c['col_span'] for c in native_cells) == 1 and len(
                _cluster([x for x, _, _ in vertical])) == 2:
        return None, None, None  # A native callout with only its two outside edges.
    # Filled header bands and zebra stripes have vertical paint edges only at
    # the table perimeter. They do not establish ruled columns. Tables drawn
    # with neither outside vertical edge likewise use an open native layout;
    # keep that adapter rather than mistaking its intentional style for damage.
    internal = [(p, a, b) for p, a, b in vertical if box[0] + 3 < p < box[2] - 3]
    explicit_vertical = [(s[0], min(s[1], s[3]), max(s[1], s[3])) for s in geometry['strokes']
                         if abs(s[0] - s[2]) < .01]
    explicit_vertical += [((f['bbox'][0] + f['bbox'][2]) / 2, f['bbox'][1], f['bbox'][3])
                          for f in geometry['fills'] if f['bbox'][2] - f['bbox'][0] <= 1.5]
    outside = [p for p, a, b in explicit_vertical if b - a > 3 and
               min(abs(p - box[0]), abs(p - box[2])) <= 3 and a < box[3] and b > box[1]]
    painted_edges = set()
    for edge in [box[0], box[2]]:
        candidates = [(p, a, b) for p, a, b in vertical if abs(p - edge) <= 3]
        if not candidates:
            continue
        x = min(candidates, key=lambda line: abs(line[0] - edge))[0]
        top, bottom = min(a for _, a, _ in candidates), max(b for _, _, b in candidates)
        if (abs(top - box[1]) <= 3 and abs(bottom - box[3]) <= 3 and
                _coverage(vertical, x, top, bottom) >= bottom - top - TOLERANCE):
            painted_edges.add(edge)
    if not outside:
        coordinates = _cluster([p for p, _, _ in vertical])
        grid_rules = [(y, l, r) for y, l, r in horizontal if r - l > 3 and
                      all(any(abs(x - edge) <= TOLERANCE for x in coordinates) for edge in [l, r])]
        # Preserve the existing closed-grid endpoint proof: an explicit header
        # edge plus row-rule endpoints can establish both unpainted perimeters.
        # A partially ruled open style lacks this proof on at least one side.
        anchored_edges = {x for x in [box[0], box[2]] if grid_rules and
            any(abs(p - x) <= 3 and b - a > 3 for p, a, b in vertical) and
            all(any(abs(q - y) <= TOLERANCE and min(abs(l - x), abs(r - x)) <= 3
                    for q, l, r in grid_rules) for y in _cluster([p for p, _, _ in grid_rules]))
        }
    else:
        anchored_edges = set()
    if not internal or not (outside or (painted_edges | anchored_edges) == {box[0], box[2]}):
        return None, None, None
    source_top = any(abs(y - box[1]) <= 3 and abs(l - box[0]) <= 3 and abs(r - box[2]) <= 3
                     for y, l, r in horizontal)
    if page_fragment and not source_top:
        # A source-proved adjacent native page fragment may start within a row.
        # No artificial horizontal closing line or source cell box is invented.
        return None, None, None
    # A disconnected painted box inside a cell (for example behind rotated
    # lettering) is not part of the table's ruling network. Keep the connected
    # source grid; a truncated rule touching that grid remains subject to the
    # strict partial-border checks below.
    parent = list(range(len(horizontal) + len(vertical)))

    def component(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (y, l, r) in enumerate(horizontal):
        for j, (x, t, b) in enumerate(vertical, len(horizontal)):
            if l - TOLERANCE <= x <= r + TOLERANCE and t - TOLERANCE <= y <= b + TOLERANCE:
                parent[component(i)] = component(j)
    perimeter = {component(i) for i, (y, l, r) in enumerate(horizontal)
                 if abs(l - box[0]) <= 3 and abs(r - box[2]) <= 3}
    if perimeter:
        count = len(horizontal)
        horizontal = [line for i, line in enumerate(horizontal) if component(i) in perimeter]
        vertical = [line for i, line in enumerate(vertical, count) if component(i) in perimeter]
    incomplete_backgrounds = [r for r in geometry.get('initial_translucent_backgrounds', []) if
                              not (r[0] <= box[0] and r[1] <= box[1] and
                                   r[2] >= box[2] and r[3] >= box[3])]
    if any(r[0] < box[2] and r[2] > box[0] and r[1] < box[3] and r[3] > box[1]
           for r in [*geometry.get('unsupported_regions', []), *incomplete_backgrounds]):
        return None, None, 'unsupported_overlapping_paint'
    xs = _cluster([p for p, _, _ in vertical])
    if any(b - a > 3 and (any(abs(a - x) <= TOLERANCE for x in xs) !=
                          any(abs(b - x) <= TOLERANCE for x in xs)) for _, a, b in horizontal):
        return None, None, 'partial_internal_border'
    # Underlined words are painted rules too. Table rules terminate on source
    # column boundaries; isolated in-cell decorations cannot introduce rows.
    horizontal = [(p, a, b) for p, a, b in horizontal if
                  any(abs(a - x) <= TOLERANCE for x in xs) and
                  any(abs(b - x) <= TOLERANCE for x in xs)]
    ys = _cluster([p for p, _, _ in horizontal])
    anchored_vertical = []
    for p, a, b in vertical:
        junctions = [y for y in ys if a - TOLERANCE <= y <= b + TOLERANCE and
                     any(abs(q - y) <= TOLERANCE and l - TOLERANCE <= p <= r + TOLERANCE
                         for q, l, r in horizontal)]
        if len(junctions) >= 2:
            # Painted borders may overhang into a merged section band. Retain
            # the part supported by actual grid junctions, not the overhang.
            anchored_vertical.append((p, junctions[0], junctions[-1]))
    vertical = anchored_vertical
    xs = _cluster([p for p, _, _ in vertical])
    horizontal = [(p, a, b) for p, a, b in horizontal if
                  any(abs(a - x) <= TOLERANCE for x in xs) and
                  any(abs(b - x) <= TOLERANCE for x in xs)]
    ys = _cluster([p for p, _, _ in horizontal])
    if len(xs) < 3 or len(ys) < 2 or len(xs) * len(ys) > 20000:
        return None, None, 'incomplete_or_excessive_grid'
    left, right, top, bottom = xs[0], xs[-1], ys[0], ys[-1]
    if (abs(left - box[0]) > 3 or abs(right - box[2]) > 3 or
            abs(top - box[1]) > 3 or abs(bottom - box[3]) > 3):
        return None, None, 'grid_does_not_cover_native_table'
    endpoint_edges = []
    for x in (left, right):
        # An unpainted outside edge still has source support when every row
        # rule terminates there and an explicit vertical header edge anchors it.
        if (len(ys) >= 3 and any(abs(p - x) <= TOLERANCE for p, _, _ in vertical) and
                all(any(abs(p - y) <= TOLERANCE and
                        min(abs(a - x), abs(b - x)) <= TOLERANCE
                        for p, a, b in horizontal) for y in ys)):
            if _coverage(vertical, x, top, bottom) < bottom - top - TOLERANCE:
                vertical.append((x, top, bottom))
                endpoint_edges.append(x)
    for lines, p, a, b in [(horizontal, top, left, right), (horizontal, bottom, left, right),
                            (vertical, left, top, bottom), (vertical, right, top, bottom)]:
        if _coverage(lines, p, a, b) < b - a - TOLERANCE:
            return None, None, 'open_outer_border'
    nr, nc = len(ys) - 1, len(xs) - 1
    parent = list(range(nr * nc))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(a, b):
        parent[root(a)] = root(b)

    for row in range(nr):
        for col in range(nc):
            for other, lines, position, start, end in (
                    [(row * nc + col + 1, vertical, xs[col + 1], ys[row], ys[row + 1])]
                    if col + 1 < nc else []) + (
                    [((row + 1) * nc + col, horizontal, ys[row + 1], xs[col], xs[col + 1])]
                    if row + 1 < nr else []):
                length = end - start
                covered = _coverage(lines, position, start, end)
                if covered < length - TOLERANCE:
                    if covered > TOLERANCE:
                        return None, None, 'partial_internal_border'
                    join(row * nc + col, other)
    groups = defaultdict(list)
    for row in range(nr):
        for col in range(nc):
            groups[root(row * nc + col)].append((row, col))
    cells = []
    for units in groups.values():
        rr, cc = [r for r, _ in units], [c for _, c in units]
        r0, r1, c0, c1 = min(rr), max(rr) + 1, min(cc), max(cc) + 1
        if len(units) != (r1 - r0) * (c1 - c0):
            return None, None, 'nonrectangular_cell'
        cells.append({'row': r0, 'column': c0, 'row_span': r1 - r0, 'col_span': c1 - c0,
                      'bbox': [xs[c0], ys[r0], xs[c1], ys[r1]], 'glyphs': []})
    # Candidate paint edges that merge away in every cell are not logical
    # columns/rows. Keeping them would turn a plain cell into a false colspan.
    used_x = sorted({edge for c in cells for edge in (c['column'], c['column'] + c['col_span'])})
    used_y = sorted({edge for c in cells for edge in (c['row'], c['row'] + c['row_span'])})
    x_index, y_index = {v: i for i, v in enumerate(used_x)}, {v: i for i, v in enumerate(used_y)}
    for cell in cells:
        column, row = cell['column'], cell['row']
        cell['col_span'] = x_index[column + cell['col_span']] - x_index[column]
        cell['row_span'] = y_index[row + cell['row_span']] - y_index[row]
        cell['column'], cell['row'] = x_index[column], y_index[row]
    xs, ys = [xs[i] for i in used_x], [ys[i] for i in used_y]
    selected = [c for c in characters if c['text'].strip() and
                left <= (c['bbox'][0] + c['bbox'][2]) / 2 <= right and
                top <= (c['bbox'][1] + c['bbox'][3]) / 2 <= bottom]
    if any(any(ch in c['text'] for ch in ['\ufffd', '\ufffe', '\uffff']) for c in selected):
        return None, None, 'unmapped_source_glyph'
    for char in selected:
        x, y = (char['bbox'][0] + char['bbox'][2]) / 2, (char['bbox'][1] + char['bbox'][3]) / 2
        owners = [c for c in cells if c['bbox'][0] < x < c['bbox'][2] and c['bbox'][1] < y < c['bbox'][3]]
        if len(owners) != 1:
            return None, None, 'glyph_on_cell_boundary'
        owners[0]['glyphs'].append(char)
    for cell in cells:
        glyphs = cell.pop('glyphs')
        indices = {c['index'] for c in glyphs}
        first, last = (min(indices), max(indices)) if indices else (0, -1)
        cell['text'] = normalize_text(''.join(
            c['text'] + ('\n' if c.get('layout_break_after') else '') for c in characters
            if c['index'] in indices or first <= c['index'] <= last and not c['text'].strip())).strip()
        # Header semantics stay native and require complete text conservation.
        cell['header'] = any(c['header'] and text_key(c['text']) == text_key(cell['text'])
                             for c in native_cells if c['text'].strip())
        cell['alignment_uncertain'] = False
    expected = Counter(''.join(text_key(c['text']) for c in native_cells))
    actual = Counter(''.join(text_key(c['text']) for c in cells))
    if not selected or expected != actual:
        return None, None, 'native_glyph_text_mismatch'
    if _native_partition_witness(native_cells, cells, selected):
        # Native extraction has complete source coverage and stronger partition
        # witnesses than this incomplete painted grid. Its explicit adapter and
        # native audit retain the original cells, spans and physical locations.
        return None, None, None
    for cell in cells:
        # A closed paint partition can still miss faint header separators.
        # Do not collapse distinct recognised column headers into one cell.
        header_columns = set()
        for native in native_cells:
            bounds = native.get('bbox')
            if not native['header'] or native['row'] != 0 or not native['text'].strip() or bounds is None:
                continue
            x, y = (bounds.l + bounds.r) / 2, (bounds.t + bounds.b) / 2
            if (cell['bbox'][0] < x < cell['bbox'][2] and cell['bbox'][1] < y < cell['bbox'][3] and
                    text_key(native['text']) in text_key(cell['text'])):
                header_columns.add(native['column'])
        if len(header_columns) > 1:
            return None, None, 'conflicting_header_partition'
    cells.sort(key=lambda c: (c['row'], c['column']))
    physical_columns, column_aliases = None, None
    native_columns = max((c.get('column', 0) + c.get('col_span', 1) for c in native_cells), default=0)
    if native_columns < len(xs) - 1:
        # A divider can move between rows without creating another semantic
        # column. Require exact, uniquely located native/source cell agreement
        # to identify those alternative boundaries. A real narrow cell may
        # never collapse to zero width.
        constraints = defaultdict(set)
        for cell in cells:
            if not cell['text']:
                continue
            matches = []
            for native in native_cells:
                b = native.get('bbox')
                if b is not None and text_key(native['text']) == text_key(cell['text']) and (
                        cell['bbox'][0] < (b.l + b.r) / 2 < cell['bbox'][2] and
                        cell['bbox'][1] < (b.t + b.b) / 2 < cell['bbox'][3]):
                    matches.append(native)
            if len(matches) != 1 or not {'column', 'col_span'} <= matches[0].keys():
                break
            n = matches[0]
            constraints[cell['column']].add(n['column'])
            constraints[cell['column'] + cell['col_span']].add(n['column'] + n['col_span'])
        else:
            if set(constraints) == set(range(len(xs))) and all(len(v) == 1 for v in constraints.values()):
                mapping = [next(iter(constraints[i])) for i in range(len(xs))]
                if (mapping == sorted(mapping) and set(mapping) == set(range(native_columns + 1)) and
                        all(mapping[c['column']] < mapping[c['column'] + c['col_span']] for c in cells)):
                    physical_columns, column_aliases = xs, mapping
                    xs = [max(x for x, col in zip(xs, mapping) if col == i) for i in range(native_columns + 1)]
                    for cell in cells:
                        start, end = cell['column'], cell['column'] + cell['col_span']
                        cell['column'], cell['col_span'] = mapping[start], mapping[end] - mapping[start]
    evidence = {'origin': 'pdf_rulings_and_glyphs', 'bbox': [left, top, right, bottom],
                'columns': xs, 'rows': ys,
                'endpoint_edges': endpoint_edges,
                'glyph_sha256': hashlib.sha256(
                    ''.join(c['text'] for c in selected).encode()).hexdigest()}
    if physical_columns is not None:
        evidence.update(physical_columns=physical_columns, column_aliases=column_aliases)
    return cells, evidence, None


def table_row_map(table, cells, *, update_text=False):
    """Map canonical row/cell text to exact spans of its authoritative table."""
    by_row = defaultdict(list)
    for cell in cells:
        detail = cell['details']
        for row in range(detail['row'], detail['row'] + detail['row_span']):
            by_row[row].append(cell)
    rows, offset, rendered = [], 0, []
    for row, members in sorted(by_row.items()):
        values = []
        for cell in sorted(members, key=lambda c: c['details']['column']):
            column, span = cell['details']['column'], cell['details']['col_span']
            values.extend([''] * max(0, column + span - len(values)))
            values[column] = cell['text']
        while values and not values[-1]:
            values.pop()
        if not any(values):
            continue
        text = ' | '.join(values)
        entry = {'row': row, 'span': {'start': offset, 'end': offset + len(text)}, 'cells': []}
        for cell in members:
            column = cell['details']['column']
            if cell['text'] and column < len(values):
                start = offset + sum(len(v) + 3 for v in values[:column])
                entry['cells'].append({'cell_key': cell['locator_key'], 'column': column,
                                       'span': {'start': start, 'end': start + len(cell['text'])}})
        rows.append(entry)
        rendered.append(text)
        offset += len(text) + 1
    aggregate = '\n'.join(rendered)
    if update_text:
        table['text'] = aggregate.strip()
    if aggregate.strip() != table['text']:
        raise ValueError('table aggregate does not match its source cells')
    trim = len(aggregate) - len(aggregate.lstrip())
    for row in rows:
        for ref in [row, *row['cells']]:
            ref['span'] = {'start': max(0, ref['span']['start'] - trim),
                           'end': ref['span']['end'] - trim}
    return rows


def continuation_grid(previous_columns, columns):
    """Match physical boundaries to a prior grid without inventing new ones."""
    if not previous_columns or not columns:
        return None
    mapping = []
    for x in columns:
        matches = [i for i, p in enumerate(previous_columns) if abs(x - p) <= 2 * TOLERANCE]
        if len(matches) != 1:
            return None
        mapping.append(matches[0])
    if (mapping != sorted(set(mapping)) or mapping[0] != 0 or
            mapping[-1] != len(previous_columns) - 1):
        return None
    logical = list(previous_columns)
    for physical, canonical in enumerate(mapping):
        logical[canonical] = columns[physical]
    return logical, mapping


def link_table_continuations(items, page_heights, warnings):
    """Link only uniquely supported adjacent-page table fragments, never copy text.

    Blank governing cells need a same-column lexical continuation witness.
    Compatible columns without that witness leave a blocking uncertainty.
    """
    cells = defaultdict(list)
    for item in items:
        if item['kind'] == 'table_cell':
            cells[item['parent_key']].append(item)
    all_tables = [(index, t) for index, t in enumerate(items) if t['kind'] == 'table']
    tables = [(index, t) for index, t in all_tables if t['details'].get('table_geometry')]
    headers, row_maps = {}, {}

    def interrupted(between, previous, table):
        for node in between:
            if node['kind'] not in {'heading', 'caption', 'table'}:
                continue
            box = node['location'].get('bbox')
            if not box:
                return True
            page = node['location']['page']
            if (page == previous['location']['page'] and box[1] >= previous['location']['bbox'][3] or
                    page == table['location']['page'] and box[3] <= table['location']['bbox'][1]):
                return True
        return False
    for index, table in tables:
        key = table['locator_key']
        rows = table_row_map(table, cells[key])
        row_maps[key] = rows
        table['details']['table_rows'] = [{'row': row['row'], 'span': row['span']} for row in rows]
        own_headers = []
        for row in rows:
            members = [c for c in cells[key] if c['text'] and c['details']['row'] <= row['row'] <
                       c['details']['row'] + c['details']['row_span']]
            # Native models sometimes mark the first data row as a header.
            # Counts/amounts are not column years, nor is a lone section label.
            numeric_data = any(re.match(r'\d', c['text']) and not re.fullmatch(
                r'(?:19|20)\d{2}(?:\s*\([a-z]\))?', c['text'], re.I) for c in members)
            covered = {col for c in members for col in range(c['details']['column'],
                       c['details']['column'] + c['details']['col_span'])}
            missing = set(range(len(table['details']['table_geometry']['columns']) - 1)) - covered
            ordered = sorted(members, key=lambda c: c['details']['column'])
            year_columns = (len(ordered) >= 3 and ordered[0]['details']['column'] == 0 and
                            all(c['details']['header'] and re.fullmatch(r'(?:19|20)\d{2}', c['text'])
                                for c in ordered[1:]) and
                            len({c['text'] for c in ordered[1:]}) == len(ordered) - 1)
            if (len(members) < 2 or numeric_data or missing - {0} or
                    not (all(c['details']['header'] for c in members) or year_columns)):
                break
            own_headers.append({'element_key': key, 'span': row['span']})
        headers[key] = own_headers
        table['details']['table_headers'] = list(own_headers)
        if not own_headers and len(table['details']['table_geometry']['columns']) == 3 and len(rows) >= 3:
            left = [next((c for c in cells[key] if c['details']['column'] == 0 and
                          c['details']['row'] <= r['row'] <
                          c['details']['row'] + c['details']['row_span']), None) for r in rows]
            right = [c for c in cells[key] if c['details']['column'] == 1 and c['text']]
            if (all(c and c['text'] for c in left) and
                    len({c['text'] for c in left}) == len(left) and right and
                    all(re.search(r'[^\W\d_]', c['text']) for c in right)):
                # Self-contained field/description rows govern their own values;
                # a missing matrix header must not be invented for this layout.
                table['details']['table_role'] = 'key_value'
    for (previous_index, previous), (index, table) in zip(tables, tables[1:]):
        key, prior_key = table['locator_key'], previous['locator_key']
        page, prior_page = table['location']['page'], previous['location']['page']
        a, b = previous['details']['table_geometry'], table['details']['table_geometry']
        if page != prior_page + 1:
            continue
        if a['bbox'][3] < page_heights[prior_page] * .5 or b['bbox'][1] > page_heights[page] * .2:
            continue
        between = [i for i in items if i['location'].get('page') in {prior_page, page}
                   and i['kind'] != 'table_cell']
        # Captions may be yielded late by Docling. Only a source-located new
        # heading/caption after the prior table can interrupt continuation.
        if interrupted(between, previous, table):
            continue
        if headers[key]:
            # A self-contained table cannot require a prior table's columns.
            continue
        if table['details'].get('table_role') == 'key_value':
            continue
        prior_columns = previous['details'].get('table_continuation', {}).get('evidence', {}).get(
            'columns', a['columns'])
        matched = continuation_grid(prior_columns, b['columns'])
        # A continuation may merge subdivisions only where every governing
        # source-header row already spans that boundary. Years alone cannot
        # license a changed label partition.
        supported = matched is not None
        if supported:
            logical_columns, mapping = matched
            removed = set(range(len(prior_columns))) - set(mapping)
            for header in headers[prior_key]:
                source_key = header['element_key']
                source_row = next(r['row'] for r in row_maps[source_key] if r['span'] == header['span'])
                for edge in removed:
                    if not any(c['text'] and c['details']['row'] <= source_row <
                               c['details']['row'] + c['details']['row_span'] and
                               c['details']['column'] < edge <
                               c['details']['column'] + c['details']['col_span'] for c in cells[source_key]):
                        supported = False
        if not supported:
            warnings.append({'code': 'table_continuation_uncertain', 'ref': table['details']['docling_ref'],
                             'page': page, 'reason': 'column_boundaries_changed'})
            continue
        if not headers[prior_key]:
            warnings.append({'code': 'table_continuation_uncertain', 'ref': table['details']['docling_ref'],
                             'page': page, 'reason': 'missing_governing_headers'})
            continue
        if mapping != list(range(len(mapping))):
            for cell in cells[key]:
                detail = cell['details']
                start, end = detail['column'], detail['column'] + detail['col_span']
                detail['column'], detail['col_span'] = mapping[start], mapping[end] - mapping[start]
            row_maps[key] = table_row_map(table, cells[key], update_text=True)
            table['details']['table_rows'] = [{'row': r['row'], 'span': r['span']} for r in row_maps[key]]
        inherited = [] if headers[key] else list(headers[prior_key])
        if inherited:
            headers[key] = inherited
        continuation = {'previous_table_key': prior_key, 'header_context': inherited, 'row_context': [],
                        'evidence': {'origin': 'adjacent_source_grids', 'previous_page': prior_page,
                                     'page': page, 'columns': logical_columns}}
        if mapping != list(range(len(mapping))):
            continuation['evidence']['column_mapping'] = mapping
        first = next((r for r in row_maps[key] if r['span'] not in
                      [s['span'] for s in table['details']['table_headers']]), None)
        last = row_maps[prior_key][-1] if row_maps[prior_key] else None
        if first and last:
            def covering(table_key, row):
                return {column: c for c in cells[table_key] for column in range(
                    c['details']['column'], c['details']['column'] + c['details']['col_span'])
                    if c['details']['row'] <= row < c['details']['row'] + c['details']['row_span']}
            before, after = covering(prior_key, last['row']), covering(key, first['row'])
            missing = [col for col, c in after.items() if not c['text'] and col in before and before[col]['text']]
            connectors = {'de', 'des', 'du', 'le', 'la', 'les', 'et', 'ou', 'en', 'sur',
                          'the', 'of', 'and', 'or', 'to', 'for', 'with'}
            joiners = {'sur', 'en', 'de', 'du', 'des', 'à', 'aux', 'au', 'in', 'on', 'from', 'to', 'with'}
            witness_columns = set()
            for col, c in after.items():
                prior_text = before[col]['text'].rstrip() if col in before else ''
                unclosed = prior_text.count('(') > prior_text.count(')')
                if (c['text'] and prior_text and
                        (c['text'][0].islower() and (prior_text.split()[-1].casefold() in connectors or
                         bool(missing) and c['text'].split()[0].casefold() in joiners and
                         not prior_text.endswith(('.', '?', '!', ';', ':'))) or
                         unclosed and (c['text'][0].islower() or c['text'][0] in '0123456789('))):
                    witness_columns.add(col)
            governing = {col for col in missing if before[col]['details']['row_span'] > 1 and
                         after[col]['details']['row_span'] > 1 and
                         abs(before[col]['location']['bbox'][3] - a['bbox'][3]) <= TOLERANCE and
                         abs(after[col]['location']['bbox'][1] - b['bbox'][1]) <= TOLERANCE}
            for ref in last['cells']:
                col = ref['column']
                if col in governing:
                    cell = after[col]['details']
                    continuation['row_context'].append({'element_key': prior_key, **ref,
                        'applies_to_rows': list(range(cell['row'], cell['row'] + cell['row_span'])),
                        'origin': 'continued_governing_row_span'})
            if witness_columns:
                for ref in last['cells']:
                    col = ref['column']
                    c = after.get(col)
                    if c is not None and col not in governing and (col in missing or col in witness_columns):
                        continuation['row_context'].append({'element_key': prior_key, **ref,
                                                            'applies_to_row': first['row']})
            elif set(missing) - governing:
                warnings.append({'code': 'table_row_continuation_uncertain',
                                 'ref': table['details']['docling_ref'], 'page': page,
                                 'reason': 'blank_governing_cells_without_split_row_witness'})
        table['details']['table_continuation'] = continuation
        previous['details'].setdefault('table_continues_to_keys', []).append(key)
    for (previous_index, previous), (index, table) in zip(all_tables, all_tables[1:]):
        detail = table['details']
        page, prior_page = table['location']['page'], previous['location']['page']
        bounds = previous['location'].get('bbox')
        if (detail.get('table_geometry') and not detail.get('table_headers') and
                detail.get('table_role') != 'key_value' and
                not detail.get('table_continuation') and not previous['details'].get('table_geometry') and
                page == prior_page + 1 and bounds and bounds[3] >= page_heights[prior_page] * .5 and
                detail['table_geometry']['bbox'][1] <= page_heights[page] * .2 and
                not interrupted(items[previous_index + 1:index], previous, table)):
            warnings.append({'code': 'table_continuation_uncertain', 'ref': detail['docling_ref'],
                             'page': page, 'reason': 'previous_table_geometry_unavailable'})


def table_context_errors(elements, details):
    """Validate references without granting source fidelity or review approval."""
    ids = {e['id']: e for e in elements}
    errors = []
    for element in elements:
        detail = details.get(element['id'], {})
        continuation = detail.get('table_continuation')
        if not continuation:
            continue
        def fail(reason):
            errors.append({'table_id': element['id'], 'reason': reason})
        if (not isinstance(continuation, dict) or
                not {'previous_table_id', 'header_context', 'row_context', 'evidence'} <= continuation.keys() or
                not isinstance(continuation['header_context'], list) or
                not continuation['header_context'] or
                not isinstance(continuation['row_context'], list)):
            fail('invalid_context_metadata')
            continue
        previous = ids.get(continuation.get('previous_table_id'))
        if (element['kind'] != 'table' or not previous or previous['kind'] != 'table' or
                previous['ordinal'] >= element['ordinal'] or
                previous['location'].get('page', 0) + 1 != element['location'].get('page') or
                element['id'] not in details.get(previous['id'], {}).get('table_continues_to', [])):
            fail('invalid_or_nonreciprocal_previous_table')
            continue
        prior_grid = details[previous['id']].get('table_geometry') or {}
        grid = detail.get('table_geometry') or {}
        evidence = continuation['evidence']
        prior_columns = details[previous['id']].get('table_continuation', {}).get('evidence', {}).get(
            'columns', prior_grid.get('columns', [])) if isinstance(prior_grid, dict) else []
        matched = continuation_grid(prior_columns, grid.get('columns', [])) if isinstance(grid, dict) else None
        expected_evidence = None
        if matched:
            logical, mapping = matched
            expected_evidence = {'origin': 'adjacent_source_grids', 'previous_page': previous['location']['page'],
                                 'page': element['location']['page'], 'columns': logical}
            if mapping != list(range(len(mapping))):
                expected_evidence['column_mapping'] = mapping
        if not matched or evidence != expected_evidence:
            fail('incompatible_source_grids')
        for kind in ['header_context', 'row_context']:
            for ref in continuation.get(kind, []):
                if not isinstance(ref, dict):
                    fail('invalid_context_source_span')
                    continue
                source = ids.get(ref.get('element_id'))
                span = ref.get('span', {})
                if not isinstance(span, dict):
                    fail('invalid_context_source_span')
                    continue
                start, end = span.get('start'), span.get('end')
                if (not source or source['kind'] != 'table' or source['ordinal'] >= element['ordinal'] or
                        details.get(source['id'], {}).get('evidence_role') != 'authoritative' or
                        type(start) is not int or type(end) is not int or
                        not 0 <= start < end <= len(source['text'])):
                    fail('invalid_context_source_span')
                    continue
                if kind == 'header_context' and ref not in details[source['id']].get('table_headers', []):
                    fail('context_is_not_a_source_header')
                if kind == 'header_context' and matched:
                    header_rows = [r['row'] for r in details[source['id']].get('table_rows', [])
                                   if r['span'] == span]
                    removed = set(range(len(prior_columns))) - set(matched[1])
                    for edge in removed:
                        if len(header_rows) != 1 or not any(
                                c['kind'] == 'table_cell' and c['text'] and
                                details[c['id']].get('parent_element_id') == source['id'] and
                                details[c['id']]['row'] <= header_rows[0] <
                                details[c['id']]['row'] + details[c['id']]['row_span'] and
                                details[c['id']]['column'] < edge <
                                details[c['id']]['column'] + details[c['id']]['col_span'] for c in elements):
                            fail('coarsened_boundary_splits_source_header')
                if kind == 'row_context':
                    cell = ids.get(ref.get('cell_element_id'))
                    cell_detail = details.get(ref.get('cell_element_id'), {})
                    if (not cell or cell['kind'] != 'table_cell' or
                            cell_detail.get('parent_element_id') != source['id'] or
                            cell['text'] != source['text'][start:end] or
                            cell_detail.get('column') != ref.get('column') or
                            not set(ref.get('applies_to_rows', [ref.get('applies_to_row')])) <=
                            {r['row'] for r in detail.get('table_rows', [])}):
                        fail('invalid_governing_cell_context')
    return errors


def context_for_spans(detail, spans):
    """Return mandatory source context for the physical rows in these spans."""
    continuation = detail.get('table_continuation') or {}
    refs = list(continuation.get('header_context', []))
    for ref in continuation.get('row_context', []):
        rows = [r for r in detail.get('table_rows', []) if
                r['row'] in ref.get('applies_to_rows', [ref.get('applies_to_row')])]
        if any(start < row['span']['end'] and end > row['span']['start']
               for row in rows for start, end in spans):
            refs.append(ref)
    unique = {}
    for ref in refs:
        unique.setdefault((ref['element_id'], ref['span']['start'], ref['span']['end']), ref)
    return list(unique.values())
