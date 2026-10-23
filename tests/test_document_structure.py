"""Hierarchy regressions independent of optional saved pilot artifacts."""
import copy
import json
from pathlib import Path
import unittest

from knowledge_foundry.document_structure import (
    assemble_document_hierarchy, ancestor_ids, structure_snapshot,
)
from knowledge_foundry.structural_chunks import assemble_structural_chunks
from knowledge_foundry.claim_generation_and_classification import _candidate_input
import claim_fixtures as context_fixtures


from claim_fixtures import introduction_bundle


class DocumentStructureTests(unittest.TestCase):
    introduction = staticmethod(introduction_bundle)

    def test_introduction_paragraph_seven_and_sector_lists_have_explicit_parents(self):
        bundle, snapshot = self.introduction()
        before = copy.deepcopy(bundle)
        hierarchy = assemble_document_hierarchy(bundle, snapshot)
        by_id = {r['element_id']: r for r in hierarchy}
        ids = [e['id'] for e in bundle['elements']]
        for child, expected_parent in [(1, 0), (2, 0), (3, 2), (4, 3), (5, 3),
                                       (7, 2), (8, 7), (9, 7), (10, 0), (12, 11), (13, 12)]:
            self.assertEqual(by_id[ids[child]]['parent_element_id'], ids[expected_parent])
        self.assertIsNone(by_id[ids[6]]['parent_element_id'])
        self.assertEqual(ancestor_ids(ids[9], by_id), [ids[0], ids[2], ids[7]])
        records, links = assemble_structural_chunks(bundle, snapshot)
        self.assertEqual(len(records), len(bundle['chunks']))
        self.assertTrue(all(not r['merges'] for r in links))
        for index in (4, 5, 8, 9):
            record = next(r for r in records if bundle['chunks'][index]['id'] in r['original_chunk_ids'])
            self.assertIn(ids[2], record['context_element_ids'])
        payload = json.loads(_candidate_input(bundle['chunks'], {e['id']: e for e in bundle['elements']}, bundle, records))
        self.assertIn('7. Le cadre juridique', '\n'.join(r['text'] for r in payload['structural_chunks']))
        # Hierarchy context never invents source spans or original membership.
        self.assertEqual(bundle, before)

    def test_metadata_conflicting_with_document_order_is_rejected(self):
        bundle, snapshot = self.introduction()
        snapshot['elements'][0]['parent_element_id'] = bundle['elements'][-1]['id']
        with self.assertRaisesRegex(ValueError, 'parent must precede'):
            assemble_document_hierarchy(bundle, snapshot)

    def test_distant_content_does_not_inherit_a_numbered_rule(self):
        bundle = context_fixtures.bundle([('list_item', '7. A local rule:', 1),
                                                   ('list_item', '- Local alternative.', 1),
                                                   ('paragraph', 'Distant independent prose.', 9)], [[0], [1], [2]])
        hierarchy = {r['element_id']: r for r in assemble_document_hierarchy(bundle)}
        self.assertIsNone(hierarchy[bundle['elements'][2]['id']]['parent_element_id'])

    def test_split_list_request_retains_paragraph_seven_as_non_citable_context(self):
        bundle, snapshot = self.introduction()
        records, _ = assemble_structural_chunks(bundle, snapshot)
        chunk = bundle['chunks'][9]
        record = next(r for r in records if chunk['id'] in r['original_chunk_ids'])
        payload = json.loads(_candidate_input([chunk], {e['id']: e for e in bundle['elements']}, bundle, [record]))
        text = payload['structural_chunks'][0]['text']
        self.assertIn('7. Le cadre juridique', text)
        self.assertIn('Contexte non citable', text)
        self.assertEqual(len(payload['eligible_evidence']), 1)
        self.assertEqual(payload['eligible_evidence'][0]['reference']['element_id'], bundle['elements'][9]['id'])

    def test_transitive_shared_fragments_are_bounded_and_remote_context_does_not_merge(self):
        bundle = context_fixtures.bundle([('paragraph', 'Long shared paragraph.', 1),
                                                   ('paragraph', 'Remote text.', 10)], [[0], [(0,), 1]])
        first, remote = bundle['chunks']
        bundle['chunks'] = [copy.deepcopy(first) for _ in range(9)] + [remote]
        for i, chunk in enumerate(bundle['chunks']):
            chunk['id'] += f'-{i}'
            chunk['sequence'] = i
        records, links = assemble_structural_chunks(bundle)
        self.assertTrue(all(len(r['original_chunk_ids']) <= 4 for r in records))
        self.assertEqual(sum(len(r['original_chunk_ids']) for r in records), 10)
        self.assertTrue(any(not r['merges'] for r in links))
        self.assertFalse(any(r['reason'] == 'governing_context_reference' for r in links))

    def test_saved_acpr_introduction_when_available(self):
        root = Path('artifacts/parsed-20261017/documents')
        chunked = Path('artifacts/chunked-20261017/artifact-bundle.json')
        if not chunked.exists() or not root.exists():
            self.skipTest('optional saved ACPR pilot run unavailable')
        bundle = json.loads(chunked.read_text())
        rows = [r for path in sorted(root.glob('*/parse.json')) for r in json.loads(path.read_text())['elements']]
        hierarchy = {r['element_id']: r for r in assemble_document_hierarchy(bundle, structure_snapshot(rows))}
        elements = {e['id']: e for e in bundle['elements']}
        sectors = [e for e in elements.values() if e['ordinal'] < 100 and e['text'].startswith('Dans le secteur')]
        self.assertEqual(len(sectors), 2)
        for sector in sectors:
            parents = [elements[eid]['text'] for eid in ancestor_ids(sector['id'], hierarchy)]
            self.assertEqual(parents[0], 'INTRODUCTION')
            self.assertTrue(parents[-1].startswith('7. Le cadre juridique'))

        insurance = next(e['id'] for e in sectors if 'assurance' in e['text'])
        for e in elements.values():
            if e['document_id'] == elements[insurance]['document_id'] and e['ordinal'] in {107, 108, 109}:
                self.assertEqual(hierarchy[e['id']]['parent_element_id'], insurance)

    def test_unmarked_list_continuation_preserves_lead_across_page_notes(self):
        bundle = context_fixtures.bundle([
            ('heading', 'INTRODUCTION', 3), ('list_item', '7. These institutions:', 4),
            ('paragraph', 'Insurance:', 4), ('list_item', '- The retirement institutions governed by the', 4),
            ('footnote', '6 A detached note.', 4), ('list_item', 'supplementary retirement provisions;', 5),
            ('list_item', '- The insurance branches.', 5), ('list_item', '8. A new rule.', 5)], [[i] for i in range(8)])
        for i, e in enumerate(bundle['elements']):
            x = 60 if i in {2, 3, 6} else 75 if i == 5 else 40
            e['location']['bbox'] = [x, 30 + i * 20, 500, 40 + i * 20]
        hierarchy = {r['element_id']: r for r in assemble_document_hierarchy(bundle)}
        ids = [e['id'] for e in bundle['elements']]
        for i in (3, 5, 6):
            self.assertEqual(hierarchy[ids[i]]['parent_element_id'], ids[2])
        self.assertEqual(hierarchy[ids[5]]['basis'], 'local_list_continuation')
        self.assertEqual(hierarchy[ids[7]]['parent_element_id'], ids[0])

    def banner_bundle(self):
        passages = [('heading', 'Previous topic', 8), ('heading', 'Case n° 4: earlier case', 8),
                    ('heading', 'Criteria', 8), ('heading', 'Case n° 5: new case', 10),
                    ('paragraph', 'Operators must retain the report.', 10), ('heading', 'Criteria', 10),
                    ('heading', 'New topic', 10), ('heading', 'Keywords', 11),
                    ('heading', 'O Other topic', 12), ('heading', 'Case n° 13: another case', 12),
                    ('paragraph', 'A different case rule.', 12)]
        bundle = context_fixtures.bundle(passages,
                    [[i] if i != 4 else [(0,), i] for i in range(len(passages))])
        levels = [1, 2, 3, 2, None, 3, 1, 2, 2, 2, None]
        parents = [None, 0, 1, 0, 0, 3, None, 6, 6, 6, 9]
        boxes = {3: [30, 100, 470, 110], 4: [30, 130, 470, 145], 5: [30, 200, 200, 210],
                 6: [330, 60, 480, 80], 8: [40, 60, 480, 86], 9: [30, 110, 470, 120]}
        rows = []
        for i, e in enumerate(bundle['elements']):
            e['location']['bbox'] = boxes.get(i, [30, 40 + i * 15, 470, 50 + i * 15])
            rows.append({'element_id': e['id'], 'level': levels[i], 'docling_parent_ref': '#/body',
                         'parent_element_id': bundle['elements'][parents[i]]['id'] if parents[i] is not None else None})
        return bundle, structure_snapshot(rows)

    def test_page_banner_geometry_repairs_late_order_and_stale_topic_levels(self):
        bundle, snapshot = self.banner_bundle()
        hierarchy = {r['element_id']: r for r in assemble_document_hierarchy(bundle, snapshot)}
        ids = [e['id'] for e in bundle['elements']]
        self.assertEqual(hierarchy[ids[3]]['parent_element_id'], ids[6])
        self.assertEqual(hierarchy[ids[5]]['parent_element_id'], ids[3])
        self.assertEqual(hierarchy[ids[7]]['parent_element_id'], ids[3])
        self.assertIsNone(hierarchy[ids[8]]['parent_element_id'])
        self.assertEqual(hierarchy[ids[9]]['parent_element_id'], ids[8])
        self.assertEqual(hierarchy[ids[10]]['parent_element_id'], ids[9])

    def test_stale_original_heading_context_is_removed_only_from_derived_inputs(self):
        from knowledge_foundry.claim_generation_and_classification import _evidence_choices
        bundle, snapshot = self.banner_bundle()
        before = copy.deepcopy(bundle)
        records, _ = assemble_structural_chunks(bundle, snapshot)
        chunk = bundle['chunks'][4]
        record = next(r for r in records if chunk['id'] in r['original_chunk_ids'])
        self.assertNotIn('Previous topic', record['text'])
        elements = {e['id']: e for e in bundle['elements']}
        payload = json.loads(_candidate_input([chunk], elements, bundle, [record]))
        choices = _evidence_choices([chunk], elements, [record])
        self.assertEqual(set(choices), {row['id'] for row in payload['eligible_evidence']})
        self.assertTrue(all(row['reference']['element_id'] == bundle['elements'][4]['id']
                            for row in payload['eligible_evidence']))
        self.assertIn('New topic', payload['structural_chunks'][0]['text'])
        self.assertNotIn('Previous topic', payload['structural_chunks'][0]['text'])
        self.assertEqual(bundle, before)

    def test_boxed_caption_ends_prior_numbered_scope_without_merging_its_contents(self):
        bundle = context_fixtures.bundle([
            ('heading', 'Section', 12), ('list_item', '37. A preceding rule.', 13),
            ('paragraph', 'Encadré n° 5 : An independent discussion', 13),
            ('paragraph', 'The box explains its own subject.', 13),
            ('paragraph', 'The box continues on the following page.', 14),
            ('paragraph', 'The box ends here.', 15), ('heading', 'New section', 15),
            ('list_item', '38. A different rule.', 15)], [[i] for i in range(8)])
        hierarchy = {r['element_id']: r for r in assemble_document_hierarchy(bundle)}
        ids = [e['id'] for e in bundle['elements']]
        self.assertEqual(hierarchy[ids[2]]['parent_element_id'], ids[0])
        for i in (3, 4, 5):
            self.assertEqual(hierarchy[ids[i]]['parent_element_id'], ids[2])
        self.assertEqual(hierarchy[ids[7]]['parent_element_id'], ids[6])
        records, _ = assemble_structural_chunks(bundle)
        self.assertEqual(len(records), len(bundle['chunks']))
