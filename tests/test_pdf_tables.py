"""Offline source-geometry, continuation and consumer regressions; no pilot approval."""

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

from knowledge_foundry.pdf_tables import (
    context_for_spans, link_table_continuations, pdf_page_geometry, ruled_cells,
    table_context_errors, native_page_fragment, text_key, open_field_cells,
)
from knowledge_foundry.pdf_structure import pdf_page_characters, separate_table_note_markers
from knowledge_foundry.parsers import parse_pdf
from knowledge_foundry.parsing import ROOT, _normalize
from knowledge_foundry.chunking import _chunk_document
from knowledge_foundry.parsing_audit import audit_document
from knowledge_foundry.chunking_audit import _audit_document


def write_pdf(path, stream=None, resources=b'', font=b'Helvetica'):
    # A merged body label spans two physically ruled numeric rows.
    stream = stream if stream is not None else (b'0 0 0 RG 1 w 40 600 220 160 re S '
              b'150 600 m 150 760 l S 40 720 m 260 720 l S '
              b'150 660 m 260 660 l S '
              b'BT /F1 10 Tf 50 735 Td (Group) Tj ET '
              b'BT /F1 10 Tf 160 735 Td (Count) Tj ET '
              b'BT /F1 10 Tf 50 690 Td (North) Tj ET '
              b'BT /F1 10 Tf 160 690 Td (42) Tj ET '
              b'BT /F1 10 Tf 160 630 Td (73) Tj ET')
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] '
               b'/Resources << /Font << /F1 4 0 R >> ' + resources + b' >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /' + font + b' >>',
               b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    data, offsets = b'%PDF-1.4\n', [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(data))
        data += f'{index} 0 obj\n'.encode() + value + b'\nendobj\n'
    xref = len(data)
    data += b'xref\n0 6\n0000000000 65535 f \n'
    data += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    data += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    path.write_bytes(data)
    return stream


def box(values):
    obj = SimpleNamespace(l=values[0], t=values[1], r=values[2], b=values[3])
    obj.to_top_left_origin = lambda height: obj
    return obj


def source_fixture(folder, *, stream=None):
    source = folder / 'stations.pdf'
    write_pdf(source, stream)
    native_cells = []
    for row, col, text, bounds in [(0, 0, 'Group', [50, 55, 80, 65]),
                                   (0, 1, 'Count', [160, 55, 190, 65]),
                                   (1, 0, 'Nor', [50, 100, 66, 110]),
                                   (2, 0, 'th', [66, 100, 80, 110]),
                                   (1, 1, '42 73', [160, 100, 180, 170])]:
        native_cells.append({'text': text, 'start_row_offset_idx': row,
                             'start_col_offset_idx': col, 'row_span': 1, 'col_span': 1,
                             'column_header': row == 0, 'row_header': False,
                             'bbox': dict(zip(['l', 't', 'r', 'b'], bounds), coord_origin='TOPLEFT')})
    native = {'pages': {'1': {'size': {'width': 600, 'height': 800}}}, 'texts': [], 'pictures': [],
              'groups': [], 'tables': [{'self_ref': '#/tables/0', 'label': 'table',
              'prov': [{'page_no': 1, 'bbox': {'l': 40, 't': 40, 'r': 260, 'b': 200,
                                             'coord_origin': 'TOPLEFT'}}],
              'data': {'table_cells': native_cells}}]}
    node = SimpleNamespace(label='table', self_ref='#/tables/0', parent=SimpleNamespace(cref='#/body'),
        prov=[SimpleNamespace(page_no=1, bbox=box([40, 40, 260, 200]))],
        data=SimpleNamespace(table_cells=[SimpleNamespace(**{**c, 'bbox': box(
            [c['bbox'][k] for k in ['l', 't', 'r', 'b']])}) for c in native_cells]))
    doc = SimpleNamespace(pages={1: SimpleNamespace(size=SimpleNamespace(height=800, width=600))},
                          export_to_dict=lambda: copy.deepcopy(native),
                          iterate_items=lambda with_groups: iter([(node, 1)]))
    converter = Mock()
    converter.convert.return_value = SimpleNamespace(status='success', document=doc)
    items, _, warnings = parse_pdf(source, {}, {'ocr': {'mode': 'off'}}, converter)
    base = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
    bundle, sidecar = _normalize(base['sources'][0], base['documents'][0], items)
    (folder / 'docling.json').write_text(json.dumps(native))
    (folder / 'parse.json').write_text(json.dumps({'warnings': warnings}))
    return source, bundle, sidecar, native_cells, warnings


def continuation_fixture():
    items = []
    for page, rows, y in [(1, [('Station', 'Finding'), ('Station A', 'Analysis of')], [180, 220, 360]),
                           (2, [('', 'the readings.'), ('Station B', 'Complete.')], [40, 100, 160])]:
        key = f'table-{page}'
        table = {'kind': 'table', 'text': '\n'.join(' | '.join(row) for row in rows).strip(),
                 'locator_key': key, 'parent_key': None,
                 'location': {'format': 'pdf', 'page': page, 'label': key, 'bbox': [40, y[0], 260, y[-1]]},
                 'details': {'evidence_role': 'authoritative', 'docling_ref': f'#/tables/{page - 1}',
                             'heading_path': [], 'table_geometry': {'origin': 'pdf_rulings_and_glyphs',
                             'bbox': [40, y[0], 260, y[-1]], 'columns': [40, 150, 260], 'rows': y}}}
        items.append(table)
        for row, values in enumerate(rows):
            for col, text in enumerate(values):
                items.append({'kind': 'table_cell', 'text': text,
                    'locator_key': f'{key}:r{row}:c{col}', 'parent_key': key,
                    'location': {'format': 'pdf', 'page': page, 'label': f'cell {row} {col}',
                                 'bbox': [[40, 150][col], y[row], [150, 260][col], y[row + 1]]},
                    'details': {'row': row, 'column': col, 'row_span': 1, 'col_span': 1,
                                'header': page == 1 and row == 0, 'evidence_role': 'structural'}})
    warnings = []
    link_table_continuations(items, {1: 400, 2: 400}, warnings)
    return items, warnings


