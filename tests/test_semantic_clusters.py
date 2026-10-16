import unittest
from knowledge_foundry.semantic_clusters import similarity_matrix, cluster_members, refine_clusters


class SemanticClustersTests(unittest.TestCase):
    def test_dog_d_cross_document_vectors_cluster_while_cat_stays_separate(self):
        vectors = {'dog-D-doc-A': [1, 0, 0], 'dog-D-doc-B': [.99, .05, 0], 'cat-doc-A': [0, 0, 1]}
        scores = similarity_matrix(vectors)
        expected = [['cat-doc-A'], ['dog-D-doc-A', 'dog-D-doc-B']]
        self.assertEqual(cluster_members(vectors, scores, .8), expected)
        self.assertEqual(cluster_members(reversed(vectors), scores, .8), expected)

    def test_all_members_constraint_prevents_similarity_chaining(self):
        scores = similarity_matrix({'A': [1, 0], 'B': [.8, .6], 'C': [.28, .96]})
        self.assertEqual(cluster_members(['C', 'B', 'A'], scores, .75), [['A', 'B'], ['C']])

    def test_size_partition_makes_progress_when_threshold_does_not(self):
        ids = ['A', 'B', 'C', 'D', 'E']
        scores = similarity_matrix({key: [1, 0] for key in ids})
        events = []
        groups, splits = refine_clusters([ids], scores, .8, lambda members: len(members) <= 2,
                                        on_progress=events.append)
        self.assertEqual(groups, [['A', 'B'], ['C', 'D'], ['E']])
        self.assertEqual(len(splits), 1)
        self.assertEqual([e['state'] for e in events],
                         ['checking_semantic_clusters_fit_claim_generation'] * 2 +
                         ['splitting_oversized_semantic_clusters'] * 2)
        self.assertEqual(events[0]['completed'], 0)
        self.assertEqual(events[1]['completed'], 1)
        self.assertEqual(events[2]['status'], 'started')
        self.assertEqual(events[3]['status'], 'complete')
        self.assertEqual(events[3]['resulting_clusters'], len(groups))
        self.assertEqual(splits[0]['reason'], 'claim_generation_input_budget_exceeded')
        self.assertEqual(sorted(x for group in groups for x in group), ids)

    def test_oversized_singleton_terminates_with_diagnostic(self):
        scores = similarity_matrix({'A': [1, 0]})
        with self.assertRaisesRegex(ValueError, 'oversized structural chunk: A'):
            refine_clusters([['A']], scores, .8, lambda _: False)
