"""Document hierarchy assembled from parser metadata and conservative source flow.

Docling supplies labels, reading order, groups and locations. Our parser derives
heading levels/paths; this module builds section, numbered-paragraph and local
list relationships. A parent is context, never permission to merge its subtree.
"""

import re
from collections import defaultdict

VERSION = 1
METADATA_FIELDS = ('element_id', 'parent_element_id', 'level', 'heading_path',
                   'evidence_role', 'docling_ref', 'docling_parent_ref',
                   'docling_group_depth')
MARKER = re.compile(r'^\s*(?:([-–—•▪*])|\(?([\w]+)[.)°])\s+')
NUMBERED = re.compile(r'^\s*\(?\d+[.)]\s+')
CASE_HEADING = re.compile(r'^\s*(?:cas|case)\s+n[°o]\s*\d+\b', re.I)
BOX_HEADING = re.compile(r'^\s*(?:encadré|box)\s+n[°o]\s*\d+\b', re.I)


def _page_topic_headings(flow, info):
    """Recover case-topic banners emitted late or given stale adapter levels.

    Require a labelled case heading and a spatially preceding root heading or
    large alphabetical banner on its own PDF page. Do not reorder body text,
    infer arbitrary heading sizes, or use diagram-contained headings.
    """
    pages, cases, roots = defaultdict(list), {}, {}
    for element in flow:
        details = info.get(element['id'], {})
        if (element['kind'] == 'heading' and element['location']['format'] == 'pdf'
                and element['location'].get('bbox')
                and details.get('evidence_role', 'authoritative') == 'authoritative'
                and not (details.get('docling_parent_ref') or '').startswith('#/pictures')):
            pages[(element['document_id'], element['location']['page'])].append(element)
    case_heights = defaultdict(list)
    for (document, _), headings in pages.items():
        case_heights[document].extend(e['location']['bbox'][3] - e['location']['bbox'][1]
                                     for e in headings if CASE_HEADING.match(e['text']))
    for (document, _), headings in pages.items():
        case_headings = [e for e in headings if CASE_HEADING.match(e['text'])]
        if not case_headings:
            continue
        small = min(case_heights[document])
        for case in case_headings:
            box = case['location']['bbox']
            candidates = [e for e in headings if e['id'] != case['id']
                and e['location']['bbox'][3] < box[1]
                and not CASE_HEADING.match(e['text'])
                and (info.get(e['id'], {}).get('level') == 1 or
                     (re.match(r'^\s*[A-ZÀ-Ý]\s+[A-ZÀ-Ý]', e['text']) and
                      e['location']['bbox'][3] - e['location']['bbox'][1] >= small * 1.5))]
            if candidates:
                topic = max(candidates, key=lambda e: (e['location']['bbox'][3], e['id']))
                cases[case['id']] = topic['id']
                roots[topic['id']] = min(roots.get(topic['id'], topic['ordinal']), case['ordinal'] - .5)
    return cases, roots


def _list_continuation(previous, current):
    if previous is None or previous['kind'] != 'list_item' or current['kind'] not in {'list_item', 'paragraph'}:
        return False
    left, right = previous['text'].rstrip(), current['text'].lstrip()
    a, b = previous['location'], current['location']
    if (not left or left.endswith(('.', ';', ':', '!', '?', '…')) or not right or not right[0].islower()
            or MARKER.match(right) or a['format'] != 'pdf' or b['format'] != 'pdf'
            or b['page'] not in {a['page'], a['page'] + 1} or not a.get('bbox') or not b.get('bbox')):
        return False
    ab, bb = a['bbox'], b['bbox']
    return (ab[0] - 2 <= bb[0] < bb[2] <= ab[2] + 2 and
            (b['page'] == a['page'] + 1 or
             0 <= bb[1] - ab[3] <= max(12, (bb[3] - bb[1]) * 1.5)))


def structure_snapshot(elements):
    return {'version': VERSION, 'elements': sorted(
        ({key: row[key] for key in METADATA_FIELDS if key in row} for row in elements),
        key=lambda row: row['element_id'])}


