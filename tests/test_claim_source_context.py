"""Regressions for claim-review findings; fixtures never approve corpus outputs."""

import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from claim_fixtures import bundle, fixture_settings, prepare_claim_context
from knowledge_foundry.claim_source_context import source_context_index
from knowledge_foundry.claim_generation_and_classification import (
    _candidate_input, _candidate_record, _evidence_choices, _evidence_accounting,
    _validate_candidate, _validate_candidate_choices, classify_claims, generate_claims,
)
from knowledge_foundry.structural_chunks import assemble_structural_chunks, stable_hash


class ClaimSourceContextTests(unittest.TestCase):
    def request(self, saved, primary=0):
        records, _ = assemble_structural_chunks(saved)
        own = [r for r in records if saved['chunks'][primary]['id'] in r['original_chunk_ids']]
        chunks = [c for c in saved['chunks'] if c['id'] in own[0]['original_chunk_ids']]
        elements = {e['id']: e for e in saved['elements']}
        index = source_context_index(saved)
        return (_evidence_choices(chunks, elements, own, index),
                json.loads(_candidate_input(chunks, elements, saved, own, index)), elements)

    def record(self, text, citations, choices):
        return _candidate_record('build:test', {'text': text, 'evidence': citations},
                                 {'method': 'manual', 'version': 'test'}, choices)

    def test_table_note_with_attached_marker_is_citable_without_merging(self):
        # Reproduces PPE annex p4: the rule/table and its exception were separate
        # chunks, and "État9" defeated the whitespace-based note hint.
        saved = bundle([
            ('table', 'Fonction | Personnes\nJuridiction | Membres du Conseil d’État9', 4),
            ('footnote', '9 À l’exception des conseillers en service extraordinaire sans fonctions juridictionnelles.', 4),
            ('footnote', '9 Note différente, page suivante.', 5)], [[0], [1], [2]])
        before = copy.deepcopy(saved)
        choices, payload, elements = self.request(saved)
        self.assertEqual(set(choices), {'E1', 'S1'})
        self.assertEqual(choices['S1']['reference'], saved['chunks'][1]['evidence'][0])
        self.assertEqual(choices['S1']['original_chunk_ids'], [saved['chunks'][1]['id']])
        self.assertIn('same_page_note', choices['S1']['support_reasons'])
        self.assertIn('sans fonctions juridictionnelles', payload['supporting_passages'][0]['text'])
        candidate = self.record('Les membres du Conseil d’État sont concernés, sauf les conseillers en service extraordinaire sans fonctions juridictionnelles.', ['E1', 'S1'], choices)
        _validate_candidate(candidate, 'build:test', {c['id']: c for c in saved['chunks']}, elements)
        _validate_candidate_choices(candidate, choices)
        self.assertEqual(saved, before)
        self.assertEqual(len(assemble_structural_chunks(saved)[0]), 3)
        reverse, _, _ = self.request(saved, primary=1)
        parent = next(v for k, v in reverse.items() if k.startswith('S'))
        self.assertEqual(parent['reference'], saved['chunks'][0]['evidence'][0])
        self.assertEqual(parent['support_reasons'], ['same_page_note_parent_context'])

    def test_following_exception_is_visible_with_exact_original_ownership(self):
        # Reproduces AMLR Art14(2)-(3): a later exception governs an earlier duty.
        saved = bundle([
            ('heading', 'Article 14 — Signalement interne', 50),
            ('list_item', '2. Les entités assujetties établissent des canaux de signalement internes.', 50),
            ('list_item', '3. Le paragraphe 2 ne s’applique pas aux personnes physiques ni aux entités dont les activités sont exercées par une seule personne physique.', 50),
            ('heading', 'Article 15 — Autre objet', 50),
            ('paragraph', 'Une autre règle sans rapport.', 50)], [[(0,), 1], [(0,), 2], [3, 4]])
        choices, payload, _ = self.request(saved)
        refs = {v['reference']['element_id'] for k, v in choices.items() if k.startswith('S')}
        self.assertIn(saved['elements'][2]['id'], refs)
        self.assertNotIn(saved['elements'][4]['id'], refs)
        self.assertIn('une seule personne physique', json.dumps(payload, ensure_ascii=False))
        self.assertNotIn('Une autre règle sans rapport', json.dumps(payload, ensure_ascii=False))

    def test_support_does_not_promote_spanless_or_partial_context(self):
        saved = bundle([
            ('paragraph', 'Contexte sans propriétaire citable.', 1),
            ('paragraph', 'Première partie. Seconde partie non citable.', 1),
            ('paragraph', 'La règle principale.', 1)], [[(0,), (1,), 2], [1]])
        saved['chunks'][1]['evidence'][0]['span']['end'] = len('Première partie.')
        choices, payload, _ = self.request(saved)
        support = [v for k, v in choices.items() if k.startswith('S')]
        self.assertEqual(len(support), 1)
        self.assertEqual(support[0]['text'], 'Première partie.')
        self.assertTrue(payload['non_citable_context'])
        self.assertFalse(any(v['text'] == 'Contexte sans propriétaire citable.' for v in choices.values()))

    def test_support_only_claim_and_cross_request_evidence_are_rejected(self):
        saved = bundle([('paragraph', 'Une obligation.', 1),
                        ('footnote', '1 Une exception.', 1),
                        ('paragraph', 'Une autre source.', 8)], [[0], [1], [2]])
        choices, _, _ = self.request(saved)
        with self.assertRaisesRegex(ValueError, 'primary evidence'):
            self.record('Une exception isolée.', ['S1'], choices)
        candidate = self.record('Une obligation sous réserve.', ['E1', 'S1'], choices)
        unrelated = saved['chunks'][2]['evidence'][0]
        with self.assertRaisesRegex(ValueError, 'outside its request'):
            _validate_candidate_choices({**candidate, 'evidence': candidate['evidence'] + [unrelated]}, choices)
        # Primary E coverage counts its substantive rule AND supported exception;
        # S passages do not become a second extraction population.
        rows = _evidence_accounting({'claims': [{'text': candidate['text'], 'evidence': ['E1', 'S1']}],
            'coverage': [{'evidence_id': 'E1', 'status': 'represented', 'claim_indices': [0],
                          'reason': 'Obligation et exception conservées ensemble.'}]}, choices, [candidate])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['reference'], saved['chunks'][0]['evidence'][0])

    def test_same_page_number_in_another_document_does_not_supply_support(self):
        saved = bundle([('paragraph', 'La règle 1.', 2), ('footnote', '1 Une exception étrangère.', 2)], [[0], [1]])
        saved['elements'][1]['document_id'] = 'doc:other'
        saved['chunks'][1]['document_id'] = 'doc:other'
        saved['chunks'][1]['evidence'][0]['document_id'] = 'doc:other'
        choices, _, _ = self.request(saved)
        self.assertFalse(any(k.startswith('S') for k in choices))

    def test_subset_generation_support_survives_export_resume_and_classification(self):
        saved = bundle([('paragraph', 'Les membres du conseil9 sont concernés.', 4),
                        ('footnote', '9 Sauf les membres sans fonction juridictionnelle.', 4)], [[0], [1]])
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            chunks, context = root / 'chunks', root / 'context'
            chunks.mkdir()
            (chunks / 'artifact-bundle.json').write_text(json.dumps(saved))
            config = root / 'config.json'
            config.write_text(json.dumps(fixture_settings()))
            records, _ = assemble_structural_chunks(saved)
            embeddings = root / 'embeddings.json'
            embeddings.write_text(json.dumps({'embedding_vectors': {
                stable_hash(r['text']): ([1., 0.] if saved['chunks'][0]['id'] in r['original_chunk_ids'] else [-1., 0.])
                for r in records}}))
            prepare_claim_context(chunks, context, config, root / 'cache', embeddings)
            plan = json.loads((context / 'semantic-clusters.json').read_text())
            number = next(i for i, c in enumerate(plan, 1) if saved['chunks'][0]['id'] in c['original_chunk_ids'])
            text = 'Les membres du conseil sont concernés, sauf ceux sans fonction juridictionnelle.'
            fixture = root / 'fixture.json'
            fixture.write_text(json.dumps({'candidate_clusters': {saved['chunks'][0]['id']: {
                'claims': [{'text': text, 'evidence': ['E1', 'S1']}]}}, 'classifications': {text: None}}))
            generated = root / 'generated'
            with patch('knowledge_foundry.claim_generation_and_classification._write_json', side_effect=RuntimeError('export failure')):
                with self.assertRaisesRegex(RuntimeError, 'export failure'):
                    generate_claims(None, generated, config, fixture, context_dir=context, cluster_numbers=[number])
            report = generate_claims(None, generated, config, fixture, context_dir=context,
                                     cluster_numbers=[number], resume=True)
            self.assertEqual(report['status'], 'complete')
            self.assertEqual(report['compilation_scope']['selected_chunk_ids'], [saved['chunks'][0]['id']])
            self.assertEqual(report['coverage']['summary']['selected_chunks'], 1)
            self.assertEqual(report['evidence_accounting']['expected_passages'], 1)
            candidate = json.loads((generated / 'claim-candidates.jsonl').read_text())
            self.assertEqual(set(candidate['chunk_ids']), {c['id'] for c in saved['chunks']})
            # classify validates the saved candidate manifest/accounting/lineage;
            # no real model or claim-review evaluator is invoked.
            result = classify_claims(generated, root / 'classified', config, fixture_path=fixture)
            self.assertEqual(result['status'], 'complete')


if __name__ == '__main__':
    unittest.main()
