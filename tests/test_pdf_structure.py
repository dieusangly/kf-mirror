"""Document-independent layout regressions: notes, local lists and sparse tables."""

import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from knowledge_foundry.chunking import _chunk_document
from knowledge_foundry.parsers import _pdf_table_cells, _pdf_table_text, parse_pdf
from knowledge_foundry.pdf_structure import repair_footnotes, restore_numeric_identifiers
from knowledge_foundry.parsing import ROOT, _normalize


def block(kind, text, key, top, height=8, left=40, page=1):
    return {'kind': kind, 'text': text, 'locator_key': key, 'parent_key': None,
            'location': {'format': 'pdf', 'page': page, 'bbox': [left, top, 400, top + height]},
            'details': {'docling_ref': key, 'heading_path': ['Later section'],
                        'evidence_role': 'authoritative'}}


class PdfStructureTests(unittest.TestCase):
    def test_rotated_headers_wrap_across_native_cells_and_group_headers_cannot_capture_markers(self):
        def cell(row, column, text, left, right, top, bottom, header=False, span=1):
            return SimpleNamespace(start_row_offset_idx=row, start_col_offset_idx=column,
                text=text, row_span=1, col_span=span, column_header=header, row_header=False,
                bbox=SimpleNamespace(l=left, r=right, t=top, b=bottom))
        table = SimpleNamespace(data=SimpleNamespace(table_cells=[
            cell(0, 1, 'Internal benches', 100, 175, -20, -10, True),
            cell(0, 2, 'External benches', 182, 258, -20, -10, True, 2),
            cell(1, 0, 'Test', 0, 60, 190, 200, True),
            cell(1, 1, 'North bench South', 100, 138, 0, 200, True),
            cell(1, 2, 'bench West bench', 140, 188, 0, 200, True),
            cell(1, 3, 'External lab', 220, 228, 0, 200, True),
            cell(2, 0, 'Inspect mounts', 0, 60, 220, 232),
            cell(2, 2, 'x', 182, 186, 223, 228),
            cell(3, 0, 'Check enclosure', 0, 60, 240, 252),
            cell(3, 1, 'x', 102, 106, 243, 248),
            cell(3, 3, 'x', 222, 226, 243, 248),
        ]))
        before = copy.deepcopy(table)
        characters = []
        for text, x in [('North bench ', 100), ('South ', 130), ('bench ', 140),
                        ('West bench ', 180), ('External lab', 220)]:
            for offset, value in enumerate(text):
                characters.append({'index': len(characters), 'text': value,
                                   'bbox': [x, offset * 8, x + 8, offset * 8 + 8]})
        cells = _pdf_table_cells(table, characters)
        self.assertFalse(any(c['alignment_uncertain'] for c in cells))
        self.assertEqual([(c['column'], c['text']) for c in cells if c['row'] == 1],
                         [(0, 'Test'), (1, 'North bench'), (2, 'South bench'),
                          (3, 'West bench'), (4, 'External lab')])
        self.assertEqual([(c['column'], c['col_span']) for c in cells if c['row'] == 0],
                         [(1, 2), (3, 2)])
        self.assertEqual([(c['row'], c['column']) for c in cells if c['text'] == 'x'],
                         [(2, 3), (3, 1), (3, 4)])
        self.assertEqual(table, before)

    def test_recovered_notes_reclassify_roles_after_detaching_and_merging(self):
        auxiliary = block('footnote', '8 Keep the calibration certificate.', 'n8', 650)
        auxiliary['details']['evidence_role'] = 'auxiliary'
        auxiliary['parent_key'] = 'diagram'
        filename = block('footnote', '9 maintenance.html', 'n9', 670)
        filename['details'].update(evidence_role='structural', link_text_role='non_substantive_filename')
        continuation = block('paragraph', 'Record the inspection date.', 'cont', 680)
        repaired = repair_footnotes([auxiliary, filename, continuation], {1: 800})
        self.assertEqual(len(repaired), 2)
        self.assertTrue(all(item['details']['evidence_role'] == 'authoritative' for item in repaired))
        self.assertTrue(all(item['parent_key'] is None for item in repaired))
        self.assertNotIn('link_text_role', repaired[1]['details'])
        self.assertEqual(repaired[1]['details']['docling_refs'], ['n9', 'cont'])

    def test_separate_note_marker_joins_following_note_and_stops_prior_continuation(self):
        items = [block('paragraph', 'Use the maintenance guide 12 and the procedure 13.', 'body', 100),
                 block('footnote', '12 Original maintenance guide.', 'n12', 640),
                 block('paragraph', '13', 'marker13', 652, height=4),
                 block('footnote', 'https://example.org/equipment-procedure.html', 'url13', 663)]
        repaired = repair_footnotes(items, {1: 800})
        self.assertEqual(len(repaired), 3)
        self.assertEqual(repaired[1]['text'], '12 Original maintenance guide.')
        self.assertEqual(repaired[2]['text'], '13 https://example.org/equipment-procedure.html')
        self.assertEqual(repaired[2]['details']['docling_refs'], ['marker13', 'url13'])
        self.assertEqual(repaired[2]['details']['heading_path'], [])
        self.assertIsNone(repaired[2]['parent_key'])
        unreferenced = [block('footnote', '12 Keep this guide.', 'n12', 640),
                        block('paragraph', '13', 'marker13', 652, height=4),
                        block('footnote', 'https://example.org/other.html', 'url13', 663)]
        self.assertEqual(len(repair_footnotes(unreferenced, {1: 800})), 3)

    def test_numbered_note_and_continuation_recovered_without_native_note_labels(self):
        items = [block('paragraph', 'Inspect the sensor 14 and its mount 15.', 'body', 100, 20),
                 block('paragraph', '14 Replace a defective sensor;', 'n14', 600),
                 block('paragraph', 'keep its calibration record.', 'cont', 610),
                 block('paragraph', '15 Check the mount annually.', 'n15', 622)]
        repaired = repair_footnotes(items, {1: 800})
        self.assertEqual([b['kind'] for b in repaired], ['paragraph', 'footnote', 'footnote'])
        self.assertEqual(repaired[1]['text'], '14 Replace a defective sensor;\nkeep its calibration record.')
        self.assertEqual(repaired[1]['details']['docling_refs'], ['n14', 'cont'])
        self.assertEqual(repaired[1]['details']['heading_path'], [])

    def test_known_note_anchors_mislabeled_neighbour_but_year_remains_continuation(self):
        items = [block('paragraph', 'Use the procedure 4 and the test 5.', 'body', 100),
                 block('footnote', '4 Follow the published method.', 'n4', 650),
                 block('paragraph', '5 The test was revised in', 'n5', 660),
                 block('paragraph', '2024 after inspection.', 'year', 670)]
        repaired = repair_footnotes(items, {1: 800})
        self.assertEqual(len(repaired), 3)
        self.assertEqual(repaired[-1]['kind'], 'footnote')
        self.assertIn('2024 after inspection.', repaired[-1]['text'])

    def test_body_numbering_or_unreferenced_footer_does_not_become_a_note(self):
        items = [block('paragraph', 'Use version 14 and revision 15.', 'body', 100),
                 block('paragraph', '14. First numbered instruction.', 'step14', 600),
                 block('paragraph', '15. Second numbered instruction.', 'step15', 620),
                 block('paragraph', '16 Unreferenced footer.', 'footer', 650)]
        self.assertEqual(repair_footnotes(copy.deepcopy(items), {1: 800}), items)

    def test_note_continuation_stops_at_column_furniture_and_page(self):
        items = [block('paragraph', 'Apply test 9.', 'body', 100),
                 block('footnote', '9 Qualified only for this instrument.', 'note', 650),
                 block('paragraph', 'Other column content.', 'other', 660, left=250),
                 block('paragraph', 'Next page body.', 'next', 50, page=2)]
        self.assertEqual(len(repair_footnotes(items, {1: 800, 2: 800})), 4)

    def test_geometry_splits_foreign_headers_and_merged_markers_and_retains_row_spans(self):
        def cell(row, col, text, left, right, header=False, rows=1):
            return SimpleNamespace(start_row_offset_idx=row, start_col_offset_idx=col,
                text=text, row_span=rows, col_span=1, column_header=header, row_header=False,
                bbox=SimpleNamespace(l=left, r=right, t=0 if header else 110,
                                     b=100 if header else 118))
        table = SimpleNamespace(data=SimpleNamespace(table_cells=[
            cell(0, 0, 'Item', 0, 8, True), cell(0, 1, 'Lab A Lab B', 20, 48, True),
            cell(1, 0, 'Calibration', 0, 8, rows=2), cell(1, 1, 'x x', 20, 48),
            cell(2, 2, 'x', 40, 48)]))
        characters = []
        for text, x, y in [('Item', 0, 0), ('Lab A ', 20, 0), ('Lab B', 40, 0),
                           ('x ', 20, 110), ('x', 40, 110)]:
            for index, char in enumerate(text):
                characters.append({'text': char, 'index': len(characters),
                                   'bbox': [x, y + index * 9, x + 8, y + index * 9 + 8]})
        # Markers sit on the same horizontal row, unlike rotated header glyphs.
        for char in characters[-3:]:
            char['bbox'][1:4:2] = [110, 118]
        text = _pdf_table_text(table, None, characters)
        self.assertEqual(text, 'Item | Lab A | Lab B\nCalibration | x | x\nCalibration |  | x')
        self.assertFalse(any(c['alignment_uncertain'] for c in _pdf_table_cells(table, characters)))

    def test_uncertain_marker_alignment_is_visible_without_glyphs(self):
        table = SimpleNamespace(data=SimpleNamespace(table_cells=[
            SimpleNamespace(start_row_offset_idx=0, start_col_offset_idx=0, text='x x')]))
        self.assertTrue(_pdf_table_cells(table)[0]['alignment_uncertain'])
        self.assertEqual(_pdf_table_text(table, None), 'x x')

    def test_sparse_markers_follow_text_row_geometry_not_wrong_native_row_indices(self):
        def cell(row, col, text, left, top, right, bottom, header=False):
            return SimpleNamespace(start_row_offset_idx=row, start_col_offset_idx=col,
                text=text, row_span=1, col_span=1, column_header=header, row_header=False,
                bbox=SimpleNamespace(l=left, r=right, t=top, b=bottom))
        table = SimpleNamespace(data=SimpleNamespace(table_cells=[
            cell(0, 0, 'Action', 0, 0, 70, 10, True),
            cell(0, 1, 'North lab', 100, 0, 145, 10, True),
            cell(0, 2, 'South lab', 160, 0, 205, 10, True),
            cell(1, 0, 'Inspect', 0, 20, 70, 35),
            cell(2, 0, 'Replace', 0, 40, 70, 60),
            cell(1, 1, 'x', 110, 25, 115, 30),
            # Extractor attached the second-row marker to the first row.
            cell(1, 2, 'x', 170, 48, 175, 53)]))
        self.assertEqual(_pdf_table_text(table, None),
                         'Action | North lab | South lab\nInspect | x\nReplace |  | x')
        self.assertFalse(any(c['alignment_uncertain'] for c in _pdf_table_cells(table)))

    def test_overlapping_row_anchors_flag_uncertainty_without_guessing(self):
        cells = [SimpleNamespace(start_row_offset_idx=row, start_col_offset_idx=col,
                    text=text, row_span=1, col_span=1, column_header=False,
                    bbox=SimpleNamespace(l=left, r=left + 10, t=top, b=bottom))
                 for row, col, text, left, top, bottom in [
                     (1, 0, 'Inspect', 0, 20, 45), (2, 0, 'Replace', 0, 40, 60),
                     (1, 1, 'x', 30, 41, 43)]]
        marker = next(c for c in _pdf_table_cells(SimpleNamespace(data=SimpleNamespace(table_cells=cells)))
                      if c['text'] == 'x')
        self.assertEqual(marker['row'], 1)
        self.assertTrue(marker['alignment_uncertain'])

    def test_tall_category_does_not_override_nearby_equipment_row_labels(self):
        for marker_column, category_column in [(8, 2), (2, 8)]:
            with self.subTest(marker_column=marker_column):
                def cell(row, column, text, top, bottom):
                    return SimpleNamespace(start_row_offset_idx=row, start_col_offset_idx=column,
                        text=text, row_span=1, col_span=1, column_header=False,
                        bbox=SimpleNamespace(l=column * 10, r=column * 10 + 8,
                                             t=top, b=bottom))
                table = SimpleNamespace(data=SimpleNamespace(table_cells=[
                    cell(10, category_column, 'Equipment group', 35, 65),
                    cell(10, 5, 'Measure pressure', 20, 40),
                    cell(20, 5, 'Replace filter', 50, 70),
                    cell(10, marker_column, '✓', 54, 58),
                ]))
                marker = next(c for c in _pdf_table_cells(table) if c['text'] == '✓')
                self.assertEqual(marker['row'], 20)
                self.assertFalse(marker['alignment_uncertain'])

    def test_single_digit_counts_keep_columns_beneath_a_spanning_caption(self):
        def cell(row, column, text, header=False, span=1):
            return SimpleNamespace(start_row_offset_idx=row, start_col_offset_idx=column,
                text=text, row_span=1, col_span=span, column_header=header, row_header=False,
                bbox=SimpleNamespace(l=column * 40, r=column * 40 + 20,
                                     t=row * 20, b=row * 20 + 10))
        table = SimpleNamespace(data=SimpleNamespace(table_cells=[
            cell(0, 0, 'Equipment inspection counts', True, 3),
            cell(1, 0, 'Operating'), cell(1, 1, '2'), cell(1, 2, '7'),
            cell(2, 0, 'Under repair'), cell(2, 1, '4'), cell(2, 2, '6'),
        ]))
        self.assertEqual(_pdf_table_text(table, None),
                         'Equipment inspection counts\nOperating | 2 | 7\nUnder repair | 4 | 6')
        self.assertFalse(any(c['alignment_uncertain'] for c in _pdf_table_cells(table)))

    def test_column_headers_restart_for_each_header_row(self):
        def cell(row, column, text, header=False, span=1):
            return SimpleNamespace(start_row_offset_idx=row, start_col_offset_idx=column,
                text=text, row_span=1, col_span=span, column_header=header, row_header=False,
                bbox=SimpleNamespace(l=column * 50, r=column * 50 + 30,
                                     t=row * 20, b=row * 20 + 10))
        table = SimpleNamespace(data=SimpleNamespace(table_cells=[
            cell(0, 0, 'Annual equipment inspections', True, 3),
            cell(1, 0, 'Instrument', True), cell(1, 1, 'North lab', True),
            cell(1, 2, 'South lab', True),
            cell(2, 0, 'Pressure sensor'), cell(2, 1, '4'), cell(2, 2, '8'),
        ]))
        self.assertEqual(_pdf_table_text(table, None),
                         'Annual equipment inspections\nInstrument | North lab | South lab\nPressure sensor | 4 | 8')
        headers = [(c['row'], c['column']) for c in _pdf_table_cells(table) if c['header']]
        self.assertEqual(headers, [(0, 0), (1, 0), (1, 1), (1, 2)])

    def test_numeric_identifier_separators_require_unambiguous_local_source_glyphs(self):
        def glyphs(text, left=0):
            return [{'text': char, 'index': i, 'bbox': [left + i, 0, left + i + 1, 1]}
                    for i, char in enumerate(text)]
        self.assertEqual(restore_numeric_identifiers('Rule 73582 applies.', [0, 0, 100, 10],
                                                    glyphs('Rule 73-\n582 applies.')),
                         'Rule 73-582 applies.')
        self.assertEqual(restore_numeric_identifiers('Rule 73582 applies.', [0, 0, 100, 10], []),
                         'Rule 73582 applies.')
        self.assertEqual(restore_numeric_identifiers('Rule 73582 applies.', [0, 0, 100, 10],
                                                    glyphs('73-582 735-82')),
                         'Rule 73582 applies.')
        self.assertEqual(restore_numeric_identifiers('Rule 73582 applies.', [0, 0, 100, 10],
                                                    glyphs('73-582', left=200)),
                         'Rule 73582 applies.')

    def test_wrapped_vertical_header_remains_one_column(self):
        cell = SimpleNamespace(start_row_offset_idx=0, start_col_offset_idx=0,
            text='Control room', column_header=True, row_span=1, col_span=1,
            bbox=SimpleNamespace(l=20, r=38, t=0, b=100))
        characters = []
        for text, x in [('Control ', 20), ('room', 30)]:
            for index, char in enumerate(text):
                characters.append({'text': char, 'index': len(characters),
                                   'bbox': [x, index * 9, x + 8, index * 9 + 8]})
        table = SimpleNamespace(data=SimpleNamespace(table_cells=[cell]))
        self.assertEqual(len(_pdf_table_cells(table, characters)), 1)
        self.assertEqual(_pdf_table_text(table, None, characters), 'Control room')

    def test_local_list_heading_does_not_govern_following_outdented_numbered_rule(self):
        def node(kind, text, index, left, top):
            return SimpleNamespace(label=kind, text=text, orig=text, marker=None,
                self_ref=f'#/texts/{index}', parent=SimpleNamespace(cref='#/body'),
                prov=[SimpleNamespace(page_no=1, bbox=SimpleNamespace(
                    to_top_left_origin=lambda height: SimpleNamespace(l=left, t=top, r=500, b=top + 12)))])
        nodes = [node('section_header', '1. Observatory', 0, 40, 40),
                 node('section_header', 'Portable instruments:', 1, 60, 80),
                 node('list_item', '- Spectrometers.', 2, 60, 100),
                 node('list_item', '2. Every station retains logs.', 3, 40, 140)]
        document = SimpleNamespace(iterate_items=lambda with_groups: iter((node, 1) for node in nodes),
            pages={1: SimpleNamespace(size=SimpleNamespace(height=800))},
            export_to_dict=lambda: {'texts': [], 'tables': [], 'pictures': [], 'groups': []})
        converter = Mock()
        converter.convert.return_value = SimpleNamespace(status='success', document=document)
        items, _, _ = parse_pdf(Path('unused.pdf'), {}, {'ocr': {'mode': 'off'}}, converter)
        self.assertEqual(items[1]['kind'], 'paragraph')
        self.assertEqual(items[-1]['details']['heading_path'], ['1. Observatory'])
        # Also ensure chunking does not repeat the local introduction at that boundary.
        base = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
        bundle, sidecar = _normalize(base['sources'][0], base['documents'][0], items)
        chunks, _ = _chunk_document(bundle, {'elements': sidecar}, 'build:test', {'max_tokens': 64})
        rule = next(chunk for chunk in chunks if 'Every station' in chunk['text'])
        self.assertNotIn('Portable instruments', rule['text'])