def coarsening_fixture(*, merged_header=True):
    items, _ = continuation_fixture()
    for item in items:
        for field in ['table_continuation', 'table_continues_to_keys']:
            item['details'].pop(field, None)
    before = next(i for i in items if i['locator_key'] == 'table-1')
    before['text'] = 'Station |  | Finding\nStation A | Urban | 42'
    before['details']['table_geometry']['columns'] = [40, 100, 150, 260]
    for cell in items:
        if cell['kind'] != 'table_cell' or cell['parent_key'] != 'table-1':
            continue
        d = cell['details']
        if d['column'] == 1:
            d['column'] = 2
        elif d['row'] == 0 and merged_header:
            d['col_span'] = 2
        else:
            cell['location']['bbox'][2] = 100
    finding = next(i for i in items if i['locator_key'] == 'table-1:r1:c1')
    finding['text'] = '42'
    subtype = copy.deepcopy(finding)
    subtype.update(text='Urban', locator_key='table-1:r1:subtype')
    subtype['details']['column'] = 1
    subtype['location']['bbox'][0:3:2] = [100, 150]
    items.insert(items.index(finding), subtype)
    if not merged_header:
        subheader = copy.deepcopy(subtype)
        subheader.update(text='Type', locator_key='table-1:r0:subtype')
        subheader['details'].update(row=0, header=True)
        subheader['location']['bbox'][1:4:2] = [180, 220]
        items.insert(items.index(subtype), subheader)
        before['text'] = before['text'].replace('Station |  |', 'Station | Type |')
    after = next(i for i in items if i['locator_key'] == 'table-2')
    after['text'] = 'Station B | 73\nStation C | 90'
    for row, values in enumerate([['Station B', '73'], ['Station C', '90']]):
        for col, value in enumerate(values):
            next(i for i in items if i['locator_key'] == f'table-2:r{row}:c{col}')['text'] = value
    return items