def validate_structure(bundle, snapshot):
    if not isinstance(snapshot, dict) or set(snapshot) != {'version', 'elements'} or snapshot['version'] != VERSION:
        raise ValueError('invalid document structure snapshot')
    elements = {e['id']: e for e in bundle['elements']}
    seen = set()
    for row in snapshot['elements']:
        eid = row['element_id']
        if eid not in elements or eid in seen or set(row) - set(METADATA_FIELDS):
            raise ValueError('document structure element membership mismatch')
        seen.add(eid)
        parent = row.get('parent_element_id')
        if parent is not None and (parent not in elements or
                elements[parent]['document_id'] != elements[eid]['document_id'] or
                elements[parent]['ordinal'] >= elements[eid]['ordinal']):
            raise ValueError('document structure parent must precede its child in the same document')


def _family(element):
    match = MARKER.match(element['text'])
    return ('bullet' if match[1] else 'number' if match[2].isdigit() else 'letter') if match else 'unmarked'


def assemble_document_hierarchy(bundle, snapshot=None):
    snapshot = snapshot if snapshot is not None else structure_snapshot([])
    validate_structure(bundle, snapshot)
    info = {row['element_id']: row for row in snapshot['elements']}
    elements = {e['id']: e for e in bundle['elements']}
    flow = sorted(elements.values(), key=lambda e: (e['document_id'], e['ordinal'], e['id']))
    cases, topic_positions = _page_topic_headings(flow, info)
    flow.sort(key=lambda e: (e['document_id'], topic_positions.get(e['id'], e['ordinal']), e['id']))
    result, stack, numbered, introductions = [], [], None, []
    document, last_body, active_case, boxed = None, None, None, None
    # Furniture, table cells and footnotes never drive body-flow inference.
    body = [e for e in flow if e['kind'] not in {'footnote', 'figure', 'table_cell', 'caption'}
            and info.get(e['id'], {}).get('evidence_role', 'authoritative') == 'authoritative']
    following = {e['id']: other for e, other in zip(body, body[1:])}
    for element in flow:
        eid, kind = element['id'], element['kind']
        details = info.get(eid, {})
        if element['document_id'] != document:
            document, stack, numbered, introductions, last_body = element['document_id'], [], None, [], None
            active_case, boxed = None, None
        if last_body is not None and kind not in {'footnote', 'table_cell'}:
            a, b = last_body['location'], element['location']
            if (a.get('article_id') != b.get('article_id') or
                    (a.get('page') is not None and b.get('page') is not None and
                     b['page'] > a['page'] + 1)):
                numbered, introductions = None, []
                boxed = None
        next_element = following.get(eid)
        local_intro = (not re.fullmatch(r'\s*(?:Whereas|Considérant(?: ce qui suit)?)\s*:', element['text'], re.I) and
                       kind in {'paragraph', 'list_item', 'heading'} and
                       element['text'].rstrip().endswith(':') and next_element is not None and
                       next_element['document_id'] == document and next_element['kind'] == 'list_item')
        parent, basis, role = None, 'root', 'content'
        native_parent = details.get('parent_element_id')
        if kind == 'table_cell':
            parent, basis = native_parent, 'parser_parent'
        elif kind == 'footnote' or details.get('evidence_role') in {'structural', 'auxiliary'}:
            role = 'detached'
            if kind == 'figure':
                parent, basis = active_case or boxed or (stack[-1][1] if stack else None), 'heading_flow'
        elif kind in {'paragraph', 'heading', 'table'} and BOX_HEADING.match(element['text']):
            # A captioned box is a sibling discussion under its section, not
            # continuation of the preceding numbered rule. Tables already own
            # their enclosed content and are not persistent box introductions.
            parent = active_case or (stack[-1][1] if stack else None)
            basis, role = 'boxed_caption', 'boxed_context'
            boxed, numbered, introductions = (None if kind == 'table' else eid), None, []
        elif kind == 'heading' and not (local_intro and numbered and not re.match(r'^\s*\d', element['text'])):
            level = details.get('level')
            if type(level) is not int or level < 1:
                match = re.match(r'^\s*(\d+(?:\.\d+)*)\.?\s+', element['text'])
                level = len(match[1].split('.')) if match else 1
            if eid in topic_positions:
                level, active_case = 1, None
            elif eid in cases:
                level = 2
            elif active_case and level > 1:
                level = max(3, level)
            else:
                active_case = None
            while stack and stack[-1][0] >= level:
                stack.pop()
            active_ids = {entry[1] for entry in stack}
            parent = (native_parent if native_parent in active_ids and not active_case
                      else (stack[-1][1] if stack else None))
            if eid in cases:
                parent = cases[eid]
                active_case = eid
            basis, role = ('parser_parent' if parent == native_parent and parent else 'heading_level'), 'section'
            if eid in topic_positions or eid in cases or (active_case and parent != native_parent):
                basis = 'page_heading_geometry'
            stack.append((level, eid))
            numbered, introductions, boxed = None, [], None
        else:
            section = stack[-1][1] if stack else None
            # Explicit parser hierarchy takes precedence over heading proximity.
            parent = section if active_case else native_parent or section
            basis = 'parser_parent' if parent == native_parent and native_parent else 'heading_flow'
            if boxed:
                parent, basis = boxed, 'boxed_scope'
            family = _family(element) if kind == 'list_item' else None
            # A new numbered paragraph ends the preceding rule's local scope.
            # Ordered alternatives under a colon lead remain part of that list.
            is_numbered = bool(NUMBERED.match(element['text'])) and kind in {'paragraph', 'list_item'}
            continuation = bool(introductions) and _list_continuation(last_body, element)
            if is_numbered and introductions:
                intro = elements[introductions[-1][0]]
                a, b = intro['location'].get('bbox'), element['location'].get('bbox')
                if (family != introductions[-1][1] or
                        (a and b and b[0] < a[0] - 2)):
                    introductions = []
            if is_numbered and not introductions:
                numbered, role = eid, 'numbered_paragraph'
                if boxed:
                    a, b = elements[boxed]['location'].get('bbox'), element['location'].get('bbox')
                    if not a or not b or b[0] < a[0] - 2:
                        parent, basis, boxed = section, 'heading_flow', None
            elif continuation:
                parent, basis = introductions[-1][0], 'local_list_continuation'
            else:
                if numbered:
                    parent, basis = numbered, 'numbered_paragraph_scope'
                if family and introductions:
                    intro_id, expected_family, child_box = introductions[-1]
                    intro = elements[intro_id]
                    a, b = intro['location'], element['location']
                    # Hanging list labels can be left of a short textual lead.
                    # Compare against its numbered parent when available.
                    floor = elements[numbered]['location'].get('bbox') if numbered else a.get('bbox')
                    while introductions and b.get('bbox') and introductions[-1][2] and b['bbox'][0] < introductions[-1][2][0] - 2:
                        introductions.pop()
                    if introductions:
                        intro_id, expected_family, child_box = introductions[-1]
                    if (introductions and family == expected_family and b.get('article_id') == a.get('article_id') and
                            (not floor or not b.get('bbox') or b['bbox'][0] >= floor[0] - 2)):
                        parent, basis = intro_id, 'local_list_flow'
                    else:
                        introductions = []
                elif not local_intro:
                    introductions = []
            if local_intro:
                role = 'numbered_paragraph' if numbered == eid else 'list_introduction'
                # A sibling textual lead (bank / insurance) belongs to the
                # numbered rule, not to the preceding lead's entire list.
                introductions.append((eid, _family(next_element), next_element['location'].get('bbox')))
            if native_parent and elements[native_parent]['kind'] not in {'heading', 'footnote'} and not boxed:
                parent, basis = native_parent, 'parser_parent'
        result.append({'element_id': eid, 'parent_element_id': parent,
                       'role': role, 'basis': basis})
        if kind not in {'footnote', 'table_cell', 'figure', 'caption'} and role != 'detached':
            last_body = element
    return result


def ancestor_ids(element_id, hierarchy):
    result, current = [], hierarchy[element_id]['parent_element_id']
    while current is not None:
        if current in result or current == element_id:
            raise ValueError('cyclic document hierarchy')
        result.append(current)
        current = hierarchy[current]['parent_element_id']
    return list(reversed(result))
