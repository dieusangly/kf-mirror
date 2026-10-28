"""Explicit PDF response adapters; native responses remain available for review.

Supported export dialects: Unstructured element JSON, LlamaParse items/pages,
MinerU content_list, and Mistral OCR pages. SDK/network integrations implement
StructureProvider directly; this module never uploads documents.
"""
from pathlib import Path
from collections import Counter
import tempfile

from knowledge_foundry.structure_providers import ProviderResult


KINDS = {'Title': 'heading', 'Header': 'paragraph', 'NarrativeText': 'paragraph', 'Text': 'paragraph',
         'UncategorizedText': 'paragraph', 'Address': 'paragraph', 'EmailAddress': 'paragraph',
         'Formula': 'paragraph', 'CodeSnippet': 'paragraph', 'Footer': 'paragraph',
         'Footnote': 'footnote',
         'PageNumber': 'paragraph', 'PageBreak': 'paragraph',
         'ListItem': 'list_item', 'Table': 'table', 'Image': 'figure', 'FigureCaption': 'caption',
         'CompositeElement': 'paragraph', 'TableChunk': 'table',
         'text': 'paragraph', 'heading': 'heading', 'table': 'table', 'image': 'figure',
         'list': 'list_item', 'code': 'paragraph', 'equation': 'paragraph', 'caption': 'caption',
         'footnote': 'footnote', 'header': 'paragraph', 'footer': 'paragraph', 'page_number': 'paragraph'}


