import copy
import unittest
from knowledge_foundry.structural_chunks import assemble_structural_chunks
import claim_fixtures as context_fixtures


class StructuralChunksTests(unittest.TestCase):
    def bundle(self, passages, groups):
        return context_fixtures.bundle(passages, groups)

    def test_shared_element_fragments_preserve_originals_and_deduplicate_only_exact_spans(self):
        bundle = self.bundle([('table', 'header | row', 1), ('paragraph', 'header | row', 2)], [[0], [1]])
        bundle['chunks'].insert(1, copy.deepcopy(bundle['chunks'][0]))
        # Original IDs must differ even when spans match (different saved chunk text).
        bundle['chunks'][1]['id'] += '-second'
        bundle['chunks'][1]['text'] += ' original rendering'
        before = copy.deepcopy(bundle)
        records, links = assemble_structural_chunks(bundle)
        self.assertEqual(bundle, before)
        self.assertEqual(len(records), 2)
        grouped = next(r for r in records if len(r['original_chunk_ids']) == 2)
        self.assertEqual(grouped['text'], 'header | row')
        self.assertEqual(len(grouped['evidence_memberships'][0]['original_chunk_ids']), 2)
        self.assertEqual(links[0]['strength'], 'identity')
        self.assertEqual(sorted(c for r in records for c in r['original_chunk_ids']), sorted(c['id'] for c in bundle['chunks']))

    def test_shared_heading_does_not_connect_unrelated_passages(self):
        bundle = self.bundle([('heading', 'Rules', 1), ('paragraph', 'Dogs must rest.', 1),
                              ('paragraph', 'Cats must eat.', 2)], [[(0,), 1], [(0,), 2]])
        records, links = assemble_structural_chunks(bundle)
        self.assertEqual(len(records), 2)
        self.assertEqual(links, [])

    def test_intervening_heading_stops_list_and_ambiguous_notes_do_not_link(self):
        bundle = self.bundle([('paragraph', 'Choose:', 1), ('heading', 'Unrelated', 1),
                              ('list_item', 'a. feed cats.', 1), ('paragraph', 'Dogs 17 rest.', 2),
                              ('footnote', '17 first note', 2), ('footnote', '17 second note', 2)],
                             [[0], [1], [2], [3], [4], [5]])
        records, links = assemble_structural_chunks(bundle)
        self.assertEqual(len(records), 6)
        self.assertTrue(all(link['reason'] == 'document_parent' and not link['merges'] for link in links))

    def test_supported_table_continuation_records_heuristic_reason(self):
        bundle = self.bundle([('table', 'Action | suspend the', 1), ('table', ' | observations.', 2)], [[0], [1]])
        records, links = assemble_structural_chunks(bundle)
        self.assertEqual(len(records), 1)
        self.assertEqual(links[0]['reason'], 'adjacent_table_continuation')
        self.assertEqual(links[0]['strength'], 'heuristic')

    def test_bulleted_and_numeric_lists_keep_their_governing_rule(self):
        for first, second in [('- Inspect the sensor;', '- Inspect the mount.'),
                              ('1) Inspect the sensor;', '2) Inspect the mount.')]:
            with self.subTest(first=first):
                bundle = self.bundle([('paragraph', 'Operators must perform these checks:', 1),
                                      ('list_item', first, 1), ('list_item', second, 2)], [[0], [1], [2]])
                records, links = assemble_structural_chunks(bundle)
                self.assertEqual(len(records), 3)
                self.assertTrue(all(not link['merges'] for link in links))
                lead = bundle['elements'][0]['id']
                children = [r for r in records if r['original_chunk_ids'] != [bundle['chunks'][0]['id']]]
                self.assertTrue(all(lead in r['context_element_ids'] for r in children))

    def test_list_does_not_cross_outdent_or_new_marker_family(self):
        for last in ['1) New independent rule.', '- Another bullet.']:
            bundle = self.bundle([('paragraph', 'Local alternatives:', 1),
                                  ('list_item', '- Inspect.', 1), ('list_item', last, 1)], [[0], [1], [2]])
            for index, e in enumerate(bundle['elements']):
                e['location']['bbox'] = [60 if index < 2 else 40, 40 + index * 20, 400, 50 + index * 20]
            records, _ = assemble_structural_chunks(bundle)
            self.assertEqual(len(records), 3)
            last_record = next(r for r in records if bundle['chunks'][2]['id'] in r['original_chunk_ids'])
            self.assertNotIn(bundle['elements'][0]['id'], last_record['context_element_ids'])

    def test_box_sentence_continuation_requires_adjacent_matching_geometry(self):
        for interrupted, shifted in [(False, False), (True, False), (False, True)]:
            with self.subTest(interrupted=interrupted, shifted=shifted):
                passages = [('table', 'Requirement | Operators must retain the', 1)]
                if interrupted:
                    passages.append(('heading', 'A different section', 1))
                passages.append(('paragraph', 'calibration record for each instrument.', 1))
                bundle = self.bundle(passages, [[i] for i in range(len(passages))])
                bundle['elements'][0]['location']['bbox'] = [40, 40, 400, 150]
                bundle['elements'][-1]['location']['bbox'] = [420 if shifted else 40, 155,
                                                           700 if shifted else 395, 165]
                records, links = assemble_structural_chunks(bundle)
                self.assertEqual(len(records), len(passages) if interrupted or shifted else 1)
                if not interrupted and not shifted:
                    self.assertEqual(links[0]['reason'], 'adjacent_sentence_continuation')

    def test_table_border_overlap_can_contain_a_continuation_but_prose_overlap_cannot(self):
        for kind, top, bottom, expected in [('table', 145, 155, 1),
                                             ('table', 130, 140, 2),
                                             ('paragraph', 145, 155, 2)]:
            with self.subTest(kind=kind, top=top):
                bundle = self.bundle([(kind, 'Operators must retain the', 1),
                                      ('paragraph', 'calibration record.', 1)], [[0], [1]])
                bundle['elements'][0]['location']['bbox'] = [40, 40, 400, 150]
                bundle['elements'][1]['location']['bbox'] = [40, top, 395, bottom]
                records, _ = assemble_structural_chunks(bundle)
                self.assertEqual(len(records), expected)