class PdfTableTests(unittest.TestCase):
    def test_isolated_header_governs_adjacent_same_page_table_and_is_not_stranded(self):
        original, _ = continuation_fixture()
        items = [i for i in original if not (i.get('parent_key') == 'table-1' and i['details']['row'] == 1)]
        first = next(i for i in items if i['locator_key'] == 'table-1')
        first['text'] = 'Station | Finding'
        first['location']['bbox'][3] = 220
        first['details']['table_geometry'].update(bbox=[40, 180, 260, 220], rows=[180, 220])
        for i in items:
            i['details'].pop('table_continuation', None)
            i['details'].pop('table_continues_to_keys', None)
            if i['location']['page'] == 2:
                i['location']['page'] = 1
                i['location']['bbox'][1] += 190; i['location']['bbox'][3] += 190
                if i['kind'] == 'table':
                    i['details']['table_geometry'].update(bbox=[40, 230, 260, 350], rows=[230, 290, 350])
            if i['locator_key'] == 'table-2:r0:c0':
                i['text'] = 'Station A'
        next(i for i in items if i['locator_key'] == 'table-2')['text'] = 'Station A | the readings.\nStation B | Complete.'
        warnings = []
        link_table_continuations(items, {1: 400}, warnings)
        self.assertEqual(warnings, [])
        base = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
        bundle, details = _normalize(base['sources'][0], base['documents'][0], items)
        chunks, _ = _chunk_document(bundle, {'elements': details}, 'build:test', {'max_tokens': 64})
        self.assertEqual(len(chunks), 1)
        self.assertIn('Station | Finding', chunks[0]['text'])
        self.assertIn('Station A', chunks[0]['text'])
        audit = _audit_document(bundle['documents'][0], bundle['sources'][0], chunks,
                                bundle['elements'], {'elements': details}, 64, 0)
        self.assertEqual(audit['findings'], [])
        # A separate caption in the gap must prevent inherited columns.
        for i in items:
            i['details'].pop('table_continuation', None)
            i['details'].pop('table_continues_to_keys', None)
        items.insert(0, {'kind': 'caption', 'text': 'Independent table', 'locator_key': 'caption',
                        'location': {'format': 'pdf', 'page': 1, 'bbox': [40, 222, 260, 228]},
                        'details': {'evidence_role': 'authoritative'}})
        link_table_continuations(items, {1: 400}, [])
        self.assertNotIn('table_continuation', next(i for i in items if i['locator_key'] == 'table-2')['details'])

    def test_open_field_proof_requires_painted_columns_and_unique_complete_glyphs(self):
        cells, chars = [], []
        for row in range(3):
            for col, x in enumerate([50, 170]):
                value = chr(65 + row * 2 + col)
                chars.append({'index': len(chars), 'text': value,
                              'bbox': [x, 50 + row * 40, x + 5, 60 + row * 40]})
                cells.append({'row': row, 'column': col, 'row_span': 1, 'col_span': 1,
                              'text': value, 'bbox': SimpleNamespace(l=x, t=50 + row * 40,
                                  r=x + 5, b=60 + row * 40, coord_origin='TOPLEFT')})
        geometry = {'strokes': [[x, 40, x, 160] for x in [40, 150, 260]],
                    'fills': [], 'unsupported_regions': []}
        box = [40, 40, 260, 160]
        proof = open_field_cells(cells, chars, geometry, box)
        self.assertEqual(proof['columns'], [40, 150, 260])
        self.assertEqual(proof['glyph_indices'], list(range(6)))
        incomplete = {**geometry, 'strokes': geometry['strokes'][:2]}
        self.assertIsNone(open_field_cells(cells, chars, incomplete, box))
        cells[0]['row_section'] = True
        self.assertIsNone(open_field_cells(cells, chars, geometry, box))
        geometry['fills'] = [{'bbox': [40, 45, 260, 65], 'color': [217, 217, 217, 255]}]
        self.assertEqual(open_field_cells(cells, chars, geometry, box)['section_rows'], [0])
        self.assertIsNone(open_field_cells(cells, chars + [chars[0]], geometry, box))

    def test_open_fields_inherit_source_section_rows_without_copying_cells(self):
        items, _ = continuation_fixture()
        for item in items:
            item['details'].pop('table_continuation', None)
            item['details'].pop('table_continues_to_keys', None)
            if item['kind'] == 'table':
                item['details']['table_geometry']['origin'] = 'native_fields_with_source_columns'
                item['details']['table_geometry']['section_rows'] = [0] if item['location']['page'] == 1 else []
            if item['kind'] == 'table_cell':
                item['details']['header'] = False
                item['details']['row_section'] = item['locator_key'] == 'table-1:r0:c0'
        before = next(i for i in items if i['locator_key'] == 'table-1')
        after = next(i for i in items if i['locator_key'] == 'table-2')
        original = [i['text'] for i in items]
        warnings = []
        link_table_continuations(items, {1: 400, 2: 400}, warnings)
        self.assertEqual([i['text'] for i in items], original)
        context = after['details']['table_continuation']['header_context']
        self.assertEqual(context, before['details']['table_section_headers'])
        self.assertEqual(before['details']['table_headers'], [])
        self.assertEqual(before['text'][context[0]['span']['start']:context[0]['span']['end']],
                         'Station | Finding')
        self.assertEqual(after['details']['table_role'], 'continued_fields')
        base = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
        bundle, sidecar = _normalize(base['sources'][0], base['documents'][0], items)
        self.assertEqual(table_context_errors(bundle['elements'], {d['element_id']: d for d in sidecar}), [])

    def test_source_verified_leaf_headers_prevent_paint_from_merging_native_columns(self):
        with TemporaryDirectory() as name:
            source = Path(name) / 'columns.pdf'
            write_pdf(source, b'40 600 220 160 re S 150 720 m 150 760 l S '
                b'40 720 m 260 720 l S BT /F1 10 Tf 50 735 Td (North) Tj ET '
                b'BT /F1 10 Tf 160 735 Td (South) Tj ET '
                b'BT /F1 10 Tf 50 690 Td (42) Tj ET BT /F1 10 Tf 160 690 Td (73) Tj ET')
            native = [{'row': row, 'column': column, 'row_span': 1, 'col_span': 1,
                       'header': row == 0, 'text': text, 'bbox': box(bounds)}
                      for row, column, text, bounds in [
                          (0, 0, 'North', [48, 55, 90, 70]), (0, 1, 'South', [158, 55, 200, 70]),
                          (1, 0, '42', [48, 100, 90, 115]), (1, 1, '73', [158, 100, 200, 115])]]
            chars, geometry = pdf_page_characters(source, 1), pdf_page_geometry(source, 1)
            self.assertEqual(ruled_cells(native, chars, geometry, [40, 40, 260, 200]), (None, None, None))
            # Without source-verified leaf headers, native columns alone cannot
            # defeat a physical merged cell.
            for cell in native:
                cell['header'] = False
            repaired, _, error = ruled_cells(native, chars, geometry, [40, 40, 260, 200])
            self.assertIsNone(error)
            self.assertEqual(len(repaired), 3)
            native[2]['text'] = '43'
            self.assertEqual(ruled_cells(native, chars, geometry, [40, 40, 260, 200])[2],
                             'native_glyph_text_mismatch')

    def test_source_aligned_row_anchors_preserve_distinct_native_number_cells(self):
        with TemporaryDirectory() as name:
            source = Path(name) / 'rows.pdf'
            write_pdf(source, b'40 600 220 160 re S 150 600 m 150 760 l S '
                b'150 680 m 260 680 l S BT /F1 10 Tf 50 720 Td (1) Tj ET '
                b'BT /F1 10 Tf 160 720 Td (North) Tj ET '
                b'BT /F1 10 Tf 50 640 Td (2) Tj ET BT /F1 10 Tf 160 640 Td (South) Tj ET')
            native = [{'row': row, 'column': column, 'row_span': 1, 'col_span': 1,
                       'header': False, 'text': text, 'bbox': box(bounds)}
                      for row, column, text, bounds in [
                          (0, 0, '1', [48, 70, 90, 85]), (0, 1, 'North', [158, 70, 200, 85]),
                          (1, 0, '2', [48, 150, 90, 165]), (1, 1, 'South', [158, 150, 200, 165])]]
            chars, geometry = pdf_page_characters(source, 1), pdf_page_geometry(source, 1)
            self.assertEqual(ruled_cells(native, chars, geometry, [40, 40, 260, 200]), (None, None, None))
            # Text in another physical row cannot establish the association.
            native[3]['row'] = 0
            repaired, _, error = ruled_cells(native, chars, geometry, [40, 40, 260, 200])
            self.assertIsNone(error)
            self.assertEqual(len(repaired), 3)

    def test_numeric_note_markers_require_source_geometry_and_a_unique_note(self):
        with TemporaryDirectory() as name:
            source = Path(name) / 'counts.pdf'
            write_pdf(source, b'BT /F1 10 Tf 60 700 Td (42) Tj ET '
                      b'BT /F1 6 Tf 72 704 Td (17) Tj ET '
                      b'BT /F1 8 Tf 40 80 Td (17 4 observations were retained.) Tj ET')
            chars = pdf_page_characters(source, 1)
            native = {'pages': {'1': {'size': {'height': 800}}}, 'texts': [{
                'self_ref': '#/texts/0', 'label': 'footnote',
                'text': '17 4 observations were retained.', 'prov': [{'page_no': 1,
                'bbox': {'l': 35, 't': 705, 'r': 300, 'b': 725, 'coord_origin': 'TOPLEFT'}}]}]}
            cells = [{'text': '4217', 'bbox': [40, 85, 100, 110]}]
            repaired = separate_table_note_markers(cells, chars, native, 1)
            self.assertEqual(repaired[0]['text'], '42 [17]')
            self.assertEqual(repaired[0]['footnote_references'][0]['note']['docling_ref'], '#/texts/0')
            self.assertEqual(cells[0]['text'], '4217')
            for variation in ['baseline', 'font_size', 'wrong_digit', 'no_note', 'other_page',
                              'duplicate_note', 'wrong_note_glyphs', 'missing_glyphs', 'missing_box']:
                with self.subTest(variation=variation):
                    altered_chars, altered_native, altered_cells = copy.deepcopy((chars, native, cells))
                    marker = [c for c in altered_chars if c['text'].strip()][:4][2:]
                    if variation == 'baseline':
                        for c in marker:
                            c['bbox'][1] += 4
                            c['bbox'][3] += 4
                    elif variation == 'font_size':
                        for c in marker:
                            c['bbox'][1] = c['bbox'][3] - 10
                    elif variation == 'wrong_digit':
                        altered_cells[0]['text'] = '4317'
                    elif variation == 'no_note':
                        altered_native['texts'] = []
                    elif variation == 'other_page':
                        altered_native['texts'][0]['prov'][0]['page_no'] = 2
                    elif variation == 'duplicate_note':
                        altered_native['texts'] *= 2
                    elif variation == 'wrong_note_glyphs':
                        altered_native['texts'][0]['text'] = '17 Unrelated note wording.'
                    elif variation == 'missing_glyphs':
                        altered_chars = []
                    elif variation == 'missing_box':
                        altered_cells[0]['bbox'] = None
                    unchanged = separate_table_note_markers(altered_cells, altered_chars, altered_native, 1)
                    self.assertEqual(unchanged, altered_cells)

    def test_numeric_note_markers_survive_both_adapters_and_audit_rejects_tampering(self):
        for ruled in [False, True]:
            with self.subTest(ruled=ruled), TemporaryDirectory() as name:
                folder = Path(name)
                source = folder / 'counts.pdf'
                frame = b'40 680 220 80 re S 150 680 m 150 760 l S ' if ruled else b''
                write_pdf(source, frame + b'BT /F1 10 Tf 50 700 Td (Items) Tj ET '
                    b'BT /F1 10 Tf 160 700 Td (42) Tj ET '
                    b'BT /F1 6 Tf 172 704 Td (17) Tj ET '
                    b'BT /F1 8 Tf 40 80 Td (17 4 observations were retained.) Tj ET')
                cells = [{'text': text, 'start_row_offset_idx': 0, 'start_col_offset_idx': col,
                          'row_span': 1, 'col_span': 1, 'column_header': False, 'row_header': False,
                          'bbox': dict(zip(['l', 't', 'r', 'b'], bounds), coord_origin='TOPLEFT')}
                         for col, text, bounds in [(0, 'Items', [45, 85, 140, 110]),
                                                   (1, '42 17', [155, 85, 200, 110])]]
                note = {'self_ref': '#/texts/0', 'label': 'footnote',
                        'text': '17 4 observations were retained.', 'prov': [{'page_no': 1,
                        'bbox': {'l': 35, 't': 705, 'r': 300, 'b': 725, 'coord_origin': 'TOPLEFT'}}]}
                native = {'pages': {'1': {'size': {'height': 800, 'width': 600}}},
                          'texts': [note], 'groups': [], 'pictures': [], 'tables': [{
                          'self_ref': '#/tables/0', 'label': 'table', 'data': {'table_cells': cells},
                          'prov': [{'page_no': 1, 'bbox': {'l': 40, 't': 40, 'r': 260, 'b': 120,
                                                         'coord_origin': 'TOPLEFT'}}]}]}
                table = SimpleNamespace(label='table', self_ref='#/tables/0',
                    parent=SimpleNamespace(cref='#/body'), prov=[SimpleNamespace(page_no=1,
                    bbox=box([40, 40, 260, 120]))], data=SimpleNamespace(table_cells=[
                    SimpleNamespace(**{**c, 'bbox': box([c['bbox'][k] for k in ['l', 't', 'r', 'b']])})
                    for c in cells]))
                note_node = SimpleNamespace(label='footnote', self_ref='#/texts/0', text=note['text'],
                    parent=SimpleNamespace(cref='#/body'), prov=[SimpleNamespace(page_no=1,
                    bbox=box([35, 705, 300, 725]))])
                doc = SimpleNamespace(pages={1: SimpleNamespace(size=SimpleNamespace(height=800, width=600))},
                    export_to_dict=lambda: copy.deepcopy(native),
                    iterate_items=lambda with_groups: iter([(table, 1), (note_node, 1)]))
                converter = Mock()
                converter.convert.return_value = SimpleNamespace(status='success', document=doc)
                items, _, warnings = parse_pdf(source, {}, {'ocr': {'mode': 'off'}}, converter)
                base = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
                bundle, sidecar = _normalize(base['sources'][0], base['documents'][0], items)
                elements = bundle['elements']
                count = next(e for e in elements if e['kind'] == 'table_cell' and '42' in e['text'])
                self.assertEqual(count['text'], '42 [17]')
                detail = next(d for d in sidecar if d['element_id'] == count['id'])
                self.assertEqual(detail['header_origin'], 'pdf_rulings_and_glyphs' if ruled else 'docling_table_structure')
                (folder / 'docling.json').write_text(json.dumps(native))
                (folder / 'parse.json').write_text(json.dumps({'warnings': warnings}))
                outcome = {'manifest_id': 'counts', 'status': 'parsed', 'warnings': warnings}
                checked = audit_document(outcome, elements, sidecar, folder, source_path=source)
                self.assertFalse([f for f in checked['findings'] if f['severity'] == 'error'], checked['findings'])
                detail['footnote_references'][0]['marker'] = '71'
                checked = audit_document(outcome, elements, sidecar, folder, source_path=source)
                self.assertTrue(any(f['code'] in {'TABLE_FOOTNOTE_MISMATCH', 'TABLE_NATIVE_MISMATCH'}
                                    for f in checked['findings']))
                detail.pop('footnote_references')
                count['text'] = '4217'
                checked = audit_document(outcome, elements, sidecar, folder, source_path=source)
                self.assertTrue(any(f['code'] in {'TABLE_GEOMETRY_MISMATCH', 'TABLE_NATIVE_MISMATCH'}
                                    for f in checked['findings']))

    def test_fill_implicitly_closes_a_rectangle_without_closing_its_stroke(self):
        with TemporaryDirectory() as name:
            source = Path(name) / 'open.pdf'
            write_pdf(source, b'40 600 m 260 600 l 260 760 l 40 760 l B')
            geometry = pdf_page_geometry(source, 1)
            self.assertEqual(geometry['unsupported_regions'], [])
            self.assertEqual(geometry['fills'][0]['bbox'], [40, 40, 260, 200])
            self.assertEqual(len(geometry['strokes']), 3)

    def test_uniform_initial_translucent_background_is_not_a_later_overlay(self):
        with TemporaryDirectory() as name:
            source = Path(name) / 'background.pdf'
            stream = write_pdf(source)
            background = b'q /BG gs 0.5 0.5 1 rg 0 0 600 800 re f Q '
            resources = b'/ExtGState << /BG << /Type /ExtGState /ca 0.35 >> >>'
            for first in [True, False]:
                write_pdf(source, background + stream if first else stream + background, resources)
                geometry = pdf_page_geometry(source, 1)
                cells, _, error = ruled_cells([{'text': 'Group Count North 42 73', 'header': False}],
                    pdf_page_characters(source, 1), geometry, [40, 40, 260, 200])
                if first:
                    self.assertIsNotNone(cells)
                    self.assertIsNone(error)
                else:
                    self.assertEqual(error, 'unsupported_overlapping_paint')
            partial = b'q /BG gs 0.5 0.5 1 rg 60 640 180 100 re f Q '
            write_pdf(source, partial + stream, resources)
            self.assertEqual(ruled_cells([{'text': 'Group Count North 42 73', 'header': False}],
                pdf_page_characters(source, 1), pdf_page_geometry(source, 1), [40, 40, 260, 200])[2],
                'unsupported_overlapping_paint')

    def test_disconnected_text_background_does_not_make_a_hole_in_a_grid_cell(self):
        with TemporaryDirectory() as name:
            source = Path(name) / 'inset.pdf'
            stream = write_pdf(source)
            write_pdf(source, b'q .8 .8 1 rg 40 600 220 160 re f 1 g 50 620 40 90 re f Q ' + stream)
            cells, _, error = ruled_cells([{'text': 'Group Count North 42 73', 'header': False}],
                pdf_page_characters(source, 1), pdf_page_geometry(source, 1), [40, 40, 260, 200])
            self.assertIsNone(error)
            self.assertEqual(next(c['row_span'] for c in cells if c['text'] == 'North'), 2)

    def test_painted_side_and_opposite_rule_endpoints_preserve_ruled_reconstruction(self):
        with TemporaryDirectory() as name:
            source = Path(name) / 'painted.pdf'
            stream = write_pdf(source).replace(b'40 600 220 160 re S',
                b'40 600 m 260 600 l S 40 760 m 260 760 l S')
            write_pdf(source, b'q .8 .8 1 rg 40 600 110 160 re f 40 720 220 40 re f Q ' + stream)
            cells, _, error = ruled_cells([{'text': 'Group Count North 42 73', 'header': False}],
                pdf_page_characters(source, 1), pdf_page_geometry(source, 1), [40, 40, 260, 200])
            self.assertIsNone(error)
            self.assertIsNotNone(cells)
            self.assertEqual(next(c['row_span'] for c in cells if c['text'] == 'North'), 2)

    def test_page_fragment_requires_native_adjacency_and_does_not_hide_a_painted_top(self):
        def table(ref, page, top, bottom):
            return {'self_ref': ref, 'prov': [{'page_no': page, 'bbox': {
                'l': 40, 't': top, 'r': 260, 'b': bottom, 'coord_origin': 'TOPLEFT'}}]}
        native = {'pages': {'1': {'size': {'height': 800}}, '2': {'size': {'height': 800}}},
                  'tables': [table('prior', 1, 400, 760), table('current', 2, 38, 202)]}
        self.assertTrue(native_page_fragment(native, 'current'))
        self.assertFalse(native_page_fragment(native, 'prior'))
        native['tables'][0]['prov'][0]['bbox']['b'] = 500
        self.assertFalse(native_page_fragment(native, 'current'))
        native['tables'][0]['prov'][0]['bbox'] = None
        self.assertFalse(native_page_fragment(native, 'current'))
        with TemporaryDirectory() as name:
            source = Path(name) / 'fragment.pdf'
            write_pdf(source)
            cells, _, error = ruled_cells([{'text': 'Group Count North 42 73', 'header': False}],
                pdf_page_characters(source, 1), pdf_page_geometry(source, 1), [38, 38, 262, 202],
                page_fragment=True)
            self.assertIsNotNone(cells)
            self.assertIsNone(error)

    def test_native_fallback_audit_still_rejects_changed_values_and_associations(self):
        with TemporaryDirectory() as name:
            folder = Path(name)
            stream = write_pdf(folder / 'template.pdf').replace(b'40 600 220 160 re S',
                b'40 600 m 260 600 l S 40 760 m 260 760 l S')
            source, bundle, sidecar, _, warnings = source_fixture(folder, stream=stream)
            self.assertEqual(warnings, [])
            self.assertFalse(any('table_geometry' in d for d in sidecar))
            report = audit_document({'manifest_id': 'native', 'status': 'parsed', 'warnings': []},
                                    bundle['elements'], sidecar, folder, source_path=source)
            self.assertFalse([f for f in report['findings'] if f['severity'] == 'error'], report['findings'])
            for case in ['value', 'column', 'bbox']:
                elements, info = copy.deepcopy(bundle['elements']), copy.deepcopy(sidecar)
                cell = next(c for c in elements if c['kind'] == 'table_cell' and c['text'] == '42 73')
                if case == 'value':
                    cell['text'] = '42 74'
                elif case == 'column':
                    next(d for d in info if d['element_id'] == cell['id'])['column'] = 0
                else:
                    cell['location']['bbox'][0] += 2
                report = audit_document({'manifest_id': 'native', 'status': 'parsed', 'warnings': []},
                                        elements, info, folder, source_path=source)
                self.assertIn('TABLE_NATIVE_MISMATCH', [f['code'] for f in report['findings']], case)

    def test_callout_and_open_table_styles_keep_native_adapter_but_broken_frame_blocks(self):
        native = [{'text': 'Label Description', 'header': False}]
        for geometry in [
                {'strokes': [[0, 0, 200, 0], [0, 100, 200, 100], [100, 0, 100, 100]], 'fills': []},
                {'strokes': [[0, 0, 200, 0], [0, 100, 200, 100], [100, 0, 100, 100],
                             [0, 0, 0, .5], [0, 99.5, 0, 100],
                             [200, 0, 200, .5], [200, 99.5, 200, 100]], 'fills': []},
                {'strokes': [[0, 0, 200, 0], [0, 100, 200, 100], [0, 0, 0, 100], [200, 0, 200, 100]], 'fills': []}]:
            self.assertEqual(ruled_cells(native, [], geometry, [0, 0, 200, 100]), (None, None, None))
        with TemporaryDirectory() as name:
            source = Path(name) / 'broken.pdf'
            write_pdf(source)
            geometry = pdf_page_geometry(source, 1)
            geometry['strokes'] = [s for s in geometry['strokes'] if not s[0] == s[2] == 40]
            self.assertIsNotNone(ruled_cells(native, pdf_page_characters(source, 1), geometry,
                                             [40, 40, 260, 200])[2])

    def test_prose_dash_equivalence_does_not_equate_numeric_ranges_or_changed_digits(self):
        self.assertEqual(text_key('– entry\nLabel – definition'), text_key('- entry Label - definition'))
        self.assertNotEqual(text_key('10 – 20'), text_key('10 - 20'))
        self.assertNotEqual(text_key('Value 42'), text_key('Value 43'))

    def test_staggered_dividers_need_unique_native_cell_witnesses(self):
        with TemporaryDirectory() as name:
            source = Path(name) / 'staggered.pdf'
            stream = write_pdf(source)
            stream = stream.replace(b'150 600 m 150 760 l S',
                b'150 720 m 150 760 l S 140 660 m 140 720 l S 150 600 m 150 660 l S')
            stream = stream.replace(b'150 660 m 260 660 l S', b'40 660 m 260 660 l S')
            stream += b' BT /F1 10 Tf 50 630 Td (South) Tj ET'
            write_pdf(source, stream)
            native = [{'text': text, 'row': row, 'column': col, 'row_span': 1, 'col_span': 1,
                       'header': row == 0, 'bbox': box([x, y, x + 25, y + 10])}
                      for row, col, text, x, y in [(0, 0, 'Group', 50, 55), (0, 1, 'Count', 160, 55),
                       (1, 0, 'North', 50, 100), (1, 1, '42', 160, 100),
                       (2, 0, 'South', 50, 160), (2, 1, '73', 160, 160)]]
            cells, evidence, error = ruled_cells(native, pdf_page_characters(source, 1),
                                                 pdf_page_geometry(source, 1), [40, 40, 260, 200])
            self.assertIsNone(error)
            self.assertEqual(evidence['column_aliases'], [0, 1, 1, 2])
            self.assertEqual(evidence['columns'], [40, 150, 260])
            self.assertEqual(next(c['bbox'][2] for c in cells if c['text'] == 'North'), 140)
            native[2]['text'] = 'Unverified'
            _, evidence, error = ruled_cells(native, pdf_page_characters(source, 1),
                                             pdf_page_geometry(source, 1), [40, 40, 260, 200])
            self.assertEqual(error, 'native_glyph_text_mismatch')
    def test_ruled_pdf_recovers_native_false_rows_without_model_or_native_mutation(self):
        with TemporaryDirectory() as name:
            source, bundle, sidecar, native_cells, warnings = source_fixture(Path(name))
            self.assertEqual(warnings, [])
            cells = [c for c in bundle['elements'] if c['kind'] == 'table_cell']
            info = {d['element_id']: d for d in sidecar}
            north = next(c for c in cells if c['text'] == 'North')
            self.assertEqual(info[north['id']]['row_span'], 2)
            self.assertEqual([(info[c['id']]['row'], info[c['id']]['column'], c['text'])
                              for c in cells if c['text'] in ['42', '73']], [(1, 1, '42'), (2, 1, '73')])
            self.assertIn('42 73', [c['text'] for c in native_cells])
            report = audit_document({'manifest_id': 'stations', 'status': 'parsed', 'warnings': []}, bundle['elements'],
                                    sidecar, Path(name), source_path=source)
            self.assertFalse([f for f in report['findings'] if f['severity'] == 'error'], report['findings'])

    def test_source_audit_rejects_changed_digits_cells_grid_and_missing_original(self):
        with TemporaryDirectory() as name:
            folder = Path(name)
            source, bundle, sidecar, _, _ = source_fixture(folder)
            for change in ['digits', 'bbox', 'grid', 'span', 'missing_source']:
                elements, details = copy.deepcopy(bundle['elements']), copy.deepcopy(sidecar)
                cell = next(e for e in elements if e['text'] == '42')
                if change == 'digits':
                    cell['text'] = '73'
                elif change == 'bbox':
                    cell['location']['bbox'][0] += 3
                elif change == 'span':
                    next(d for d in details if d['element_id'] == cell['id'])['row_span'] = 2
                elif change == 'grid':
                    next(d for d in details if 'table_geometry' in d)['table_geometry']['columns'][1] += 3
                report = audit_document({'manifest_id': 'stations', 'status': 'parsed', 'warnings': []}, elements, details, folder,
                                        source_path=None if change == 'missing_source' else source)
                self.assertTrue([f for f in report['findings'] if f['severity'] == 'error'], change)

    def test_grid_abstains_on_missing_glyphs_mismatching_text_and_open_borders(self):
        with TemporaryDirectory() as name:
            source = Path(name) / 'stations.pdf'
            write_pdf(source)
            geometry = pdf_page_geometry(source, 1)
            characters = pdf_page_characters(source, 1)
            native = [{'text': 'Group Count North 42 73', 'header': False}]
            for chars, evidence in [([], geometry), (characters, {'strokes': [], 'fills': []})]:
                result, _, _ = ruled_cells(native, chars, evidence, [40, 40, 260, 200])
                self.assertIsNone(result)
            wrong = [{'text': 'Group Count North 42 74', 'header': False}]
            self.assertEqual(ruled_cells(wrong, characters, geometry, [40, 40, 260, 200])[2],
                             'native_glyph_text_mismatch')
            opened = copy.deepcopy(geometry)
            opened['strokes'] = [s for s in opened['strokes'] if not (s[1] == s[3] == 40)]
            self.assertIsNone(ruled_cells(native, characters, opened, [40, 40, 260, 200])[0])
            partial = copy.deepcopy(geometry)
            partial['strokes'] = [s for s in partial['strokes'] if s[1] != 140]
            partial['strokes'].append([190, 140, 260, 140])
            self.assertEqual(ruled_cells(native, characters, partial, [40, 40, 260, 200])[2],
                             'partial_internal_border')
            unknown = copy.deepcopy(characters)
            next(c for c in unknown if c['text'] == '4')['text'] = '\ufffe'
            self.assertEqual(ruled_cells(native, unknown, geometry, [40, 40, 260, 200])[2],
                             'unmapped_source_glyph')

    def test_same_color_background_rectangles_cannot_split_a_source_cell(self):
        from knowledge_foundry.pdf_tables import table_rulings
        geometry = {'strokes': [], 'fills': [
            {'bbox': [0, 0, 200, 100], 'color': [30, 40, 50, 255]},
            {'bbox': [10, 10, 180, 20], 'color': [30, 40, 50, 255]}]}
        horizontal, vertical = table_rulings(geometry, [0, 0, 200, 100])
        self.assertEqual({p for p, _, _ in horizontal}, {0, 100})
        self.assertEqual({p for p, _, _ in vertical}, {0, 200})

    def test_incomplete_header_rules_cannot_merge_distinct_native_headers(self):
        with TemporaryDirectory() as name:
            folder = Path(name)
            source, _, _, native, _ = source_fixture(folder)
            geometry = pdf_page_geometry(source, 1)
            # Keep the body divider but remove its header portion.
            geometry['strokes'] = [s for s in geometry['strokes'] if s[0] != 150 or s[2] != 150]
            geometry['strokes'].append([150, 80, 150, 200])
            native = [{'text': c['text'], 'row': c['start_row_offset_idx'],
                       'column': c['start_col_offset_idx'], 'header': c['column_header'],
                       'bbox': box([c['bbox'][k] for k in ['l', 't', 'r', 'b']])} for c in native]
            self.assertEqual(ruled_cells(native, pdf_page_characters(source, 1), geometry,
                                         [40, 40, 260, 200])[2], 'conflicting_header_partition')

    def test_painted_border_overhang_keeps_its_source_supported_grid_segment(self):
        with TemporaryDirectory() as name:
            source = Path(name) / 'stations.pdf'
            write_pdf(source)
            geometry = pdf_page_geometry(source, 1)
            geometry['strokes'] = [s for s in geometry['strokes'] if s[0] != 150 or s[2] != 150]
            geometry['strokes'].append([150, 37, 150, 203])
            native = [{'text': 'Group Count North 42 73', 'header': False}]
            cells, _, error = ruled_cells(native, pdf_page_characters(source, 1), geometry,
                                          [40, 40, 260, 200])
            self.assertIsNone(error)
            self.assertEqual(next(c['row_span'] for c in cells if c['text'] == 'North'), 2)
            self.assertEqual([(c['column'], c['text']) for c in cells if c['text'] in ['42', '73']],
                             [(1, '42'), (1, '73')])

    def test_continuation_uses_source_spans_and_row_context_has_limited_scope(self):
        items, warnings = continuation_fixture()
        self.assertEqual(warnings, [])
        for item in items:
            item['details']['heading_path'] = ['Inspection survey']
        items.insert(0, {'kind': 'heading', 'text': 'Inspection survey', 'locator_key': 'survey',
            'parent_key': None, 'location': {'format': 'pdf', 'page': 1, 'label': 'survey', 'bbox': [40, 80, 260, 100]},
            'details': {'evidence_role': 'authoritative', 'heading_path': ['Inspection survey'], 'level': 1}})
        base = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
        bundle, sidecar = _normalize(base['sources'][0], base['documents'][0], items)
        info = {d['element_id']: d for d in sidecar}
        self.assertEqual(table_context_errors(bundle['elements'], info), [])
        continued = next(e for e in bundle['elements'] if e['kind'] == 'table' and e['location']['page'] == 2)
        detail = info[continued['id']]
        self.assertEqual(len(detail['table_continuation']['row_context']), 2)
        first, second = detail['table_rows']
        self.assertEqual(len(context_for_spans(detail, [(first['span']['start'], first['span']['end'])])), 3)
        self.assertEqual(len(context_for_spans(detail, [(second['span']['start'], second['span']['end'])])), 1)
        chunks, _ = _chunk_document(bundle, {'elements': sidecar}, 'build:test', {'max_tokens': 15})
        result = _audit_document(bundle['documents'][0], bundle['sources'][0], chunks, bundle['elements'],
                                 {'elements': sidecar}, 15, 3)
        self.assertFalse([f for f in result['findings'] if f['severity'] == 'error'], result['findings'])
        for chunk in chunks:
            if any(ref['element_id'] == continued['id'] for ref in chunk['evidence']):
                self.assertIn('Station | Finding', chunk['text'])
        broken = copy.deepcopy(chunks)
        target = next(c for c in broken if any(r['element_id'] == continued['id'] for r in c['evidence']))
        target['evidence'] = [r for r in target['evidence'] if r['element_id'] == continued['id']]
        result = _audit_document(bundle['documents'][0], bundle['sources'][0], broken, bundle['elements'],
                                 {'elements': sidecar}, 15, 3)
        self.assertIn('STRUCTURAL_CONTENT_MISSING', [f['code'] for f in result['findings']])
        complete, _ = _chunk_document(bundle, {'elements': sidecar}, 'build:test', {'max_tokens': 4})
        self.assertTrue(any('Station | Finding' in c['text'] for c in complete))

    def test_continuation_rejects_new_titles_columns_and_unwitnessed_blank_labels(self):
        original, _ = continuation_fixture()
        for case in ['title', 'columns', 'ambiguous', 'repeated_headers', 'missing_prior_grid']:
            items = copy.deepcopy(original)
            for i in items:
                for k in ['table_continuation', 'table_continues_to_keys']:
                    i['details'].pop(k, None)
            if case == 'title':
                index = next(n for n, i in enumerate(items) if i['locator_key'] == 'table-2')
                items.insert(index, {'kind': 'heading', 'text': 'Different survey', 'locator_key': 'title',
                    'location': {'format': 'pdf', 'page': 2, 'bbox': [40, 10, 260, 20]},
                    'details': {'evidence_role': 'authoritative'}})
            elif case == 'columns':
                next(i for i in items if i['locator_key'] == 'table-2')['details']['table_geometry']['columns'][1] = 170
            elif case == 'missing_prior_grid':
                next(i for i in items if i['locator_key'] == 'table-1')['details'].pop('table_geometry')
            elif case == 'repeated_headers':
                next(i for i in items if i['locator_key'] == 'table-2')['text'] = 'Station | Finding\nStation B | Complete.'
                for col, value in enumerate(['Station', 'Finding']):
                    cell = next(i for i in items if i['locator_key'] == f'table-2:r0:c{col}')
                    cell['text'] = value
                    cell['details']['header'] = True
            else:
                cell = next(i for i in items if i['locator_key'] == 'table-1:r1:c1')
                cell['text'] = 'Complete statement'
                next(i for i in items if i['locator_key'] == 'table-1')['text'] = 'Station | Finding\nStation A | Complete statement'
            warnings = []
            link_table_continuations(items, {1: 400, 2: 400}, warnings)
            continuation = next(i for i in items if i['locator_key'] == 'table-2')['details'].get('table_continuation')
            if case == 'ambiguous':
                self.assertEqual(continuation['row_context'], [])
                self.assertTrue(any(w['code'] == 'table_row_continuation_uncertain' for w in warnings))
            else:
                self.assertIsNone(continuation)
                if case == 'missing_prior_grid':
                    self.assertTrue(any(w.get('reason') == 'previous_table_geometry_unavailable' for w in warnings))

    def test_disjunction_is_a_same_column_split_row_witness(self):
        for ending, continuation in [('one or', 'several organisations.'), ('une ou', 'plusieurs organisations.')]:
            items, _ = continuation_fixture()
            before = next(i for i in items if i['locator_key'] == 'table-1')
            before['text'] = 'Station | Finding\nStation A | ' + ending
            next(i for i in items if i['locator_key'] == 'table-1:r1:c1')['text'] = ending
            next(i for i in items if i['locator_key'] == 'table-2:r0:c1')['text'] = continuation
            after = next(i for i in items if i['locator_key'] == 'table-2')
            after['text'] = '| ' + continuation + '\nStation B | Complete.'
            for item in items:
                item['details'].pop('table_continuation', None)
                item['details'].pop('table_continues_to_keys', None)
            warnings = []
            link_table_continuations(items, {1: 400, 2: 400}, warnings)
            self.assertEqual(warnings, [])
            self.assertEqual(len(after['details']['table_continuation']['row_context']), 2)

    def test_coarsened_label_columns_require_a_source_header_spanning_the_boundary(self):
        for merged_header in [True, False]:
            items = coarsening_fixture(merged_header=merged_header)
            original_boxes = {i['locator_key']: copy.deepcopy(i['location']['bbox']) for i in items}
            warnings = []
            link_table_continuations(items, {1: 400, 2: 400}, warnings)
            table = next(i for i in items if i['locator_key'] == 'table-2')
            if not merged_header:
                self.assertNotIn('table_continuation', table['details'])
                self.assertEqual(warnings[0]['reason'], 'column_boundaries_changed')
                continue
            self.assertEqual(warnings, [])
            self.assertEqual(table['details']['table_continuation']['evidence']['column_mapping'], [0, 2, 3])
            self.assertEqual(table['details']['table_geometry']['columns'], [40, 150, 260])
            self.assertEqual(table['text'], 'Station B |  | 73\nStation C |  | 90')
            for item in items:
                self.assertEqual(item['location']['bbox'], original_boxes[item['locator_key']])
            base = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
            bundle, sidecar = _normalize(base['sources'][0], base['documents'][0], items)
            self.assertEqual(table_context_errors(bundle['elements'], {d['element_id']: d for d in sidecar}), [])
            chunks, _ = _chunk_document(bundle, {'elements': sidecar}, 'build:test', {'max_tokens': 64})
            report = _audit_document(bundle['documents'][0], bundle['sources'][0], chunks,
                                     bundle['elements'], {'elements': sidecar}, 64, 3)
            self.assertFalse([f for f in report['findings'] if f['severity'] == 'error'], report['findings'])
            detail = next(d for d in sidecar if d.get('table_continuation'))
            detail['table_continuation']['evidence']['column_mapping'] = [0, 1, 3]
            self.assertTrue(table_context_errors(bundle['elements'], {d['element_id']: d for d in sidecar}))

    def test_continued_governing_span_applies_to_all_and_only_its_physical_rows(self):
        items, _ = continuation_fixture()
        for item in items:
            item['details'].pop('table_continuation', None)
            item['details'].pop('table_continues_to_keys', None)
        before = next(i for i in items if i['locator_key'] == 'table-1')
        before['text'] = 'Station | Finding\nAuthority | Record\nAuthority | Other record'
        before['details']['table_geometry']['rows'] = [180, 220, 290, 360]
        label = next(i for i in items if i['locator_key'] == 'table-1:r1:c0')
        label['text'] = 'Authority'
        label['details']['row_span'] = 2
        finding = next(i for i in items if i['locator_key'] == 'table-1:r1:c1')
        finding['text'] = 'Record'
        finding['location']['bbox'][3] = 290
        added = copy.deepcopy(finding)
        added.update(text='Other record', locator_key='table-1:r2:c1')
        added['details']['row'] = 2
        added['location']['bbox'][1:4:2] = [290, 360]
        items.insert(items.index(finding) + 1, added)
        after = next(i for i in items if i['locator_key'] == 'table-2')
        after['text'] = '| New record\n | Other record'
        items.remove(next(i for i in items if i['locator_key'] == 'table-2:r1:c0'))
        blank = next(i for i in items if i['locator_key'] == 'table-2:r0:c0')
        blank['details']['row_span'] = 2
        blank['location']['bbox'][3] = 160
        next(i for i in items if i['locator_key'] == 'table-2:r0:c1')['text'] = 'New record'
        next(i for i in items if i['locator_key'] == 'table-2:r1:c1')['text'] = 'Other record'
        warnings = []
        link_table_continuations(items, {1: 400, 2: 400}, warnings)
        self.assertEqual(warnings, [])
        refs = after['details']['table_continuation']['row_context']
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]['applies_to_rows'], [0, 1])
        self.assertEqual(refs[0]['origin'], 'continued_governing_row_span')

    def test_source_heading_after_table_is_not_a_barrier_when_native_order_is_wrong(self):
        items, _ = continuation_fixture()
        for item in items:
            item['details'].pop('table_continuation', None)
            item['details'].pop('table_continues_to_keys', None)
        index = next(n for n, i in enumerate(items) if i['locator_key'] == 'table-2')
        items.insert(index, {'kind': 'heading', 'text': 'Following section', 'locator_key': 'following',
            'location': {'format': 'pdf', 'page': 2, 'bbox': [40, 200, 260, 220]},
            'details': {'evidence_role': 'authoritative'}})
        warnings = []
        link_table_continuations(items, {1: 400, 2: 400}, warnings)
        self.assertEqual(warnings, [])
        self.assertIn('table_continuation', next(i for i in items if i['locator_key'] == 'table-2')['details'])

    def test_caption_before_new_table_blocks_even_if_yielded_before_the_previous_table(self):
        items, _ = continuation_fixture()
        for item in items:
            item['details'].pop('table_continuation', None)
            item['details'].pop('table_continues_to_keys', None)
        items.insert(0, {'kind': 'caption', 'text': 'Different measurement', 'locator_key': 'new-caption',
            'location': {'format': 'pdf', 'page': 2, 'bbox': [40, 10, 260, 25]},
            'details': {'evidence_role': 'authoritative'}})
        warnings = []
        link_table_continuations(items, {1: 400, 2: 400}, warnings)
        self.assertEqual(warnings, [])
        self.assertNotIn('table_continuation', next(i for i in items if i['locator_key'] == 'table-2')['details'])

    def test_unheaded_field_rows_govern_their_own_values_without_a_matrix_header(self):
        original, _ = continuation_fixture()
        items = []
        for page in [1, 2]:
            table = copy.deepcopy(next(i for i in original if i['locator_key'] == f'table-{page}'))
            table['details'].pop('table_continuation', None)
            table['details'].pop('table_continues_to_keys', None)
            y = [180, 220, 290, 360] if page == 1 else [40, 80, 120, 160]
            table['details']['table_geometry']['rows'] = y
            values = [('Name', 'Full name'), ('Street', 'Address line'), ('Country', 'Country name')]
            table['text'] = '\n'.join(' | '.join(row) for row in values)
            items.append(table)
            for row, pair in enumerate(values):
                for col, text in enumerate(pair):
                    cell = copy.deepcopy(next(i for i in original if i['locator_key'] == f'table-{page}:r0:c{col}'))
                    cell.update(text=text, locator_key=f'table-{page}:r{row}:c{col}')
                    cell['details'].update(row=row, header=False)
                    cell['location']['bbox'][1:4:2] = y[row:row + 2]
                    items.append(cell)
        warnings = []
        link_table_continuations(items, {1: 400, 2: 400}, warnings)
        self.assertEqual(warnings, [])
        self.assertTrue(all(i['details'].get('table_role') == 'key_value' and
                            'table_continuation' not in i['details'] for i in items if i['kind'] == 'table'))

    def test_native_year_headers_establish_their_unmarked_label_stub(self):
        items, _ = continuation_fixture()
        table = next(i for i in items if i['locator_key'] == 'table-1')
        table['details']['table_geometry']['columns'] = [40, 150, 205, 260]
        table['text'] = 'Station | 2022 | 2023\nStation A | 42 | 73'
        for item in items:
            item['details'].pop('table_continuation', None)
            item['details'].pop('table_continues_to_keys', None)
        next(i for i in items if i['locator_key'] == 'table-1:r0:c0')['details']['header'] = False
        for row, pair in enumerate([['2022', '2023'], ['42', '73']]):
            cell = next(i for i in items if i['locator_key'] == f'table-1:r{row}:c1')
            cell['text'] = pair[0]
            cell['location']['bbox'][2] = 205
            extra = copy.deepcopy(cell)
            extra.update(text=pair[1], locator_key=f'table-1:r{row}:c2')
            extra['details']['column'] = 2
            extra['location']['bbox'][0:3:2] = [205, 260]
            items.insert(items.index(cell) + 1, extra)
        # Only the first page is needed to establish its governing header.
        items = [i for i in items if i['location']['page'] == 1]
        link_table_continuations(items, {1: 400}, [])
        self.assertEqual(len(table['details']['table_headers']), 1)

    def test_invalid_continuation_graph_or_scope_cannot_be_consumed(self):
        items, _ = continuation_fixture()
        base = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
        bundle, original = _normalize(base['sources'][0], base['documents'][0], items)
        for case in ['reciprocal', 'span', 'scope', 'columns', 'cell', 'format']:
            sidecar = copy.deepcopy(original)
            info = {d['element_id']: d for d in sidecar}
            detail = next(d for d in sidecar if 'table_continuation' in d)
            ref = detail['table_continuation']['row_context'][0]
            if case == 'reciprocal':
                info[detail['table_continuation']['previous_table_id']]['table_continues_to'] = []
            elif case == 'span':
                ref['span']['end'] += 1
            elif case == 'scope':
                ref['applies_to_row'] = 99
            elif case == 'columns':
                detail['table_geometry']['columns'][1] += 10
            elif case == 'cell':
                ref['cell_element_id'] = detail['element_id']
            else:
                detail['table_continuation'] = 'unrecognised adapter'
            self.assertTrue(table_context_errors(bundle['elements'], info), case)
            with self.assertRaisesRegex(ValueError, 'invalid table continuation'):
                _chunk_document(bundle, {'elements': sidecar}, 'build:test', {'max_tokens': 128})