def adapt_export(name, response, artifact):
    if artifact['source']['format'] != 'pdf':
        raise ValueError('native export adapters currently require PDF originals; use a custom adapter for other formats')
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(artifact['artifact_file']) as pdf:
        sizes = {i + 1: (page.get_width(), page.get_height()) for i, page in enumerate(pdf)}
    items, warnings = [], []
    heading_keys, heading_texts = [], []

    def add(kind, text, page, raw, *, box=None, scale=None, parent=None, level=None, key=None):
        if page not in sizes:
            raise ValueError('provider page is outside the ingested PDF')
        if kind is None:
            raise ValueError('unknown provider element type; extend the adapter explicitly')
        key = str(key or f'{name}:p{page}:item-{len(items)}')
        location = {'format': 'pdf', 'page': page, 'label': key}
        if box:
            if scale:
                width, height = sizes[page]
                box = [box[0] * width / scale[0], box[1] * height / scale[1],
                       box[2] * width / scale[0], box[3] * height / scale[1]]
            location['bbox'] = box
        details = {'provider': name, 'provider_metadata': raw, 'heading_path': list(heading_texts),
                   'evidence_role': 'structural' if kind == 'figure' else 'authoritative'}
        if kind == 'heading':
            level = level or 1
            del heading_keys[level-1:]
            del heading_texts[level-1:]
            heading_texts.append(text)
            details.update(level=level, heading_path=list(heading_texts))
        if raw.get('type') in {'header', 'footer', 'page_number', 'Header', 'Footer', 'PageNumber', 'PageBreak'}:
            # Preserve page furniture without making it citable body evidence.
            details['evidence_role'] = 'structural'
        items.append({'kind': kind, 'text': text or '', 'locator_key': key, 'location': location,
                      'parent_key': parent or (heading_keys[-1] if heading_keys else None), 'details': details})
        if kind == 'heading':
            heading_keys.append(key)
        return key

    if name == 'unstructured':
        rows = response if isinstance(response, list) else response['elements']
        counts = Counter(str(row['element_id']) for row in rows if row.get('element_id'))
        preceding = set()
        for index, row in enumerate(rows):
            meta = row.get('metadata', {})
            coords = meta.get('coordinates') or {}
            points = coords.get('points')
            box = [min(p[0] for p in points), min(p[1] for p in points),
                   max(p[0] for p in points), max(p[1] for p in points)] if points else None
            scale = (coords['layout_width'], coords['layout_height']) if box else None
            native_key = str(row.get('element_id') or f'unstructured:{index}')
            # Some releases emit identical IDs for repeated text at distinct
            # locations. Disambiguate by native order; never discard a repeat.
            locator = f'unstructured:{index}:{native_key}' if counts[native_key] > 1 else native_key
            parent = str(meta['parent_id']) if meta.get('parent_id') else None
            # Native layout parents may be omitted or follow their children.
            # Keep that relationship in native metadata without inventing order.
            if parent and (parent not in preceding or counts[parent] > 1):
                warnings.append({'code': 'unstructured_unresolved_parent', 'page': meta['page_number'],
                                 'element_id': row.get('element_id'), 'parent_id': parent})
                parent = None
            key = add(KINDS.get(row['type']), row.get('text', ''), meta['page_number'], row,
                box=box, scale=scale, parent=parent,
                level=meta.get('category_depth', 0) + 1 if row['type'] == 'Title' else None,
                key=locator)
            if parent is None and meta.get('parent_id'):
                items[-1]['parent_key'] = None
            preceding.add(key)
    elif name == 'llamaparse':
        pages = response.get('items', {}).get('pages') or response.get('pages')
        if not pages:
            raise ValueError('LlamaParse export requires expanded items/pages')
        def walk(rows, page, parent=None):
            for row in rows:
                kind = KINDS.get(row['type'])
                boxes = row.get('bbox') or []
                box = None
                if boxes:
                    box = [min(b['x'] for b in boxes), min(b['y'] for b in boxes),
                           max(b['x'] + b['w'] for b in boxes), max(b['y'] + b['h'] for b in boxes)]
                text = row.get('value', row.get('md', ''))
                if row['type'] == 'table' and row.get('rows'):
                    text = '\n'.join(' | '.join(str(v) for v in r) for r in row['rows'])
                if row['type'] == 'image':
                    text = row.get('caption', '')
                if row['type'] == 'list':
                    # A list container carries hierarchy; children own text.
                    text = row.get('value', '')
                    kind = 'paragraph'
                key = add(kind, text, page, row, box=box, parent=parent, level=row.get('level'))
                if row['type'] == 'list':
                    walk(row.get('items', []), page, key)
                    for item in items:
                        if item['parent_key'] == key and item['kind'] == 'paragraph':
                            item['kind'] = 'list_item'
        for page in pages:
            if page.get('success') is False:
                raise ValueError('LlamaParse reported a failed page')
            walk(page['items'], page.get('page_number', page.get('page')))
    elif name == 'mineru':
        rows = response if isinstance(response, list) else response['content_list']
        for row in rows:
            page, kind = row['page_idx'] + 1, KINDS.get(row['type'])
            text = row.get('text', row.get('code_body', row.get('table_body', '')))
            if row.get('text_level'):
                kind = 'heading'
            if row['type'] == 'table':
                from lxml import html
                table = html.fromstring(row.get('table_body', '<table/>'))
                text = '\n'.join(' | '.join(' '.join(cell.itertext()) for cell in r.xpath('./th|./td'))
                                 for r in table.xpath('.//tr'))
            if row['type'] == 'list':
                text = '\n'.join(row['list_items'])
            add(kind, text, page, row, box=row.get('bbox'), scale=(1000, 1000), level=row.get('text_level'))
    elif name == 'mistral':
        from knowledge_foundry.parsers import parse_markdown
        for page in response['pages']:
            number = page['index'] + 1
            markdown = page['markdown']
            # Substitute separated tables before parsing so table identity and
            # text survive; preserve the original native page and block data.
            for table in page.get('tables', []):
                content = table['content']
                if table.get('format') == 'html' or content.lstrip().startswith('<table'):
                    from lxml import html
                    node = html.fromstring(content)
                    rows = [' | '.join(' '.join(c.itertext()) for c in r.xpath('./th|./td')) for r in node.xpath('.//tr')]
                    content = '| ' + rows[0] + ' |\n| ' + ' | '.join('---' for _ in rows[0].split('|')) + ' |\n' + '\n'.join('| ' + r + ' |' for r in rows[1:])
                markdown = markdown.replace('[' + table['id'] + '](' + table['id'] + ')', content)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'page.md'
                path.write_text(markdown)
                blocks, _, _ = parse_markdown(path, {}, {'tables': True})
            for block in blocks:
                if block['kind'] == 'table_cell':
                    # Cell structure is also retained on its table's raw metadata.
                    continue
                key = add(block['kind'], block['text'], number, {'native_page_index': page['index'], 'block': block['details'], 'confidence_scores': page.get('confidence_scores')},
                          level=block['details'].get('level'))
            # Images without pixel coordinates stay native metadata and require
            # source review rather than an invented PDF box.
    else:
        raise ValueError('unknown native provider export dialect: ' + name)
    # Some providers omit a referenced layout container. Preserve that provider
    # relationship in metadata, while avoiding a dangling canonical parent.
    keys = {i['locator_key'] for i in items}
    for item in items:
        if item['parent_key'] not in keys:
            item['parent_key'] = None
    if name == 'unstructured' and any(r['type'] in {'CompositeElement', 'TableChunk'} for r in rows):
        chunks = [{'text': i['text'], 'evidence': [{'locator_key': i['locator_key']}]}
                  for i in items if i['text'].strip() and i['details']['evidence_role'] == 'authoritative']
    else:
        chunks = None
    return ProviderResult(items, response, warnings, chunks,
                          {'parser': name, 'page_count': len(sizes), 'export_dialect': name})
