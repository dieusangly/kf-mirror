"""Regression checks for generic causes found by the AML/CFT source review."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from docling_core.types.doc import DoclingDocument, DocItemLabel, Size, BoundingBox, CoordOrigin, ProvenanceItem
from knowledge_foundry.parsers import _pdf_heading_level, parse_pdf
from knowledge_foundry.pdf_source import adapt_native, unspace_heading
from knowledge_foundry.pdf_structure import order_single_column_pages
from test_pdf_tables import write_pdf


def add(doc, label, value, top, bottom, *, left=50, right=540, parent=None):
    return doc.add_text(label, value, parent=parent, prov=ProvenanceItem(
        page_no=1, charspan=(0, len(value)), bbox=BoundingBox(
            l=left, t=top, r=right, b=bottom, coord_origin=CoordOrigin.TOPLEFT)))


class StructuringRepairTests(unittest.TestCase):
    def test_peer_panels_precede_the_lower_spanning_section(self):
        def node(label, value, box):
            return SimpleNamespace(label=label, text=value, parent=SimpleNamespace(cref='#/body'),
                prov=[SimpleNamespace(page_no=1, bbox=SimpleNamespace(to_top_left_origin=lambda h:
                    SimpleNamespace(l=box[0], t=box[1], r=box[2], b=box[3])))])
        left = node('section_header', 'Purpose', [50, 100, 230, 115])
        content = node('text', 'A first panel', [50, 120, 230, 140])
        lower = node('section_header', 'Registration', [50, 200, 550, 215])
        right = node('section_header', 'Users', [330, 100, 550, 115])
        right_body = node('text', 'A second panel', [330, 120, 550, 140])
        flow = [(n, 1) for n in [left, content, lower, right, right_body]]
        ordered = order_single_column_pages(flow, {1: SimpleNamespace(size=SimpleNamespace(height=800))})
        self.assertEqual([n.text for n, _ in ordered], ['Purpose', 'A first panel', 'Users', 'A second panel', 'Registration'])

    def test_word_gap_recovery_requires_two_source_spacing_bands(self):
        value = 'A l p h a b e t a'
        glyphs = []
        x = 50
        for index, letter in enumerate('Alphabeta'):
            glyphs.append({'text': letter, 'index': index, 'bbox': [x, 50, x + 5, 60]})
            x += 11 if index == 4 else 7
        self.assertEqual(unspace_heading(value, glyphs), 'Alpha beta')
        for index, glyph in enumerate(glyphs):
            glyph['bbox'] = [50 + index * 7, 50, 55 + index * 7, 60]
        self.assertIsNone(unspace_heading(value, glyphs))

    def test_near_black_body_style_does_not_turn_prose_into_headings(self):
        with TemporaryDirectory() as name:
            path = Path(name) / 'body.pdf'
            write_pdf(path, b'0.137 0.122 0.125 rg BT /F1 12 Tf 50 700 Td (Source heading) Tj ET '
                            b'BT /F1 12 Tf 50 670 Td (An ordinary introductory sentence) Tj ET')
            doc = DoclingDocument(name='Fixture'); doc.add_page(1, Size(width=600, height=800))
            add(doc, DocItemLabel.SECTION_HEADER, 'Source heading', 84, 104)
            add(doc, DocItemLabel.TEXT, 'An ordinary introductory sentence', 114, 134)
            adapted, _ = adapt_native(doc.export_to_dict(), path, {'mode': 'off'})
            self.assertEqual(adapted['texts'][1]['label'], 'text')

    def test_colored_decimal_heading_does_not_require_a_matching_prototype(self):
        with TemporaryDirectory() as name:
            path = Path(name) / 'heading.pdf'
            value = '1.1.1. The legal framework'
            write_pdf(path, f'0 0.2 0.6 rg BT /F1 12 Tf 50 700 Td ({value}) Tj ET'.encode())
            doc = DoclingDocument(name='Fixture'); doc.add_page(1, Size(width=600, height=800))
            add(doc, DocItemLabel.LIST_ITEM, value, 84, 104)
            adapted, _ = adapt_native(doc.export_to_dict(), path, {'mode': 'off'})
            self.assertEqual(adapted['texts'][0]['label'], 'section_header')
            _, proof = adapt_native(doc.export_to_dict(), path, {'mode': 'off'})
            self.assertEqual(proof, json.loads(json.dumps(proof)))

    def test_numbering_preserves_part_annex_case_and_letter_scope(self):
        part = [(1, 'part', 'III. Group scope', 'numbered')]
        self.assertEqual(_pdf_heading_level('2. Consolidated policies', part, {}), (2, 'decimal'))
        annex = [(1, 'annex', 'ANNEXE 1', 'numbered'), (2, 'case', 'CAS 4 : Ownership', 'case'),
                 (3, 'sub', '1. Simple interests', 'decimal')]
        self.assertEqual(_pdf_heading_level('2. Cumulative interests', annex, {}), (3, 'decimal'))
        self.assertEqual(_pdf_heading_level('CAS 5 : Assets', annex, {}), (2, 'case'))
        letter = part + [(2, 'letter', 'A. Legal persons', 'letter')]
        self.assertEqual(_pdf_heading_level('1. Identification', letter, {}), (3, 'decimal'))
        self.assertEqual(_pdf_heading_level('B. Legal arrangements', letter, {}), (2, 'letter'))
        self.assertEqual(_pdf_heading_level('ANNEX VI', annex, {}), (1, 'numbered'))
        chapter = [(1, 'chapter', 'Chapitre 7 - Sectors', 'legal')]
        self.assertEqual(_pdf_heading_level('II. Domestic framework', chapter, {}), (2, 'numbered'))
        self.assertEqual(_pdf_heading_level('2. Consolidated policies', part,
            {'2. consolidated policies': (2, 'III. Differently spelled group scope')}), (2, 'decimal'))

    def test_source_typography_keeps_sector_above_repeated_subsections(self):
        with TemporaryDirectory() as name:
            path = Path(name) / 'sectors.pdf'
            stream = (b'0 0.3 0.3 rg BT /F1 18 Tf 50 700 Td (Crowdfunding) Tj ET '
                      b'0 0.7 0.4 rg BT /F1 14 Tf 50 670 Td (Description) Tj ET '
                      b'0 0 0 rg BT /F1 10 Tf 50 640 Td (The sector includes several regulated activities.) Tj ET '
                      b'0 0.7 0.4 rg BT /F1 14 Tf 50 600 Td (Exposure) Tj ET')
            write_pdf(path, stream)
            doc = DoclingDocument(name='Fixture'); doc.add_page(1, Size(width=600, height=800))
            for title, top, bottom in [('Crowdfunding', 80, 105), ('Description', 115, 135),
                                       ('Exposure', 185, 205)]:
                add(doc, DocItemLabel.SECTION_HEADER, title, top, bottom)
            add(doc, DocItemLabel.TEXT, 'The sector includes several regulated activities.', 148, 165)
            items, native, _ = parse_pdf(path, {}, {'ocr': {'mode': 'off'}}, native_document=doc.export_to_dict())
            headings = {n['text']: n for n in items if n['kind'] == 'heading'}
            self.assertEqual(headings['Description']['details']['heading_path'], ['Crowdfunding', 'Description'])
            self.assertEqual(headings['Exposure']['details']['heading_path'], ['Crowdfunding', 'Exposure'])
            self.assertEqual(native, doc.export_to_dict())

    def test_literal_numbered_caption_outside_diagram_is_body(self):
        with TemporaryDirectory() as name:
            path = Path(name) / 'caption.pdf'
            value = '246. The exemption applies subject to checking control.'
            write_pdf(path, f'BT /F1 10 Tf 50 350 Td ({value}) Tj ET'.encode())
            doc = DoclingDocument(name='Fixture'); doc.add_page(1, Size(width=600, height=800))
            picture = doc.add_picture(prov=ProvenanceItem(page_no=1, charspan=(0, 0), bbox=BoundingBox(
                l=40, t=100, r=550, b=400, coord_origin=CoordOrigin.TOPLEFT)))
            caption = add(doc, DocItemLabel.CAPTION, value, 438, 455, parent=picture)
            native = doc.export_to_dict()
            adapted, proof = adapt_native(native, path, {'mode': 'off'})
            node = adapted['texts'][0]
            self.assertEqual(node['label'], 'text')
            self.assertEqual(node['parent'], {'$ref': '#/body'})
            self.assertIn('source_external_caption', [r['method'] for r in proof['repairs']])
            self.assertEqual(native['texts'][0]['label'], 'caption')
            DoclingDocument.model_validate(adapted)

    def test_wrapped_date_is_not_split_into_a_new_note(self):
        with TemporaryDirectory() as name:
            path = Path(name) / 'date.pdf'
            value = '23 See the order of 27 July 2011.'
            write_pdf(path, b'BT /F1 10 Tf 50 140 Td (23 See the order of) Tj ET '
                            b'BT /F1 10 Tf 50 125 Td (27 July 2011.) Tj ET')
            doc = DoclingDocument(name='Fixture'); doc.add_page(1, Size(width=600, height=800))
            add(doc, DocItemLabel.FOOTNOTE, value, 645, 680)
            adapted, proof = adapt_native(doc.export_to_dict(), path, {'mode': 'off'})
            self.assertEqual([n['text'] for n in adapted['texts']], [value])
            self.assertIsNone(proof)

    def test_merged_notes_split_only_at_literal_left_aligned_marker(self):
        with TemporaryDirectory() as name:
            path = Path(name) / 'notes.pdf'
            write_pdf(path, b'BT /F1 10 Tf 50 140 Td (39 First source note.) Tj ET '
                            b'BT /F1 10 Tf 50 125 Td (40 Separate source note.) Tj ET')
            doc = DoclingDocument(name='Fixture'); doc.add_page(1, Size(width=600, height=800))
            add(doc, DocItemLabel.FOOTNOTE, '39 First source note. 40 Separate source note.', 645, 680)
            adapted, proof = adapt_native(doc.export_to_dict(), path, {'mode': 'off'})
            self.assertEqual([n['text'] for n in adapted['texts']], ['39 First source note.', '40 Separate source note.'])
            self.assertIn('source_note_boundaries', [r['method'] for r in proof['repairs']])
            DoclingDocument.model_validate(adapted)
