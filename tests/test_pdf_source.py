"""Generic source-adapter regressions. OCR and PDF conversion are mocked."""
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from docling_core.types.doc import (DoclingDocument, DocItemLabel, Size, BoundingBox,
                                    CoordOrigin, ProvenanceItem)
from knowledge_foundry.pdf_source import (adapt_native, cell, complete_final_row,
    contents_cells, coarsen_columns, invisible_objects, key, lines, provenance, text, raster_display_title, joined_lines)
from knowledge_foundry.pdf_structure import pdf_page_characters
from knowledge_foundry.parsers import parse_pdf
from knowledge_foundry.parsing import _normalize, ROOT
from scripts.auto_review_parsing import audit_document
from test_pdf_tables import write_pdf


def glyphs(value, left, top, start=0):
    result = []
    previous_space = False
    for i, letter in enumerate(value):
        if not letter.isspace():
            result.append({'text': letter, 'index': start + i, 'bbox': [left + i * 5, top, left + i * 5 + 4, top + 8],
                           'line_y': top + 4, 'line_size': 10, 'space_before': previous_space})
        previous_space = letter.isspace()
    return result


class SourceAdapterTests(unittest.TestCase):
    def test_short_suffix_outside_native_box_is_not_deleted(self):
        with TemporaryDirectory() as name:
            path = Path(name) / 'identifier.pdf'
            write_pdf(path, b'BT /F1 12 Tf 50 700 Td (Batch identifier) Tj ET '
                      b'BT /F1 12 Tf 250 700 Td (42) Tj ET')
            doc = DoclingDocument(name='Fixture'); doc.add_page(1, Size(width=600, height=800))
            value = 'Batch identifier 42'
            doc.add_text(DocItemLabel.TEXT, value, prov=ProvenanceItem(page_no=1,
                charspan=(0, len(value)), bbox=BoundingBox(l=48, t=85, r=200, b=110,
                                                                       coord_origin=CoordOrigin.TOPLEFT)))
            converter = Mock(); converter.convert.return_value = SimpleNamespace(status='success', document=doc)
            items, native, warnings = parse_pdf(path, {}, {'ocr': {'mode': 'off'}}, converter)
            self.assertEqual(items[0]['text'], value)
            self.assertEqual(native, doc.export_to_dict())
            self.assertIn('source_text_edge_uncertain', [w['code'] for w in warnings])
            # Omitting the warning cannot make the independent source audit
            # accept this uncertainty; replay derives it from the original.
            folder = Path(name)
            base = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
            bundle, sidecar = _normalize(base['sources'][0], base['documents'][0], items)
            (folder / 'docling.json').write_text(json.dumps(native))
            (folder / 'parse.json').write_text(json.dumps({'warnings': [], 'settings': {'ocr': {'mode': 'off'}}}))
            audited = audit_document({'manifest_id': 'fixture', 'status': 'parsed', 'warnings': []},
                                     bundle['elements'], sidecar, folder, source_path=path)
            self.assertIn('PDF_SOURCE_VERIFICATION_UNCERTAIN', [f['code'] for f in audited['findings']])

    def test_vector_numbers_are_not_discarded_for_lacking_a_text_layer(self):
        with TemporaryDirectory() as name:
            path = Path(name) / 'outlined-number.pdf'
            write_pdf(path, b'60 700 m 50 690 l 65 690 l S 60 700 m 60 680 l S')
            doc = DoclingDocument(name='Fixture'); doc.add_page(1, Size(width=600, height=800))
            value = '4'
            doc.add_text(DocItemLabel.TEXT, value, prov=ProvenanceItem(page_no=1,
                charspan=(0, len(value)), bbox=BoundingBox(l=48, t=95, r=68, b=122,
                                                                       coord_origin=CoordOrigin.TOPLEFT)))
            original = doc.export_to_dict()
            evidence = {'settings': {'engine': 'mock'}, 'words': [
                {'text': value, 'confidence': 96, 'bbox': [50, 100, 65, 120]}]}
            with patch('knowledge_foundry.pdf_source.ocr_region', return_value=evidence) as engine:
                adapted, proof = adapt_native(original, path, {'mode': 'auto'})
            engine.assert_called_once()
            self.assertEqual(adapted, original)
            self.assertIsNone(proof)

    def test_failed_ocr_is_uncertainty_not_proof_that_a_word_is_furniture(self):
        with TemporaryDirectory() as name:
            path = Path(name) / 'graphic-label.pdf'; write_pdf(path, b'40 650 200 100 re S')
            doc = DoclingDocument(name='Fixture'); doc.add_page(1, Size(width=600, height=800))
            value = 'STOP'
            doc.add_text(DocItemLabel.TEXT, value, prov=ProvenanceItem(page_no=1,
                charspan=(0, len(value)), bbox=BoundingBox(l=50, t=65, r=130, b=90,
                                                                       coord_origin=CoordOrigin.TOPLEFT)))
            original = doc.export_to_dict()
            with patch('knowledge_foundry.pdf_source.ocr_region', return_value={'settings': {'engine': 'mock'}, 'words': []}):
                adapted, proof = adapt_native(original, path, {'mode': 'auto'})
            self.assertEqual(adapted, original)
            self.assertEqual(proof['uncertainties'][0]['code'], 'source_ocr_verification_uncertain')

    def test_uncertain_raster_body_prose_is_never_treated_as_a_cover_title(self):
        value = 'A source display title containing several clearly separated words'
        words = [{'bbox': [40, 100, 80, 130]} for _ in value.split()]
        self.assertTrue(raster_display_title(value, [40, 100, 500, 300], 800, words))
        self.assertFalse(raster_display_title(value + '.', [40, 100, 500, 300], 800, words))
        small = [{'bbox': [40, 100, 80, 109]} for _ in value.split()]
        self.assertFalse(raster_display_title(value, [40, 100, 500, 700], 800, small))
        self.assertFalse(raster_display_title(' '.join([value] * 4), [40, 100, 500, 700], 800, words))

    def test_mixed_provenance_preserves_literal_text_and_uncertain_ocr(self):
        with TemporaryDirectory() as name:
            path = Path(name) / 'mixed.pdf'
            write_pdf(path, b'BT /F1 12 Tf 50 700 Td (laboratory.example) Tj ET 300 690 30 20 re S')
            doc = DoclingDocument(name='Fixture'); doc.add_page(1, Size(width=600, height=800))
            literal = 'laboratory.example'; value = literal + ' ZZ'
            node = doc.add_text(DocItemLabel.TEXT, value, prov=ProvenanceItem(page_no=1,
                charspan=(0, len(literal)), bbox=BoundingBox(l=48, t=85, r=200, b=110, coord_origin=CoordOrigin.TOPLEFT)))
            node.prov.append(ProvenanceItem(page_no=1, charspan=(len(literal) + 1, len(value)),
                bbox=BoundingBox(l=300, t=85, r=330, b=110, coord_origin=CoordOrigin.TOPLEFT)))
            with patch('knowledge_foundry.pdf_source.ocr_region', return_value={'settings': {'engine': 'mock'}, 'words': []}):
                adapted, proof = adapt_native(doc.export_to_dict(), path, {'mode': 'auto'})
            DoclingDocument.model_validate(adapted)
            self.assertEqual(adapted['texts'][0]['text'], literal)
            self.assertEqual(adapted['texts'][0]['content_layer'], 'body')
            self.assertEqual(adapted['texts'][1]['text'], 'ZZ')
            self.assertEqual(adapted['texts'][1]['content_layer'], 'body')
            self.assertEqual(proof['repairs'][0]['method'], 'source_mixed_provenance')
            self.assertEqual(proof['uncertainties'][0]['code'], 'source_ocr_verification_uncertain')

    def test_caption_and_heading_split_remains_a_valid_native_working_document(self):
        with TemporaryDirectory() as name:
            path = Path(name) / 'title.pdf'
            write_pdf(path, b'BT /F1 10 Tf 50 740 Td (Figure 8) Tj ET '
                      b'BT /F1 14 Tf 50 710 Td (SENSOR) Tj ET '
                      b'BT /F1 14 Tf 50 690 Td (CALIBRATION) Tj ET')
            doc = DoclingDocument(name='Fixture'); doc.add_page(1, Size(width=600, height=800))
            for value, box in [('SENSOR', [48, 78, 170, 100]),
                               ('CALIBRATION Figure 8', [48, 45, 170, 120])]:
                doc.add_heading(value, level=1, prov=ProvenanceItem(page_no=1, charspan=(0, len(value)),
                    bbox=BoundingBox(l=box[0], t=box[1], r=box[2], b=box[3], coord_origin=CoordOrigin.TOPLEFT)))
            source = doc.export_to_dict()
            adapted, proof = adapt_native(source, path, {'mode': 'off'})
            DoclingDocument.model_validate(adapted)
            self.assertEqual(adapted['texts'][0]['text'], 'SENSOR\nCALIBRATION')
            self.assertEqual(adapted['texts'][1]['text'], 'Figure 8')
            self.assertEqual(adapted['texts'][1]['label'], 'caption')
            self.assertEqual(adapted['body']['children'][0]['$ref'], '#/texts/1')
            self.assertEqual(source, doc.export_to_dict())
            self.assertEqual(len(proof['repairs']), 2)

    def test_merged_notes_split_at_literal_line_markers_and_hanging_text_joins(self):
        with TemporaryDirectory() as name:
            path = Path(name) / 'notes.pdf'
            write_pdf(path, b'BT /F1 8 Tf 40 100 Td ((3) First note.) Tj ET '
                      b'BT /F1 8 Tf 40 85 Td ((4) Second note.) Tj ET '
                      b'BT /F1 8 Tf 40 72 Td ((5) Third note) Tj ET '
                      b'BT /F1 8 Tf 51 62 Td (continued here.) Tj ET')
            doc = DoclingDocument(name='Fixture'); doc.add_page(1, Size(width=600, height=800))
            for value, label, box in [('(3) First note. (4) Second note.', DocItemLabel.FOOTNOTE, [39, 690, 130, 720]),
                    ('(5) Third note', DocItemLabel.TEXT, [39, 722, 130, 732]),
                    ('continued here.', DocItemLabel.TEXT, [51, 732, 130, 742])]:
                doc.add_text(label, value, prov=ProvenanceItem(page_no=1, charspan=(0, len(value)),
                    bbox=BoundingBox(l=box[0], t=box[1], r=box[2], b=box[3], coord_origin=CoordOrigin.TOPLEFT)))
            adapted, proof = adapt_native(doc.export_to_dict(), path, {'mode': 'off'})
            DoclingDocument.model_validate(adapted)
            notes = [n['text'] for n in adapted['texts'] if n['label'] == 'footnote']
            self.assertTrue(any(n.startswith('(3)') and '(4)' not in n for n in notes), notes)
            self.assertTrue(any(n.startswith('(4)') for n in notes), notes)
            self.assertTrue(any(n.startswith('(5)') and 'continued here.' in n for n in notes), notes)

    def test_line_assembly_keeps_source_spaces_and_dot_leaders(self):
        chars = glyphs('Entry name.... 13', 40, 60)
        self.assertEqual(text(chars), 'Entry name.... 13')
        self.assertEqual(len(lines(chars)), 1)
        from knowledge_foundry.parsers import _pdf_text
        grouped = [glyphs('Exposure to ZX-', 40, 60), glyphs('QR risks', 40, 80, 100)]
        self.assertIn('ZX-QR', _pdf_text(joined_lines(grouped)))

    def test_contents_require_complete_entry_page_pairs(self):
        chars = []
        for i, (title, number) in enumerate([('Preface', '7'), ('First chapter', '13'),
                                            ('Second chapter', '22'), ('Third chapter', '45'),
                                            ('Appendix', '61')]):
            chars += glyphs(title + '......', 40, 50 + i * 40, len(chars) * 3)
            chars += glyphs(number, 220, 50 + i * 40, len(chars) * 3)
        table = {'prov': provenance(1, [35, 40, 240, 250], '')}
        result = contents_cells(table, chars, 400)
        self.assertEqual([(c['text'].split('.')[0], c['start_col_offset_idx']) for c in result[::2]],
                         [('Preface', 0), ('First chapter', 0), ('Second chapter', 0), ('Third chapter', 0), ('Appendix', 0)])
        self.assertEqual([c['text'] for c in result[1::2]], ['7', '13', '22', '45', '61'])
        orphan = chars + glyphs('Unfinished entry', 40, 245, 2000)
        table['prov'] = provenance(1, [35, 40, 240, 270], '')
        self.assertIsNone(contents_cells(table, orphan, 400))
        self.assertIsNone(contents_cells(table, [c for c in chars if c['text'] != '.'], 400))

    def test_final_table_row_requires_exact_complete_column_witnesses(self):
        values = [('Left unit', 40, 40, 0, 0), ('Right unit', 180, 40, 0, 1),
                  ('Officer', 45, 70, 1, 0), ('Officer', 187, 70, 1, 1)]
        chars, raw = [], []
        for value, x, y, row, col in values:
            group = glyphs(value, x, y, len(chars) * 3)
            chars += group
            raw.append(cell(value, row, col, [x, y, x + len(value) * 5, y + 8]))
        chars += glyphs('A. SAMPLE', 40, 100, 1000) + glyphs('B. EXAMPLE', 180, 100, 2000)
        table = {'prov': provenance(1, [35, 30, 245, 115], ''), 'data': {'table_cells': raw}}
        result = complete_final_row(table, chars, 400)
        self.assertEqual([c['text'] for c in result[-2:]], ['A. SAMPLE', 'B. EXAMPLE'])
        self.assertEqual([c['start_col_offset_idx'] for c in result[-2:]], [0, 1])
        for variation in ['wrong_text', 'missing_column', 'overlap', 'header', 'outside_row']:
            changed, source = copy.deepcopy(table), copy.deepcopy(chars)
            if variation == 'wrong_text': changed['data']['table_cells'][0]['text'] = 'Other unit'
            elif variation == 'missing_column': source = [c for c in source if c['index'] < 2000]
            elif variation == 'overlap': changed['data']['table_cells'][1]['bbox'] = changed['data']['table_cells'][0]['bbox']
            elif variation == 'header': changed['data']['table_cells'][0]['column_header'] = True
            else:
                for c in source:
                    if c['index'] >= 2000: c['bbox'][1] -= 30; c['bbox'][3] -= 30
            with self.subTest(variation=variation): self.assertIsNone(complete_final_row(changed, source, 400))

    def test_column_coarsening_needs_full_painted_divider_and_unique_glyphs(self):
        chars, raw = [], []
        for value, x, y, row, col in [('Earlier', 40, 40, 0, 1), ('Later', 180, 40, 0, 2),
                                      ('Item', 40, 70, 1, 0), ('13', 70, 70, 1, 1), ('Item 14', 180, 70, 1, 2)]:
            chars += glyphs(value, x, y, len(chars) * 3)
            raw.append(cell(value, row, col, [x, y, x + len(value) * 5, y + 8], header=row == 0))
        table = {'prov': provenance(1, [35, 30, 245, 100], ''), 'data': {'table_cells': raw, 'num_cols': 3}}
        geometry = {'strokes': [[140, 30, 140, 65], [140, 65, 140, 100]]}
        result = coarsen_columns(table, chars, 400, geometry)
        self.assertEqual([c['text'] for c in result], ['Earlier', 'Later', 'Item 13', 'Item 14'])
        self.assertIsNone(coarsen_columns(table, chars, 400, {'strokes': [[140, 30, 140, 80]]}))
        wrong = copy.deepcopy(table); wrong['data']['table_cells'][3]['text'] = '31'
        self.assertIsNone(coarsen_columns(wrong, chars, 400, geometry))
        grouped = copy.deepcopy(table)
        grouped['data']['table_cells'][0]['col_span'] = 2
        self.assertIsNone(coarsen_columns(grouped, chars, 400, geometry))
        multirow = copy.deepcopy(table)
        multirow['data']['table_cells'][2]['column_header'] = True
        self.assertIsNone(coarsen_columns(multirow, chars, 400, geometry))

    def test_occluded_duplicate_requires_zero_pixel_contribution_and_whole_objects(self):
        with TemporaryDirectory() as name:
            path = Path(name) / 'clip.pdf'
            write_pdf(path, b'BT /F1 12 Tf 40 700 Td (Calibration protocol details) Tj ET '
                      b'q 0 0 0 0 re W n BT /F1 12 Tf 40 500 Td (Calibration protocol) Tj ET Q')
            original = path.read_bytes()
            chars = pdf_page_characters(path, 1)
            hidden = [c for c in chars if c['text'].strip() and 280 < c['bbox'][1] < 310]
            visible = [c for c in chars if c['text'].strip() and 80 < c['bbox'][1] < 110]
            self.assertTrue(invisible_objects(path, 1, hidden))
            self.assertFalse(invisible_objects(path, 1, visible))
            self.assertFalse(invisible_objects(path, 1, hidden[:-1]))
            self.assertEqual(path.read_bytes(), original)

    def test_adapter_keeps_frozen_native_and_audit_rejects_missing_or_tampered_proof(self):
        with TemporaryDirectory() as name:
            folder = Path(name); path = folder / 'clip.pdf'
            write_pdf(path, b'BT /F1 12 Tf 40 700 Td (Calibration protocol details) Tj ET '
                      b'q 0 0 0 0 re W n BT /F1 12 Tf 40 500 Td (Calibration protocol) Tj ET Q', font=b'Helvetica-Bold')
            doc = DoclingDocument(name='Fixture')
            doc.add_page(1, Size(width=600, height=800))
            for value, box in [('Calibration protocol details', [35, 85, 250, 110]),
                               ('Calibration protocol', [35, 285, 250, 310])]:
                doc.add_text(DocItemLabel.TEXT, value, prov=ProvenanceItem(page_no=1, charspan=(0, len(value)),
                    bbox=BoundingBox(l=box[0], t=box[1], r=box[2], b=box[3], coord_origin=CoordOrigin.TOPLEFT)))
            original = doc.export_to_dict()
            converter = Mock(); converter.convert.return_value = SimpleNamespace(status='success', document=doc)
            items, native, warnings = parse_pdf(path, {}, {'ocr': {'mode': 'off'}}, converter)
            self.assertEqual(native, original)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]['details']['pdf_source_adapter']['repairs'][0]['method'], 'occluded_duplicate')
            base = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
            bundle, sidecar = _normalize(base['sources'][0], base['documents'][0], items)
            (folder / 'docling.json').write_text(json.dumps(original))
            (folder / 'parse.json').write_text(json.dumps({'warnings': warnings, 'settings': {'ocr': {'mode': 'off'}}}))
            outcome = {'manifest_id': 'fixture', 'status': 'parsed', 'warnings': warnings}
            valid = audit_document(outcome, bundle['elements'], sidecar, folder, source_path=path)
            self.assertFalse([f for f in valid['findings'] if f['severity'] == 'error'], valid['findings'])
            proof = sidecar[0].pop('pdf_source_adapter')
            invalid = audit_document(outcome, bundle['elements'], sidecar, folder, source_path=path)
            self.assertIn('PDF_SOURCE_ADAPTER_MISMATCH', [f['code'] for f in invalid['findings']])
            sidecar[0]['pdf_source_adapter'] = proof
            proof['repairs'][0]['evidence']['changed_pixels'] = 1
            invalid = audit_document(outcome, bundle['elements'], sidecar, folder, source_path=path)
            self.assertIn('PDF_SOURCE_ADAPTER_MISMATCH', [f['code'] for f in invalid['findings']])
            # A changed original invalidates the in-process source cache. The
            # same words, visibly repeated, must no longer be excluded.
            write_pdf(path, b'BT /F1 12 Tf 40 700 Td (Calibration protocol details) Tj ET '
                      b'BT /F1 12 Tf 40 500 Td (Calibration protocol) Tj ET', font=b'Helvetica-Bold')
            current, current_proof = adapt_native(original, path, {'mode': 'off'})
            self.assertEqual(current, original)
            self.assertIsNone(current_proof)

    def test_ocr_recovery_uses_mocked_pixels_and_records_settings(self):
        with TemporaryDirectory() as name:
            path = Path(name) / 'graphic.pdf'; write_pdf(path, b'40 400 400 200 re S')
            native = {'pages': {'1': {'size': {'height': 800, 'width': 600}}}, 'tables': [],
                'texts': [{'self_ref': '#/texts/0', 'text': 'Instrument cal', 'orig': 'Instrument cal',
                           'label': 'section_header', 'content_layer': 'body', 'parent': {'$ref': '#/body'},
                           'prov': provenance(1, [50, 250, 200, 265], 'Instrument cal')}],
                'body': {'children': [{'$ref': '#/texts/0'}]}}
            evidence = {'settings': {'engine': 'mock-tesseract', 'psm': 6}, 'crop': [47, 247, 203, 268],
                        'words': [{'text': 'Instrument', 'confidence': 96, 'bbox': [50, 250, 120, 265]},
                                  {'text': 'calibration', 'confidence': 96, 'bbox': [125, 250, 210, 265]}]}
            with patch('knowledge_foundry.pdf_source.ocr_region', return_value=evidence) as engine:
                repaired, proof = adapt_native(native, path, {'mode': 'auto'})
            engine.assert_called_once()
            self.assertEqual(repaired['texts'][0]['text'], 'Instrument calibration')
            self.assertEqual(proof['repairs'][0]['evidence']['settings']['engine'], 'mock-tesseract')
            self.assertEqual(native['texts'][0]['text'], 'Instrument cal')
            with patch('knowledge_foundry.pdf_source.ocr_region', return_value=None):
                repaired, proof = adapt_native(native, path, {'mode': 'auto', 'tesseract_cmd': 'offline-unavailable'})
            self.assertEqual(repaired, native); self.assertIsNone(proof)
