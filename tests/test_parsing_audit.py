"""Focused checks for the deterministic parsing audit."""

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
from knowledge_foundry.parsing_audit import audit_document, select_outcomes  # noqa: E402


class ParsingAuditTests(unittest.TestCase):
    def test_a_source_verified_short_transcript_cannot_be_dropped(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            source = folder / 'original.pdf'; source.write_bytes(b'saved original')
            native = {'texts': [{'self_ref': '#/texts/0', 'text': 'Sensor', 'label': 'text',
                                'prov': [{'page_no': 1, 'charspan': [0, 6]}]}],
                      'groups': [], 'pictures': [], 'tables': [],
                      'pages': {'1': {'size': {'width': 600, 'height': 800}}}}
            proof = {'repairs': [{'method': 'source_pixel_ocr', 'ref': '#/texts/0'}]}
            (folder / 'docling.json').write_text(json.dumps(native))
            (folder / 'parse.json').write_text('{"settings": {}, "warnings": []}')
            with patch('knowledge_foundry.pdf_source.adapt_native', return_value=(native, proof)):
                result = audit_document({'manifest_id': 'fixture', 'status': 'parsed'}, [], [],
                                        folder, source_path=source)
            self.assertIn('VERIFIED_OCR_TEXT_LOST', [f['code'] for f in result['findings']])

    def test_warning_severities_are_counted_separately(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            warnings = [{'code': 'empty_docling_table'}, {'code': 'unlocated_block'}]
            (folder / 'parse.json').write_text(json.dumps({'warnings': warnings}))
            result = audit_document({'manifest_id': 'fixture', 'status': 'partial',
                                     'warnings': warnings}, [], [], folder)
            self.assertEqual([(f['severity'], f['count']) for f in result['findings']
                              if f['code'] == 'PARSER_WARNING'], [('error', 1), ('warning', 1)])

    def test_inconclusive_ocr_requires_review_and_literal_transcript_preservation(self):
        from unittest.mock import patch
        for value in ['Sensor', '|']:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as name:
                folder = Path(name)
                source = folder / 'original.pdf'; source.write_bytes(b'saved original')
                node = {'self_ref': '#/texts/0', 'label': 'text', 'text': value,
                        'prov': [{'page_no': 1, 'charspan': [0, len(value)]}]}
                native = {'texts': [node], 'groups': [], 'tables': [], 'pictures': [],
                          'pages': {'1': {'size': {'width': 600, 'height': 800}}}}
                warning = {'code': 'source_ocr_verification_uncertain', 'ref': '#/texts/0', 'page': 1}
                proof = {'uncertainties': [warning]}
                (folder / 'docling.json').write_text(json.dumps(native))
                (folder / 'parse.json').write_text(json.dumps({'warnings': [warning], 'settings': {}}))
                elements = [{'id': 'label', 'kind': 'paragraph', 'ordinal': 0, 'text': value,
                             'location': {'format': 'pdf', 'page': 1}}]
                details = [{'element_id': 'label', 'docling_ref': '#/texts/0',
                            'evidence_role': 'authoritative', 'pdf_source_adapter': proof}]
                outcome = {'manifest_id': 'fixture', 'status': 'parsed', 'warnings': [warning]}
                with patch('knowledge_foundry.pdf_source.adapt_native', return_value=(native, proof)):
                    retained = audit_document(outcome, elements, details, folder, source_path=source)
                    elements[0]['text'] = 'different text'
                    lost = audit_document(outcome, elements, details, folder, source_path=source)
                self.assertFalse([f for f in retained['findings'] if f['severity'] == 'error'])
                self.assertEqual(retained['review_requirements'], [warning])
                self.assertIn('UNCERTAIN_OCR_TEXT_LOST', [f['code'] for f in lost['findings']])

    def test_replayed_uncertainty_requires_short_picture_transcript_even_without_saved_warning(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            source = folder / 'original.pdf'; source.write_bytes(b'saved original')
            node = {'self_ref': '#/texts/0', 'label': 'text', 'text': 'ZZ',
                    'parent': {'$ref': '#/pictures/0'},
                    'prov': [{'page_no': 1, 'charspan': [0, 2], 'bbox': {
                        'l': 50, 't': 100, 'r': 70, 'b': 110, 'coord_origin': 'TOPLEFT'}}]}
            native = {'texts': [node], 'groups': [], 'tables': [],
                      'pages': {'1': {'size': {'width': 600, 'height': 800}}},
                      'pictures': [{'self_ref': '#/pictures/0', 'prov': [{'page_no': 1}],
                                    'children': [{'$ref': '#/texts/0'}]}]}
            proof = {'uncertainties': [{'code': 'source_ocr_verification_uncertain',
                                       'ref': '#/texts/0', 'page': 1}]}
            (folder / 'docling.json').write_text(json.dumps(native))
            (folder / 'parse.json').write_text('{"warnings": [], "settings": {}}')
            outcome = {'manifest_id': 'fixture', 'status': 'parsed', 'warnings': []}
            with patch('knowledge_foundry.pdf_source.adapt_native', return_value=(native, proof)):
                missing = audit_document(outcome, [], [], folder, source_path=source)
                elements = [{'id': 'label', 'kind': 'paragraph', 'ordinal': 0, 'text': 'ZZ',
                             'location': {'format': 'pdf', 'page': 1, 'bbox': [50, 100, 70, 110]}}]
                sidecar = [{'element_id': 'label', 'docling_ref': '#/texts/0',
                            'evidence_role': 'auxiliary', 'pdf_source_adapter': proof}]
                retained = audit_document(outcome, elements, sidecar, folder, source_path=source)
            self.assertIn('UNCERTAIN_OCR_TEXT_LOST', [f['code'] for f in missing['findings']])
            self.assertNotIn('UNCERTAIN_OCR_TEXT_LOST', [f['code'] for f in retained['findings']])
            self.assertIn('PDF_SOURCE_VERIFICATION_UNCERTAIN', [f['code'] for f in retained['findings']])

    def test_running_header_fragment_requires_structural_role(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            header = 'Repeated publisher name and edition information'
            native = {'pages': {str(p): {'size': {'height': 800}} for p in [1, 2, 3]},
                      'texts': [{'self_ref': f'#/texts/{p}', 'label': 'page_header',
                                 'content_layer': 'furniture', 'text': header,
                                 'prov': [{'page_no': p, 'bbox': {
                                     'l': 40, 't': 30, 'r': 400, 'b': 50, 'coord_origin': 'TOPLEFT'}}]}
                                for p in [1, 2, 3]]}
            (folder / 'docling.json').write_text(json.dumps(native))
            (folder / 'parse.json').write_text('{"warnings": []}')
            elements = [{'id': f'header-{p}', 'kind': 'paragraph', 'ordinal': p, 'text': header,
                         'location': {'format': 'pdf', 'page': p, 'bbox': [40, 30, 400, 50]}}
                        for p in [1, 2, 3]]
            sidecar = [{'element_id': e['id'], 'evidence_role': 'structural'} for e in elements]
            outcome = {'manifest_id': 'sample', 'status': 'parsed', 'warnings': []}
            valid = audit_document(outcome, elements, sidecar, folder)
            self.assertNotIn('PDF_RUNNING_HEADER_ROLE', [f['code'] for f in valid['findings']])
            sidecar[1]['evidence_role'] = 'authoritative'
            changed = audit_document(outcome, elements, sidecar, folder)
            self.assertIn('PDF_RUNNING_HEADER_ROLE', [f['code'] for f in changed['findings']])
            # The native furniture witnesses are normally excluded from
            # canonical elements; their evidence still validates a short header
            # that leaked into a body node on one page.
            for node in native['texts']:
                node['text'] = 'Journal issue'
            (folder / 'docling.json').write_text(json.dumps(native))
            elements[0]['text'] = 'Journal issue'
            short = audit_document(outcome, elements[:1], sidecar[:1], folder)
            self.assertNotIn('INVALID_EVIDENCE_ROLE', [f['code'] for f in short['findings']])
            for node in native['texts']:
                node.update(label='text', content_layer='body')
            (folder / 'docling.json').write_text(json.dumps(native))
            unsupported = audit_document(outcome, elements[:1], sidecar[:1], folder)
            self.assertIn('INVALID_EVIDENCE_ROLE', [f['code'] for f in unsupported['findings']])

    def test_identifier_repairs_require_matching_local_pdf_glyphs(self):
        from knowledge_foundry.pdf_structure import pdf_page_characters

        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            source = folder / 'equipment.pdf'
            phrase = 'Inspect sensor 482-17-6 before returning the equipment to service.'
            stream = f'BT /F1 12 Tf 40 700 Td ({phrase}) Tj ET'.encode()
            objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
                       b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                       b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] '
                       b'/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
                       b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
                       b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
            data, offsets = b'%PDF-1.4\n', [0]
            for index, value in enumerate(objects, 1):
                offsets.append(len(data))
                data += f'{index} 0 obj\n'.encode() + value + b'\nendobj\n'
            xref = len(data)
            data += b'xref\n0 6\n0000000000 65535 f \n'
            data += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
            data += f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
            source.write_bytes(data)
            self.assertIn('482-17-6', ''.join(c['text'] for c in pdf_page_characters(source, 1)))
            native_text = phrase.replace('482-17-6', '48217-6')
            native = {'pages': {'1': {'size': {'width': 600, 'height': 800}}},
                      'texts': [{'self_ref': '#/texts/0', 'label': 'text', 'text': native_text,
                                 'prov': [{'page_no': 1, 'charspan': [0, len(native_text)],
                                           'bbox': {'l': 35, 't': 715, 'r': 570, 'b': 690,
                                                    'coord_origin': 'BOTTOMLEFT'}}]}]}
            (folder / 'docling.json').write_text(json.dumps(native))
            (folder / 'parse.json').write_text('{"warnings": []}')
            elements = [{'id': 'sensor', 'kind': 'paragraph', 'ordinal': 0, 'text': phrase,
                         'location': {'format': 'pdf', 'page': 1}}]
            sidecar = [{'element_id': 'sensor', 'docling_ref': '#/texts/0',
                        'evidence_role': 'authoritative'}]
            outcome = {'manifest_id': 'equipment', 'status': 'parsed', 'warnings': []}
            result = audit_document(outcome, elements, sidecar, folder, source_path=source)
            self.assertEqual(result['metrics']['source_text_lost'], 0)
            unverified = audit_document(outcome, elements, sidecar, folder)
            self.assertEqual(unverified['metrics']['source_text_lost'], 1)
            for wrong in (phrase.replace('482-17-6', value)
                          for value in ('482-71-6', '48217-6', '482-176', '482176',
                                        '482/17/6', '482+17-6', '482 17-6')):
                elements[0]['text'] = wrong
                result = audit_document(outcome, elements, sidecar, folder, source_path=source)
                self.assertEqual(result['metrics']['source_text_lost'], 1)

    def test_filename_only_note_role_is_valid_but_substantive_note_cannot_be_hidden(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            (folder / 'parse.json').write_text('{"warnings": []}')
            element = {'id': 'note', 'kind': 'footnote', 'ordinal': 0,
                       'text': '7 instrument-manual.pdf', 'location': {'format': 'pdf', 'page': 1}}
            details = {'element_id': 'note', 'evidence_role': 'structural',
                       'link_text_role': 'non_substantive_filename'}
            outcome = {'manifest_id': 'instrument-manual', 'status': 'parsed', 'warnings': []}
            result = audit_document(outcome, [element], [details], folder)
            self.assertNotIn('INVALID_EVIDENCE_ROLE', [f['code'] for f in result['findings']])
            element['text'] = '7 Operators must retain instrument records.'
            result = audit_document(outcome, [element], [details], folder)
            self.assertIn('INVALID_EVIDENCE_ROLE', [f['code'] for f in result['findings']])

    def test_pdf_furniture_roles_and_visual_parent_across_heading_paths(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            (folder / "parse.json").write_text('{"warnings": []}')
            elements = [
                {"id": "cover", "kind": "paragraph", "ordinal": 0, "text": "Liberté",
                 "location": {"format": "pdf", "page": 1}},
                {"id": "figure", "kind": "figure", "ordinal": 1, "text": "",
                 "location": {"format": "pdf", "page": 2}},
                {"id": "label", "kind": "paragraph", "ordinal": 2, "text": "Fraude fiscale",
                 "location": {"format": "pdf", "page": 2}},
            ]
            sidecar = [
                {"element_id": "cover", "evidence_role": "auxiliary", "parent_element_id": None},
                {"element_id": "figure", "evidence_role": "structural", "heading_path": ["Case"]},
                {"element_id": "label", "evidence_role": "auxiliary", "parent_element_id": "figure",
                 "heading_path": ["Case", "Suspected offence"]},
            ]
            for page in (3, 4, 5):
                elements.append({"id": f"header-{page}", "kind": "paragraph",
                                 "ordinal": len(elements), "text": "Repeated publication header",
                                 "location": {"format": "pdf", "page": page}})
                sidecar.append({"element_id": f"header-{page}", "evidence_role": "structural"})
            elements.append({"id": "drop-cap", "kind": "paragraph", "ordinal": len(elements),
                             "text": "F", "location": {"format": "pdf", "page": 6,
                                                       "bbox": [35, 60, 70, 85]}})
            sidecar.append({"element_id": "drop-cap", "evidence_role": "structural"})
            elements.append({"id": "graphic-noise", "kind": "paragraph", "ordinal": len(elements),
                             "text": "EDP", "location": {"format": "pdf", "page": 7,
                                                         "bbox": [100, 600, 200, 620]}})
            sidecar.append({"element_id": "graphic-noise", "evidence_role": "structural"})
            result = audit_document({"manifest_id": "sample", "status": "parsed",
                                     "warnings": []}, elements, sidecar, folder)
            role_errors = [finding for finding in result["findings"]
                           if finding["code"] in {"INVALID_EVIDENCE_ROLE", "BROKEN_HEADING_PATH"}]
            self.assertEqual(role_errors, [])

    def test_figure_asset_hash_provenance_and_auxiliary_text(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            (folder / "figures").mkdir()
            buffer = io.BytesIO()
            Image.new("RGB", (20, 20), "white").save(buffer, format="PNG")
            data = buffer.getvalue()
            (folder / "figures" / "figure.png").write_bytes(data)
            phrase = "Achat d'un bien immobilier pour 1,5 M€"
            native = {"pages": {"1": {"size": {"width": 100, "height": 100}}},
                      "pictures": [{"self_ref": "#/pictures/0", "prov": [{"page_no": 1,
                          "bbox": {"l": 10, "t": 90, "r": 50, "b": 50,
                                   "coord_origin": "BOTTOMLEFT"}}],
                          "children": [{"$ref": "#/texts/0"}]}],
                      "texts": [{"self_ref": "#/texts/0", "label": "text", "text": phrase,
                                 "content_layer": "body", "parent": {"$ref": "#/pictures/0"},
                                 "prov": [{"page_no": 1, "bbox": {"l": 12, "t": 80,
                                                                "r": 48, "b": 70}}]}],
                      "groups": [], "tables": []}
            (folder / "docling.json").write_text(json.dumps(native))
            (folder / "parse.json").write_text('{"warnings": []}')
            elements = [{"id": "figure", "kind": "figure", "ordinal": 0, "text": "",
                         "location": {"page": 1, "bbox": [10, 10, 50, 50]}},
                        {"id": "label", "kind": "paragraph", "ordinal": 1, "text": phrase,
                         "location": {"page": 1, "bbox": [12, 20, 48, 30]}}]
            sidecar = [{"element_id": "figure", "docling_ref": "#/pictures/0",
                        "evidence_role": "structural", "parent_element_id": None,
                        "asset": {"path": "figures/figure.png", "media_type": "image/png",
                                  "sha256": hashlib.sha256(data).hexdigest(),
                                  "representation_role": "authoritative_visual"}},
                       {"element_id": "label", "docling_ref": "#/texts/0",
                        "evidence_role": "auxiliary", "parent_element_id": "figure"}]
            outcome = {"manifest_id": "sample", "status": "parsed", "warnings": []}
            valid = audit_document(outcome, elements, sidecar, folder)
            self.assertEqual(valid["metrics"]["figure_assets_checked"], 1)
            self.assertFalse(any(item["severity"] == "error" for item in valid["findings"]))
            sidecar[1]["evidence_role"] = "authoritative"
            promoted = audit_document(outcome, elements, sidecar, folder)
            self.assertIn("FIGURE_TEXT_PROMOTED", [item["code"] for item in promoted["findings"]])
            sidecar[1]["evidence_role"] = "auxiliary"
            sidecar[0]["asset"]["sha256"] = "0" * 64
            damaged = audit_document(outcome, elements, sidecar, folder)
            self.assertIn("FIGURE_ASSET_INVALID", [item["code"] for item in damaged["findings"]])

    def test_figure_asset_does_not_hide_missing_document_flow_prose(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            (folder / "figures").mkdir()
            buffer = io.BytesIO()
            Image.new("RGB", (20, 20), "white").save(buffer, format="PNG")
            data = buffer.getvalue()
            (folder / "figures" / "figure.png").write_bytes(data)
            texts = ["L'organisme doit identifier les retraits effectués dans des conditions particulièrement inhabituelles et conserver les informations utiles.",
                     "La déclaration transmise à Tracfin comporte les éléments recueillis pendant l'opération et les références nécessaires à son examen."]
            nodes = [{"self_ref": f"#/texts/{index}", "label": "text", "text": value,
                      "content_layer": "body", "parent": {"$ref": "#/pictures/0"},
                      "prov": [{"page_no": 1, "bbox": {"l": 10, "r": 90,
                                                           "t": 80 - index * 20,
                                                           "b": 70 - index * 20}}]}
                     for index, value in enumerate(texts)]
            native = {"pages": {"1": {"size": {"width": 100, "height": 100}}},
                      "pictures": [{"self_ref": "#/pictures/0", "prov": [{"page_no": 1,
                          "bbox": {"l": 10, "t": 90, "r": 90, "b": 10,
                                   "coord_origin": "BOTTOMLEFT"}}],
                          "children": [{"$ref": node["self_ref"]} for node in nodes]}],
                      "texts": nodes, "groups": [], "tables": []}
            (folder / "docling.json").write_text(json.dumps(native))
            (folder / "parse.json").write_text('{"warnings": []}')
            elements = [{"id": "figure", "kind": "figure", "ordinal": 0,
                         "text": "", "location": {"page": 1, "bbox": [10, 10, 90, 90]}}]
            sidecar = [{"element_id": "figure", "docling_ref": "#/pictures/0",
                        "evidence_role": "structural", "parent_element_id": None,
                        "asset": {"path": "figures/figure.png", "media_type": "image/png",
                                  "sha256": hashlib.sha256(data).hexdigest(),
                                  "representation_role": "authoritative_visual"}}]
            result = audit_document({"manifest_id": "A", "status": "parsed",
                                     "warnings": []}, elements, sidecar, folder)
            self.assertEqual(result["metrics"]["source_text_lost"], 2)
            self.assertIn("PICTURE_TEXT_LOST", [item["code"] for item in result["findings"]])

    def test_t_duplicate_phrases_are_distinct_source_repetitions(self):
        phrases = [(23, "Virements de fonds sans justification économique"),
                   (50, "Virements sous couvert d'activité économique")]
        elements, sidecar = [], []
        for page, phrase in phrases:
            for index, top in enumerate((100, 300)):
                element_id = f"{page}-{index}"
                elements.append({"id": element_id, "kind": "paragraph",
                                 "ordinal": len(elements), "text": phrase,
                                 "location": {"page": page, "bbox": [50, top, 170, top + 20]}})
                sidecar.append({"element_id": element_id, "docling_ref": f"#/texts/{page}-{index}",
                                "evidence_role": "authoritative", "parent_element_id": None})
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            (folder / "parse.json").write_text('{"warnings": []}')
            repeated = audit_document({"manifest_id": "T", "status": "parsed",
                                       "warnings": []}, elements, sidecar, folder)
            self.assertEqual(repeated["metrics"]["source_repetition_groups"], 2)
            self.assertEqual(repeated["metrics"]["duplicate_content_groups"], 0)
            elements[1]["location"]["bbox"] = [50, 105, 170, 125]
            duplicated = audit_document({"manifest_id": "T", "status": "parsed",
                                         "warnings": []}, elements, sidecar, folder)
            self.assertEqual(duplicated["metrics"]["duplicate_content_groups"], 1)

    def test_picture_flow_list_marker_requires_list_item_kind(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            (folder / "parse.json").write_text('{"warnings": []}')
            element = {"id": "item", "kind": "paragraph", "ordinal": 0,
                       "text": "- lieux de l'opération ;", "location": {"page": 21}}
            sidecar = [{"element_id": "item", "docling_parent_ref": "#/pictures/2",
                        "evidence_role": "authoritative", "parent_element_id": None}]
            outcome = {"manifest_id": "A", "status": "parsed", "warnings": []}
            wrong = audit_document(outcome, [element], sidecar, folder)
            self.assertIn("PICTURE_LIST_ITEM_KIND_MISMATCH",
                          [item["code"] for item in wrong["findings"]])
            element["kind"] = "list_item"
            fixed = audit_document(outcome, [element], sidecar, folder)
            self.assertNotIn("PICTURE_LIST_ITEM_KIND_MISMATCH",
                             [item["code"] for item in fixed["findings"]])

    def test_corrupt_ocr_and_footer_urls_are_not_source_loss(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            (folder / "figures").mkdir()
            buffer = io.BytesIO()
            Image.new("RGB", (20, 20), "white").save(buffer, format="PNG")
            data = buffer.getvalue()
            (folder / "figures" / "figure.png").write_bytes(data)
            values = ["“FT voddns à] 2219281007 é 23/qOuId Un ZaQUCoUa SOA",
                      "Suivez Tracfin sur",
                      "www.economie.gouv.fr/tracfin www.linkedin.com/company/tracfin"]
            nodes = [{"self_ref": f"#/texts/{index}", "label": "text", "text": value,
                      "content_layer": "body", "parent": {"$ref": f"#/pictures/{0 if index == 0 else 1}"},
                      "prov": [{"page_no": 63 if index == 0 else 82,
                                "bbox": {"l": 10 + index, "r": 90,
                                         "t": 29 if index < 2 else 20,
                                         "b": 20 if index < 2 else 11,
                                         "coord_origin": "BOTTOMLEFT"}}]}
                     for index, value in enumerate(values)]
            pictures = [{"self_ref": f"#/pictures/{index}",
                         "prov": [{"page_no": page, "bbox": {"l": 10, "t": 90,
                                                               "r": 90, "b": 10,
                                                               "coord_origin": "BOTTOMLEFT"}}],
                         "children": [{"$ref": node["self_ref"]} for node in
                                      (nodes[:1] if index == 0 else nodes[1:])]}
                        for index, page in enumerate((63, 82))]
            native = {"pages": {str(page): {"size": {"width": 100, "height": 100}}
                                for page in (63, 82)},
                      "pictures": pictures, "texts": nodes, "groups": [], "tables": []}
            warnings = [{"code": "excluded_picture_corrupt_ocr", "picture_ref": "#/pictures/0",
                         "count": 1, "refs": ["#/texts/0"]},
                        {"code": "excluded_picture_furniture", "picture_ref": "#/pictures/1",
                         "count": 2, "refs": ["#/texts/1", "#/texts/2"]}]
            (folder / "docling.json").write_text(json.dumps(native))
            (folder / "parse.json").write_text(json.dumps({"warnings": warnings}))
            elements = [{"id": f"figure-{index}", "kind": "figure", "ordinal": index,
                         "text": "", "location": {"page": page, "bbox": [10, 10, 90, 90]}}
                        for index, page in enumerate((63, 82))]
            asset = {"path": "figures/figure.png", "media_type": "image/png",
                     "sha256": hashlib.sha256(data).hexdigest(),
                     "representation_role": "authoritative_visual"}
            sidecar = [{"element_id": f"figure-{index}", "docling_ref": f"#/pictures/{index}",
                        "evidence_role": "structural", "parent_element_id": None,
                        "asset": asset} for index in (0, 1)]
            result = audit_document({"manifest_id": "sample", "status": "parsed",
                                     "warnings": warnings}, elements, sidecar, folder)
            self.assertEqual(result["metrics"]["source_text_lost"], 0)
            self.assertEqual(result["metrics"]["excluded_corrupt_ocr_nodes"], 1)
            self.assertEqual(result["metrics"]["excluded_contact_furniture_nodes"], 2)
            self.assertFalse(any(item["severity"] == "error" for item in result["findings"]))

    def test_short_picture_text_exclusion_is_not_a_clean_audit(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            values = ["date et heure de l'opération ;", "Je", "joins", "obligatoirement",
                      "le", "formulaire", "de", "désignation.", "|"]
            nodes = [{"self_ref": f"#/texts/{index}", "label": "list_item" if index == 0 else "text",
                      "text": value, "content_layer": "body",
                      "parent": {"$ref": "#/pictures/0"},
                      "prov": [{"page_no": 1, "bbox": {"l": 10 + 12 * index,
                                                          "r": 20 + 12 * index,
                                                          "t": 100 if index == 0 else 80,
                                                          "b": 90 if index == 0 else 70}}]}
                     for index, value in enumerate(values)]
            native = {"texts": nodes, "groups": [], "tables": [], "pictures": [{
                "self_ref": "#/pictures/0", "prov": [{"page_no": 1}],
                "children": [{"$ref": node["self_ref"]} for node in nodes]}]}
            warning = {"code": "excluded_picture_ocr_fragments", "picture_ref": "#/pictures/0",
                       "count": len(nodes), "refs": [node["self_ref"] for node in nodes]}
            (folder / "docling.json").write_text(json.dumps(native))
            (folder / "parse.json").write_text(json.dumps({"warnings": [warning]}))
            elements = [{"id": "picture", "kind": "figure", "ordinal": 0,
                         "text": "", "location": {"page": 1}}]
            sidecar = [{"element_id": "picture", "docling_ref": "#/pictures/0",
                        "parent_element_id": None, "evidence_role": "structural"}]
            outcome = {"manifest_id": "sample", "status": "parsed", "warnings": [warning]}
            missing = audit_document(outcome, elements, sidecar, folder)
            self.assertGreater(missing["metrics"]["picture_short_text_lost"], 0)
            self.assertIn("PICTURE_SHORT_TEXT_LOST", [item["code"] for item in missing["findings"]])
            self.assertIn("STATUS_COMPLETENESS_MISMATCH",
                          [item["code"] for item in missing["findings"]])
            self.assertGreater(missing["metrics"]["picture_justified_exclusions"], 0)
            elements.append({"id": "short", "kind": "list_item", "ordinal": 1,
                             "text": values[0], "location": {"page": 1}})
            sidecar.append({"element_id": "short", "docling_ref": "#/texts/0",
                            "parent_element_id": "picture", "evidence_role": "authoritative"})
            improved = audit_document(outcome, elements, sidecar, folder)
            self.assertLess(improved["metrics"]["picture_short_text_lost"],
                            missing["metrics"]["picture_short_text_lost"])

    def test_potential_picture_exclusion_is_diagnosed_separately(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            native = {"texts": [{"self_ref": "#/texts/0", "label": "text",
                                  "text": "Alpha Beta Gamma", "content_layer": "body",
                                  "parent": {"$ref": "#/pictures/0"},
                                  "prov": [{"page_no": 1}]}],
                      "pictures": [{"self_ref": "#/pictures/0", "prov": [{"page_no": 1}],
                                    "children": [{"$ref": "#/texts/0"}]}], "tables": []}
            warning = {"code": "excluded_figure_text_uncertain", "picture_ref": "#/pictures/0",
                       "count": 1, "refs": ["#/texts/0"]}
            (folder / "docling.json").write_text(json.dumps(native))
            (folder / "parse.json").write_text(json.dumps({"warnings": [warning]}))
            elements = [{"id": "picture", "kind": "figure", "ordinal": 0,
                         "text": "", "location": {"page": 1}}]
            sidecar = [{"element_id": "picture", "docling_ref": "#/pictures/0",
                        "parent_element_id": None, "evidence_role": "structural"}]
            result = audit_document({"manifest_id": "sample", "status": "parsed",
                                     "warnings": [warning]}, elements, sidecar, folder)
            self.assertEqual(result["metrics"]["picture_justified_exclusions"], 1)
            self.assertEqual(result["metrics"]["picture_short_text_lost"], 0)
            self.assertEqual(result["findings"][0]["severity"], "warning")
            wrong_code = {**warning, "code": "excluded_picture_ocr_fragments"}
            (folder / "parse.json").write_text(json.dumps({"warnings": [wrong_code]}))
            mismatched = audit_document({"manifest_id": "sample", "status": "parsed",
                                         "warnings": [wrong_code]}, elements, sidecar, folder)
            self.assertIn("MISCLASSIFIED_PICTURE_EXCLUSION",
                          [item["code"] for item in mismatched["findings"]])
            (folder / "parse.json").write_text(json.dumps({"warnings": [warning]}))
            wrong_status = audit_document({"manifest_id": "sample", "status": "partial",
                                           "warnings": [warning]}, elements, sidecar, folder)
            self.assertIn("STATUS_COMPLETENESS_MISMATCH",
                          [item["code"] for item in wrong_status["findings"]])

    def test_t_page_37_short_amount_phrase_cannot_be_warned_away(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            texts = ["Achat d'un bien", "immobilier pour 1,5 M€"]
            nodes = [{"self_ref": f"#/texts/{index}", "label": "text", "text": value,
                      "content_layer": "body", "parent": {"$ref": "#/pictures/0"},
                      "prov": [{"page_no": 37, "bbox": {"l": left, "r": right,
                                                           "t": top, "b": bottom}}]}
                     for index, (value, left, right, top, bottom) in enumerate([
                         (texts[0], 282, 344, 214, 207),
                         (texts[1], 267, 356, 205, 198)])]
            native = {"texts": nodes, "groups": [], "tables": [], "pictures": [{
                "self_ref": "#/pictures/0", "prov": [{"page_no": 37}],
                "children": [{"$ref": node["self_ref"]} for node in nodes]}]}
            warning = {"code": "excluded_picture_ocr_fragments", "picture_ref": "#/pictures/0",
                       "count": 2, "refs": [node["self_ref"] for node in nodes]}
            (folder / "docling.json").write_text(json.dumps(native))
            (folder / "parse.json").write_text(json.dumps({"warnings": [warning]}))
            elements = [{"id": "picture", "kind": "figure", "ordinal": 0,
                         "text": "", "location": {"page": 37}}]
            sidecar = [{"element_id": "picture", "docling_ref": "#/pictures/0",
                        "parent_element_id": None, "evidence_role": "structural"}]
            result = audit_document({"manifest_id": "T", "status": "parsed",
                                     "warnings": [warning]}, elements, sidecar, folder)
            self.assertEqual(result["metrics"]["picture_short_text_lost"], 2)
            self.assertIn("PICTURE_SHORT_TEXT_LOST", [item["code"] for item in result["findings"]])

    def test_multi_provenance_requires_charspan_split(self):
        source = "The first substantive passage continues into a second substantive passage."
        cut = source.index("continues")
        node = {"self_ref": "#/texts/0", "label": "text", "content_layer": "body",
                "text": source, "parent": {"$ref": "#/body"},
                "prov": [{"page_no": 1, "charspan": [0, cut]},
                         {"page_no": 2, "charspan": [cut, len(source)]}]}
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            (folder / "docling.json").write_text(json.dumps({"texts": [node], "pictures": [],
                                                                "tables": []}))
            (folder / "parse.json").write_text('{"warnings": []}')
            elements = [{"id": str(page), "kind": "paragraph", "ordinal": page - 1,
                         "text": source, "location": {"page": page}} for page in (1, 2)]
            sidecar = [{"element_id": str(page), "docling_ref": "#/texts/0",
                        "parent_element_id": None} for page in (1, 2)]
            outcome = {"manifest_id": "sample", "status": "parsed", "warnings": []}
            duplicated = audit_document(outcome, elements, sidecar, folder)
            self.assertEqual(duplicated["metrics"]["multi_page_duplicated"], 1)
            self.assertEqual(duplicated["metrics"]["source_text_lost"], 0)
            for element, part in zip(elements, node["prov"]):
                element["text"] = source[slice(*part["charspan"])]
            split = audit_document(outcome, elements, sidecar, folder)
            self.assertEqual(split["metrics"]["multi_page_duplicated"], 0)
            self.assertFalse(any(item["code"] == "MULTI_PROVENANCE_CHARSPAN_MISMATCH"
                                 for item in split["findings"]))

    def test_joined_note_checks_every_declared_native_fragment_on_its_page(self):
        source = '112 A substantive note begins here. The body continues on another page.'
        cut = source.index('The body')
        continuation = 'A substantive continuation completes the first page note.'
        native = {'texts': [
            {'self_ref': '#/texts/0', 'label': 'text', 'text': source,
             'prov': [{'page_no': 1, 'charspan': [0, cut]},
                      {'page_no': 2, 'charspan': [cut, len(source)]}]},
            {'self_ref': '#/texts/1', 'label': 'footnote', 'text': continuation,
             'prov': [{'page_no': 1, 'charspan': [0, len(continuation)]}]}],
            'pictures': [], 'tables': []}
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            (folder / 'docling.json').write_text(json.dumps(native))
            (folder / 'parse.json').write_text('{"warnings": []}')
            elements = [
                {'id': 'note', 'kind': 'footnote', 'ordinal': 0,
                 'text': source[:cut] + '\n' + continuation, 'location': {'page': 1}},
                {'id': 'body', 'kind': 'paragraph', 'ordinal': 1,
                 'text': source[cut:], 'location': {'page': 2}}]
            sidecar = [
                {'element_id': 'note', 'docling_ref': '#/texts/0',
                 'docling_refs': ['#/texts/0', '#/texts/1'], 'layout_repair': 'bottom_note_run'},
                {'element_id': 'body', 'docling_ref': '#/texts/0'}]
            outcome = {'manifest_id': 'fixture', 'status': 'parsed', 'warnings': []}
            valid = audit_document(outcome, elements, sidecar, folder)
            self.assertNotIn('MULTI_PROVENANCE_CHARSPAN_MISMATCH', [f['code'] for f in valid['findings']])
            elements[0]['text'] += ' Invented extra words.'
            changed = audit_document(outcome, elements, sidecar, folder)
            self.assertIn('MULTI_PROVENANCE_CHARSPAN_MISMATCH', [f['code'] for f in changed['findings']])
            elements[0]['text'] = source[:cut]
            missing = audit_document(outcome, elements, sidecar, folder)
            self.assertIn('MULTI_PROVENANCE_CHARSPAN_MISMATCH', [f['code'] for f in missing['findings']])

    def test_missing_picture_text_and_table_redundancy_are_distinct(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            source = "Substantive words inside the picture are not represented anywhere else."
            native = {"texts": [{"self_ref": "#/texts/0", "label": "text", "text": source,
                                  "content_layer": "body", "parent": {"$ref": "#/pictures/0"},
                                  "prov": [{"page_no": 1, "charspan": [0, len(source)]}]}],
                      "pictures": [{"self_ref": "#/pictures/0", "prov": [{"page_no": 1}],
                                    "children": [{"$ref": "#/texts/0"}]}],
                      "tables": []}
            (folder / "docling.json").write_text(json.dumps(native))
            (folder / "parse.json").write_text('{"warnings": []}')
            elements = [{"id": "table", "kind": "table", "ordinal": 0,
                         "text": "| Substantive repeated table cell words here |",
                         "location": {"page": 1}},
                        {"id": "cell", "kind": "table_cell", "ordinal": 1,
                         "text": "Substantive repeated table cell words here",
                         "location": {"page": 1}},
                        {"id": "picture", "kind": "figure", "ordinal": 2,
                         "text": "", "location": {"page": 1}}]
            sidecar = [{"element_id": "table", "parent_element_id": None,
                        "evidence_role": "authoritative"},
                       {"element_id": "cell", "parent_element_id": "table",
                        "evidence_role": "structural"},
                       {"element_id": "picture", "parent_element_id": None,
                        "docling_ref": "#/pictures/0", "evidence_role": "structural"}]
            result = audit_document({"manifest_id": "sample", "status": "parsed",
                                     "warnings": []}, elements, sidecar, folder)
            self.assertEqual(result["metrics"]["picture_text_lost"], 1)
            self.assertEqual(result["metrics"]["table_duplication_risk"], 0)
            self.assertEqual(result["metrics"]["duplicate_content_groups"], 0)
            sidecar[1]["evidence_role"] = "authoritative"
            invalid = audit_document({"manifest_id": "sample", "status": "parsed",
                                      "warnings": []}, elements, sidecar, folder)
            self.assertEqual(invalid["metrics"]["table_duplication_risk"], 1)
            self.assertIn("INVALID_EVIDENCE_ROLE", [item["code"] for item in invalid["findings"]])

    def test_newest_matching_outcome_selected_per_source(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            artifact = folder / "document"
            artifact.mkdir()
            (artifact / "artifact-bundle.json").write_text('{"elements": [{"id": "one"}]}')
            (artifact / "parse.json").write_text('{"elements": []}')
            for started, count, filename in (("2026-01-01", 1, "report-old.json"),
                                             ("2026-01-02", 2, "report.json")):
                report = {"started_at": started, "run_id": started,
                          "outcomes": [{"manifest_id": "sample", "status": "parsed",
                                        "element_count": count, "output": str(artifact)}]}
                (folder / filename).write_text(json.dumps(report))
            self.assertEqual(select_outcomes(folder)[0]["_run_id"], "2026-01-01")
            newer = folder / "newer"
            newer.mkdir()
            (newer / "report.json").write_text(json.dumps({
                "started_at": "2026-01-03", "run_id": "2026-01-03",
                "outcomes": [{"manifest_id": "sample", "status": "parsed",
                              "element_count": 1, "output": str(artifact)}]}))
            self.assertEqual(select_outcomes(folder, [newer])[0]["_run_id"], "2026-01-03")

    def test_empty_docling_table_with_diagnostic_is_not_semantic_content(self):
        warning = {"code": "empty_docling_table", "ref": "#/tables/0",
                   "message": "table has no text or cells"}
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            (folder / "docling.json").write_text(json.dumps({"texts": [], "pictures": [],
                "tables": [{"self_ref": "#/tables/0", "prov": [{"page_no": 1}],
                            "data": {"table_cells": []}}]}))
            (folder / "parse.json").write_text(json.dumps({"warnings": [warning]}))
            result = audit_document({"manifest_id": "sample", "status": "parsed",
                                     "warnings": [warning]}, [], [], folder)
            self.assertEqual(result["metrics"]["empty_semantic_elements"], 0)
            self.assertEqual(result["metrics"]["unsupported_structures"], 0)
            self.assertEqual([(item["code"], item["severity"]) for item in result["findings"]],
                             [("PARSER_WARNING", "warning")])


if __name__ == "__main__":
    unittest.main()
