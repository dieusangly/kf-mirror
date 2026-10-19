"""Document hierarchy assembled from parser metadata and conservative source flow.

Docling supplies labels, reading order, groups and locations. Our parser derives
heading levels/paths; this module builds section, numbered-paragraph and local
list relationships. A parent is context, never permission to merge its subtree.
"""

import re

VERSION = 1
METADATA_FIELDS = ('element_id', 'parent_element_id', 'level', 'heading_path',
                   'evidence_role', 'docling_ref', 'docling_parent_ref',
                   'docling_group_depth')
MARKER = re.compile(r'^\s*(?:([-–—•▪*])|([\w]+)[.)°])\s+')
NUMBERED = re.compile(r'^\s*\d+[.)]\s+')


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
    result, stack, numbered, introductions = [], [], None, []
    document, last_body = None, None
    # Furniture, table cells and footnotes never drive body-flow inference.
    body = [e for e in flow if e['kind'] not in {'footnote', 'figure', 'table_cell', 'caption'}
            and info.get(e['id'], {}).get('evidence_role', 'authoritative') == 'authoritative']
    following = {e['id']: other for e, other in zip(body, body[1:])}
    for element in flow:
        eid, kind = element['id'], element['kind']
        details = info.get(eid, {})
        if element['document_id'] != document:
            document, stack, numbered, introductions, last_body = element['document_id'], [], None, [], None
        if last_body is not None and kind not in {'footnote', 'table_cell'}:
            a, b = last_body['location'], element['location']
            if (a.get('article_id') != b.get('article_id') or
                    (a.get('page') is not None and b.get('page') is not None and
                     b['page'] > a['page'] + 1)):
                numbered, introductions = None, []
        next_element = following.get(eid)
        local_intro = (kind in {'paragraph', 'list_item', 'heading'} and
                       element['text'].rstrip().endswith(':') and next_element is not None and
                       next_element['document_id'] == document and next_element['kind'] == 'list_item')
        parent, basis, role = None, 'root', 'content'
        native_parent = details.get('parent_element_id')
        if kind == 'table_cell':
            parent, basis = native_parent, 'parser_parent'
        elif kind == 'footnote' or details.get('evidence_role') in {'structural', 'auxiliary'}:
            role = 'detached'
        elif kind == 'heading' and not (local_intro and numbered and not re.match(r'^\s*\d', element['text'])):
            level = details.get('level')
            if type(level) is not int or level < 1:
                match = re.match(r'^\s*(\d+(?:\.\d+)*)\.?\s+', element['text'])
                level = len(match[1].split('.')) if match else 1
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = native_parent if native_parent and elements[native_parent]['kind'] == 'heading' else (stack[-1][1] if stack else None)
            basis, role = ('parser_parent' if parent == native_parent and parent else 'heading_level'), 'section'
            stack.append((level, eid))
            numbered, introductions = None, []
        else:
            section = stack[-1][1] if stack else None
            # Explicit parser hierarchy takes precedence over heading proximity.
            parent, basis = native_parent or section, 'parser_parent' if native_parent else 'heading_flow'
            family = _family(element) if kind == 'list_item' else None
            # A new numbered paragraph ends the preceding rule's local scope.
            # Ordered alternatives under a colon lead remain part of that list.
            is_numbered = bool(NUMBERED.match(element['text'])) and kind in {'paragraph', 'list_item'}
            if is_numbered and introductions:
                intro = elements[introductions[-1][0]]
                a, b = intro['location'].get('bbox'), element['location'].get('bbox')
                if (family != introductions[-1][1] or
                        (a and b and b[0] < a[0] - 2)):
                    introductions = []
            if is_numbered and not introductions:
                numbered, role = eid, 'numbered_paragraph'
            else:
                if numbered:
                    parent, basis = numbered, 'numbered_paragraph_scope'
                if family and introductions:
                    intro_id, expected_family = introductions[-1]
                    intro = elements[intro_id]
                    a, b = intro['location'], element['location']
                    # Hanging list labels can be left of a short textual lead.
                    # Compare against its numbered parent when available.
                    floor = elements[numbered]['location'].get('bbox') if numbered else a.get('bbox')
                    if (family == expected_family and b.get('article_id') == a.get('article_id') and
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
                introductions = [(eid, _family(next_element))]
            if native_parent and elements[native_parent]['kind'] not in {'heading', 'footnote'}:
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
