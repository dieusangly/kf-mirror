import copy
import unittest
from knowledge_foundry.structural_chunks import assemble_structural_chunks
import test_compilation_context as context_tests


class StructuralChunksTests(unittest.TestCase):
    def bundle(self, passages, groups):
        return context_tests.CompilationContextTests().bundle(passages, groups)

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
        self.assertEqual(links, [])

    def test_supported_table_continuation_records_heuristic_reason(self):
        bundle = self.bundle([('table', 'Action | suspend the', 1), ('table', ' | observations.', 2)], [[0], [1]])
        records, links = assemble_structural_chunks(bundle)
        self.assertEqual(len(records), 1)
        self.assertEqual(links[0]['reason'], 'adjacent_table_continuation')
        self.assertEqual(links[0]['strength'], 'heuristic')
