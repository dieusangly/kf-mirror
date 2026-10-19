"""Regression checks for saved context and claim generation/classification."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_claim_context import generate_claims, prepare_fixture, fixture_settings

from knowledge_foundry.claim_generation_and_classification import (
    ROOT, FixtureProvider, _candidate_record, _evidence_choices,
    _validate_candidate, canonical, classify_claims, digest,
)
from knowledge_foundry.chunking import build_collection
from knowledge_foundry.parsing import parse_collection
from knowledge_foundry.structural_chunks import assemble_structural_chunks
from scripts.evaluate_claims import evaluate as evaluate_compilation
from scripts.evaluate_parsing import evaluate as evaluate_parsing
from scripts.validate_artifact_examples import check


def generation_batches(chunks, elements, _legacy_size):
    records, _ = assemble_structural_chunks({'chunks': chunks, 'elements': list(elements.values())})
    by_id = {c['id']: c for c in chunks}
    return sorted(([by_id[cid] for cid in r['original_chunk_ids']] for r in records),
                  key=lambda group: min(c['sequence'] for c in group))


class CompilationContextTests(unittest.TestCase):
    def bundle(self, passages, groups):
        """Create saved exact spans; a tuple in a group denotes spanless context."""
        bundle = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
        bundle['claims'], bundle['topics'], bundle['elements'], bundle['chunks'] = [], [], [], []
        document = bundle['documents'][0]
        for index, (kind, text, page) in enumerate(passages):
            locator = f'pdf:p{page}:test-{index}'
            bundle['elements'].append({
                'schema_version': '1.0.0', 'id': 'el:' + digest(document['id'] + '\n' + locator),
                'document_id': document['id'], 'locator_key': locator,
                'revision': digest(kind + '\n' + text), 'kind': kind, 'ordinal': index,
                'text': text, 'location': {'format': 'pdf', 'page': page, 'label': f'test {index}'}})
        for group in groups:
            refs, texts = [], []
            for item in group:
                context = isinstance(item, tuple)
                element = bundle['elements'][item[0] if context else item]
                ref = {'source_id': document['source_id'], 'document_id': document['id'],
                       'element_id': element['id'], 'element_revision': element['revision']}
                if not context:
                    ref['span'] = {'start': 0, 'end': len(element['text'])}
                refs.append(ref)
                texts.append(element['text'])
            text = '\n\n'.join(texts)
            bundle['chunks'].append({
                'schema_version': '1.0.0', 'document_id': document['id'],
                'id': 'chunk:' + digest(document['id'] + '\ntest-v1\n' + canonical(refs) + '\n' + text),
                'build_id': bundle['builds'][0]['id'], 'chunker_version': 'test-v1',
                'sequence': len(bundle['chunks']), 'text': text, 'evidence': refs})
        bundle['builds'][0].update(output_chunk_ids=[c['id'] for c in bundle['chunks']],
                                   output_claim_ids=[], output_topic_ids=[])
        check(bundle)
        return bundle

    def test_inline_structural_text_preserves_order_context_and_citation_map(self):
        from knowledge_foundry.claim_generation_and_classification import _candidate_input
        bundle = self.bundle([
            ('paragraph', 'Context without an exact span.', 1),
            ('paragraph', 'Dogs must rest.', 1),
            ('footnote', 'Only when tired.', 1)], [[(0,), 1, 2]])
        elements = {e['id']: e for e in bundle['elements']}
        records, _ = assemble_structural_chunks(bundle)
        original = copy.deepcopy(bundle)
        payload = json.loads(_candidate_input(bundle['chunks'], elements, bundle, records))
        self.assertEqual(bundle, original)
        self.assertEqual(payload['structural_chunks'][0]['passage_ids'], ['C1', 'E1', 'E2'])
        self.assertEqual(payload['structural_chunks'][0]['text'],
                         '[C1] Contexte non citable\nContext without an exact span.\n\n'
                         '[E1]\nDogs must rest.\n\n[E2]\nOnly when tired.')
        self.assertEqual([r['id'] for r in payload['eligible_evidence']], ['E1', 'E2'])
        self.assertTrue(all('text' not in r for r in payload['eligible_evidence']))
        self.assertTrue(all('text' not in r for r in payload['original_chunks']))
        choices = self.choices(bundle)
        for citation in payload['eligible_evidence']:
            self.assertEqual(citation['reference'], choices[citation['id']]['reference'])
        self.assertEqual(payload['non_citable_context'][0]['id'], 'C1')
        from jsonschema import ValidationError
        with self.assertRaises(ValidationError):
            self.record('Context is not evidence.', ['C1'], choices)
        self.assertEqual(_candidate_input(bundle['chunks'], elements, bundle, records),
                         _candidate_input(bundle['chunks'], elements, bundle, records))

    def test_inline_citation_map_retains_passage_footnote_links(self):
        from knowledge_foundry.claim_generation_and_classification import _candidate_input
        bundle = self.exception_bundle()
        elements = {e['id']: e for e in bundle['elements']}
        records, _ = assemble_structural_chunks(bundle)
        payload = json.loads(_candidate_input(bundle['chunks'], elements, bundle, records))
        for entry in payload['eligible_evidence']:
            expected = {k: v for k, v in self.choices(bundle)[entry['id']].items()
                        if k not in {'text', 'chunk_id'}}
            self.assertEqual(entry, {'id': entry['id'], **expected})
        self.assertTrue(any(e.get('footnote_evidence') for e in payload['eligible_evidence']))

    def test_citable_text_never_resolves_to_context_even_when_context_comes_first(self):
        from knowledge_foundry.claim_generation_and_classification import _candidate_input
        bundle = self.bundle([
            ('paragraph', 'Operators must perform both checks:', 1),
            ('list_item', '- Inspect the sensor.', 1)], [[(0,), 1], [0]])
        elements = {e['id']: e for e in bundle['elements']}
        records, _ = assemble_structural_chunks(bundle)
        before = copy.deepcopy(bundle)
        for reverse in [False, True]:
            with self.subTest(reverse=reverse):
                ordered = copy.deepcopy(records)
                ordered[0]['evidence_memberships'].sort(
                    key=lambda m: ('span' in m['reference']), reverse=reverse)
                payload = json.loads(_candidate_input(bundle['chunks'], elements, bundle, ordered))
                text = '\n'.join(r['text'] for r in payload['structural_chunks'])
                label = next(e['id'] for e in payload['eligible_evidence']
                             if e['reference']['element_id'] == bundle['elements'][0]['id'])
                self.assertIn(f'[{label}]\nOperators must perform both checks:', text)
                self.assertNotIn(f'[{label}] Voir [C', text)
                self.assertEqual(text.count('Operators must perform both checks:'), 1)
                self.assertTrue(payload['non_citable_context'])
        self.assertEqual(bundle, before)

    def test_partial_evidence_does_not_promote_full_context(self):
        from knowledge_foundry.claim_generation_and_classification import _candidate_input
        bundle = self.bundle([('paragraph', 'Check sensors. Replace defective units.', 1)], [[(0,), 0]])
        bundle['chunks'][0]['evidence'][1]['span']['end'] = len('Check sensors.')
        elements = {e['id']: e for e in bundle['elements']}
        records, _ = assemble_structural_chunks(bundle)
        payload = json.loads(_candidate_input(bundle['chunks'], elements, bundle, records))
        text = '\n'.join(r['text'] for r in payload['structural_chunks'])
        self.assertIn('[C1] Contexte non citable\nCheck sensors. Replace defective units.', text)
        self.assertIn('[E1]\nCheck sensors.', text)
        self.assertEqual(len(payload['eligible_evidence']), 1)

    def test_shared_passages_render_once_but_distinct_locations_retain_equal_text(self):
        from knowledge_foundry.claim_generation_and_classification import _candidate_input
        bundle = self.bundle([('paragraph', 'Identical wording.', 1),
                              ('paragraph', 'Identical wording.', 2)], [[0], [1]])
        elements = {e['id']: e for e in bundle['elements']}
        records, _ = assemble_structural_chunks(bundle)
        # Explicit memberships let this exercise shared references across records,
        # independently of assembly's choice to join fragments of one element.
        records[1]['evidence_memberships'].insert(0, copy.deepcopy(records[0]['evidence_memberships'][0]))
        payload = json.loads(_candidate_input(bundle['chunks'], elements, bundle, records))
        text = '\n'.join(r['text'] for r in payload['structural_chunks'])
        self.assertEqual(text.count('Identical wording.'), 2)
        self.assertIn('même passage source', text)
        self.assertEqual(len(payload['eligible_evidence']), 2)
        self.assertEqual(sum(len(r['passage_ids']) for r in payload['structural_chunks']), 3)

    def exception_bundle(self):
        return self.bundle([
            ('paragraph', "Les organismes financiers 17 doivent prévoir une procédure d'escalade.", 2),
            ('footnote', "17 À l'exception des changeurs manuels", 2),
            ('footnote', "17 À l'exception des entreprises étrangères", 3)], [[0], [1], [2]])

    def choices(self, bundle):
        return _evidence_choices(bundle['chunks'], {e['id']: e for e in bundle['elements']})

    def record(self, text, evidence, choices):
        return _candidate_record('build:test', {'text': text, 'evidence': evidence},
                                 {'method': 'manual', 'version': 'test'}, choices)

    def test_same_page_exception_is_grouped_without_mutating_chunks(self):
        bundle = self.exception_bundle()
        original = copy.deepcopy(bundle)
        batches = generation_batches(bundle['chunks'], {e['id']: e for e in bundle['elements']}, 1)
        self.assertEqual([[c['id'] for c in group] for group in batches],
                         [[c['id'] for c in bundle['chunks'][:2]], [bundle['chunks'][2]['id']]])
        self.assertEqual(bundle, original)
        choices = self.choices(bundle)
        self.assertEqual(choices['E1']['footnote_evidence'], ['E2'])
        self.assertEqual(choices['E2']['parent_evidence'], ['E1'])

    def test_note_numbers_do_not_match_article_numbers_or_another_page(self):
        bundle = self.bundle([
            ('paragraph', "L'article R. 561-23 et l'article 23.1 encadrent les mesures.", 24),
            ('paragraph', "La décision 23 est prise par un membre de l'organe exécutif.", 25),
            ('footnote', "23 Ou toute personne habilitée à cet effet par l'organe exécutif.", 24)],
            [[0], [1], [2]])
        choices = self.choices(bundle)
        self.assertTrue(all('footnote_evidence' not in item for item in choices.values()))

    def test_complete_tables_on_adjacent_pages_need_not_share_a_batch(self):
        bundle = self.bundle([('table', 'Obligation | Contrôler les opérations.', 1),
                              ('table', 'Autre obligation | Former les salariés.', 2)], [[0], [1]])
        self.assertEqual(len(generation_batches(bundle['chunks'], {e['id']: e for e in bundle['elements']}, 1)), 2)

    def test_list_context_grouping_uses_structure_without_french_trigger_phrases(self):
        bundle = self.bundle([
            ('list_item', '3) When a sensor fails:', 1),
            ('paragraph', 'Choose at least one of these responses:', 1),
            ('list_item', 'a. Replace the sensor;', 1),
            ('list_item', 'b. Suspend observations.', 2)], [[0], [1, 2], [3]])
        original = copy.deepcopy(bundle)
        batches = generation_batches(bundle['chunks'], {e['id']: e for e in bundle['elements']}, 1)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 3)
        self.assertEqual(bundle, original)

    def test_repeated_paragraph_context_uses_its_original_citable_chunk(self):
        bundle = self.bundle([
            ('paragraph', "Lorsque le client est non pas le placement", 7),
            ('paragraph', "collectif mais la société de gestion, les obligations de vigilance portent sur cette société.", 8)],
            [[0], [(0,), 1]])
        elements = {e['id']: e for e in bundle['elements']}
        self.assertEqual(len(generation_batches(bundle['chunks'], elements, 1)), 1)
        choices = self.choices(bundle)
        self.assertEqual(len(choices), 2)  # No invented span for the repeated context.
        record = self.record("Lorsque la société de gestion est le client, les obligations de vigilance portent sur elle.",
                             ['E1', 'E2'], choices)
        _validate_candidate(record, 'build:test', {c['id']: c for c in bundle['chunks']}, elements)

    def test_new_subject_runs_from_saved_html_to_custom_topic_without_pilot_landmarks(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root / 'observatory.html'
            source.write_text('<html><body><main><h1>Observatory maintenance</h1>'
                              '<p>Operators must calibrate the telescope annually.</p>'
                              '</main></body></html>')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'schema_version': '1.0.0', 'sources': [{
                'manifest_id': 'observatory', 'source_id': 'src:observatory',
                'title': 'Observatory maintenance', 'class': 'test', 'source_version': 'v1',
                'publisher_url': 'https://example.org/observatory', 'local_path': source.name,
                'retrieved_on': '2026-10-15', 'media_type': 'text/html',
                'size_bytes': source.stat().st_size, 'sha256': digest(source.read_text())}]}))
            pipeline = root / 'pipeline.json'
            pipeline.write_text(json.dumps({'root': '.', 'manifest_file': 'manifest.json',
                'manifest_ids': ['observatory'], 'parsing': {'ocr': {'mode': 'off'}},
                'chunking': {'max_tokens': 256}}))
            parsed, chunked = root / 'parsed', root / 'chunked'
            parsing = parse_collection(pipeline, parsed)
            self.assertEqual(parsing['outcomes'][0]['status'], 'parsed', parsing)
            audit = evaluate_parsing(parsed, source_root=root)
            self.assertEqual(audit['gate']['status'], 'pass', audit)
            self.assertEqual(audit['reference_status'], 'not_supplied')
            self.assertEqual(audit['totals']['passages_checked'], 0)
            reference = root / 'reference.json'
            landmarks = {'status': 'draft', 'passages': [{
                'source': 'observatory', 'text': 'Operators must calibrate the telescope annually.'}],
                'structure': [], 'reading_order': []}
            reference.write_text(json.dumps(landmarks))
            verified = evaluate_parsing(parsed, reference, source_root=root)
            self.assertEqual(verified['gate']['status'], 'pass', verified)
            self.assertEqual(verified['totals']['passages_checked'], 1)
            landmarks['passages'][0]['text'] = 'An absent maintenance requirement.'
            reference.write_text(json.dumps(landmarks))
            missing = evaluate_parsing(parsed, reference, source_root=root)
            self.assertEqual(missing['gate']['status'], 'fail')
            self.assertEqual(missing['corpus']['error_codes']['MISSING_PASSAGE'], 1)
            build_collection(pipeline, parsed, chunked)
            bundle = json.loads((chunked / 'artifact-bundle.json').read_text())
            generated, classified, taxonomy = self.compile_outcomes(root, bundle, [
                ('Operators must calibrate the telescope annually.', 'topic:equipment-maintenance')])
            evaluation = evaluate_compilation(generated, classified, manifest_path=manifest,
                                              taxonomy_path=taxonomy, source_root=root)
            self.assertEqual(evaluation['automatic_gate'], 'pass', evaluation['findings'])
            self.assertEqual(evaluation['review_gate'], 'pending')

    def compile_outcomes(self, root, bundle, outcomes):
        """Exercise exports and provider calls with a supplied, unfamiliar taxonomy."""
        chunked = root / 'chunked'
        chunked.mkdir(exist_ok=True)
        (chunked / 'artifact-bundle.json').write_text(json.dumps(bundle))
        settings = fixture_settings()
        settings['steps']['claim_generation']['retries'] = 1
        settings['steps']['topic_classification']['retries'] = 1
        config = root / 'claim-generation-and-classification.json'
        config.write_text(json.dumps(settings))
        taxonomy = root / 'topics.json'
        taxonomy.write_text(json.dumps({'taxonomy_version': 'observatory-v1', 'topics': [{
            'id': 'topic:equipment-maintenance', 'title': 'Equipment maintenance',
            'scope': 'Calibration, servicing and maintenance of scientific equipment.'}]}))
        choices = self.choices(bundle)
        candidates = [{'text': text, 'evidence': [next(
            label for label, passage in choices.items() if passage['text'] == text)]}
            for text, _ in outcomes]
        contexts = []

        class Provider(FixtureProvider):
            def candidates(self, *_args):
                return {'claims': candidates}

            def classify(self, context, supplied_taxonomy, *_args):
                contexts.append((context, supplied_taxonomy))
                return {'topic_id': dict(outcomes)[context['claim']]}

        generated, classified = root / 'generated', root / 'classified'
        with patch('knowledge_foundry.claim_generation_and_classification._provider', return_value=Provider({})):
            generation = generate_claims(chunked, generated, config)
            original = (generated / 'claim-candidates.jsonl').read_bytes()
            classification = classify_claims(generated, classified, config, taxonomy)
        self.assertEqual(generation['status'], 'complete')
        self.assertEqual(classification['status'], 'complete')
        self.assertEqual(generation['errors'], [])
        self.assertEqual(classification['errors'], [])
        self.assertEqual(len(contexts), len(outcomes))  # No keyword-driven retries.
        self.assertTrue(all(t == json.loads(taxonomy.read_text()) for _, t in contexts))
        self.assertEqual((generated / 'claim-candidates.jsonl').read_bytes(), original)
        self.assertEqual(len(classification['unclassified_candidate_ids']),
                         sum(topic is None for _, topic in outcomes))
        result = json.loads((classified / 'artifact-bundle.json').read_text())
        check(result)
        self.assertEqual({c['text']: c['topic_id'] for c in result['claims']},
                         {text: topic for text, topic in outcomes if topic is not None})
        return generated, classified, taxonomy

    def test_text_patterns_do_not_override_the_supplied_classification_outcome(self):
        outcomes = [
            ('Dans le cas n° 4, le dispositif permet à un technicien de calibrer le télescope.',
             'topic:equipment-maintenance'),
            ('Dans le cas n° 4, le report peut, par extension, s’appliquer à une catégorie d’opérations.',
             'topic:equipment-maintenance'),
            ('La déclaration de soupçon et le dépôt de plainte sont deux procédures indépendantes.', None),
            ('Les documents relatifs à l’identité sont conservés cinq ans.', None)]
        bundle = self.bundle([('paragraph', text, 1) for text, _ in outcomes],
                             [list(range(len(outcomes)))])
        with tempfile.TemporaryDirectory() as name:
            self.compile_outcomes(Path(name), bundle, outcomes)

