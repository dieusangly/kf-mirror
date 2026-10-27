"""Structure-preserving chunk boundaries and source-faithful provenance."""
import copy
import json
import unittest
from knowledge_foundry.chunking import ROOT, _chunk_document, clean, digest, settings_from_config, token_count


class ChunkingTests(unittest.TestCase):
    def test_fitting_cross_page_sentence_is_emitted_once_with_both_source_spans(self):
        chunks = self._small_document([
            ('heading', 'Rule', 1, ['Rule']),
            ('paragraph', 'A previous independent rule ' + 'detail ' * 20 + '.', 1, ['Rule']),
            ('list_item', '10. The person does not qualify when the', 1, ['Rule']),
            ('footnote', '2 A separate note.', 1, []),
            ('paragraph', 'conditions are absent; apply ordinary measures.', 2, ['Rule']),
        ], 48)
        complete = [c for c in chunks if 'does not qualify' in c['text']]
        self.assertEqual(len(complete), 1)
        self.assertIn('when the conditions are absent', complete[0]['text'])
        self.assertNotIn('separate note', complete[0]['text'])
        self.assertEqual(sum('span' in r and r['element_id'] in {'el:' + digest('2'), 'el:' + digest('4')}
                             for r in complete[0]['evidence']), 2)

    def test_short_exception_label_attaches_to_governing_numbered_paragraph(self):
        chunks = self._small_document([
            ('list_item', 'a) Exceptions to additional measures:', 1, []),
            ('list_item', '15. The exception requires both conditions:', 1, []),
            ('list_item', '- no suspicion;', 1, []),
            ('list_item', '- a low-risk relationship.', 1, []),
        ], 64, {i: [x, 20+i*20, 500, 30+i*20] for i, x in enumerate([80, 40, 80, 80])})
        label = next(c for c in chunks if 'a) Exceptions' in c['text'])
        self.assertIn('15. The exception', label['text'])
        combined = '\n'.join(c['text'] for c in chunks)
        self.assertIn('no suspicion', combined)
        self.assertIn('low-risk relationship', combined)

    def test_nested_list_introduction_is_not_its_own_repeated_context(self):
        path = ['Equipment handling']
        lead = 'i) for a unit stored at low temperature, use this preparation:'
        chunks = self._small_document([
            ('heading', path[0], 1, path),
            ('paragraph', 'Choose equipment handling from these methods:', 1, path),
            ('list_item', '- move the unit indoors;', 1, path),
            ('paragraph', lead, 1, path),
            ('list_item', '- check the enclosure;', 1, path),
            ('list_item', '- inspect the sensor.', 1, path),
        ], 128)
        own = next(chunk for chunk in chunks if '- check the enclosure;' in chunk['text'])
        self.assertEqual(own['text'].count(lead), 1)
        lead_refs = [ref for ref in own['evidence']
                     if ref['element_id'] == 'el:' + digest('3')]
        self.assertEqual(len(lead_refs), 1)
        self.assertIn('span', lead_refs[0])

    def _small_document(self, rows, maximum=64, bboxes=None):
        bundle = json.loads((ROOT / "examples/artifact-bundle-html.json").read_text(encoding="utf-8"))
        bundle["elements"] = []
        details = []
        template = json.loads((ROOT / "examples/artifact-bundle-html.json").read_text(encoding="utf-8"))["elements"][0]
        for index, (kind, value, page, path) in enumerate(rows):
            element = copy.deepcopy(template)
            element.update(id="el:" + digest(str(index)), kind=kind, text=value,
                           ordinal=index, locator_key=f"pdf:p{page}:block-{index}",
                           revision=digest(kind + "\n" + value),
                           location={"format": "pdf", "page": page, "label": f"block {index}"})
            bundle["elements"].append(element)
            if bboxes and index in bboxes:
                element['location']['bbox'] = bboxes[index]
            details.append({"element_id": element["id"], "evidence_role": "authoritative",
                            "heading_path": path})
        return _chunk_document(bundle, {"elements": details}, "build:test", {"max_tokens": maximum})[0]

    def test_parenthesized_peer_definition_closes_nested_scope(self):
        chunks = self._small_document([
            ('list_item', '(36) associate means:', 1, []),
            ('list_item', '(a) a joint owner ' + 'detail ' * 20 + ';', 1, []),
            ('list_item', '(b) a business partner;', 1, []),
            ('list_item', '(37) manager means a separate office.', 1, []),
        ], 40)
        peer = next(c for c in chunks if '(37)' in c['text'])
        self.assertNotIn('associate means', peer['text'])
        self.assertTrue(any('associate means' in c['text'] and '(b)' in c['text'] for c in chunks))

    def test_nested_list_restores_outer_parent_using_indent_not_roman_i(self):
        chunks = self._small_document([
            ('list_item', '(6) institution means:', 1, []),
            ('list_item', '(e) a fund in particular:', 1, []),
            ('list_item', '(i) a first investment scheme;', 1, []),
            ('footnote', '1 Detached note.', 1, []),
            ('list_item', '(ii) a second investment scheme;', 2, []),
            ('list_item', '(f) another institution;', 2, []),
            ('list_item', '(i) a service provider;', 2, []),
            ('list_item', '(7) independent definition.', 2, []),
        ], 32, {i: [x, 50 + i * 20, 500, 60 + i * 20]
                for i, x in enumerate([60, 80, 100, 60, 100, 80, 80, 60])})
        nested = next(c for c in chunks if '(ii)' in c['text'])
        self.assertIn('institution means', nested['text'])
        self.assertIn('fund in particular', nested['text'])
        sibling = next(c for c in chunks if 'service provider' in c['text'])
        self.assertIn('institution means', sibling['text'])
        self.assertIn('(6) institution means', sibling['text'])
        self.assertEqual(sibling['text'].count('(i) a service provider'), 1)
        peer = next(c for c in chunks if '(7)' in c['text'])
        self.assertNotIn('institution means', peer['text'])
