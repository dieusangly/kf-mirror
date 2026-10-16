"""Frozen context integration tests; fixtures validate mechanics, not semantic quality."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from knowledge_foundry.claim_generation_and_classification import generate_claims as generate_prepared, ROOT
from knowledge_foundry.claim_context import prepare_claim_context, load_preparation
from knowledge_foundry.structural_chunks import assemble_structural_chunks, stable_hash


def fixture_settings():
    config = json.loads((ROOT / 'config/claim-generation-and-classification.json').read_text())
    config['steps']['claim_generation']['provider'] = 'fixture'
    config['steps']['topic_classification']['provider'] = 'fixture'
    config['pricing']['providers']['fixture'] = copy.deepcopy(config['pricing']['providers']['openai'])
    config['steps']['embeddings'].update(provider='fixture', dimensions=2, expected_dimensions=2, retries=0)
    return config


def prepare_fixture(chunked, config_path, on_progress=None):
    """Explicit test preparation shared by existing generation regression suites."""
    chunked, config_path = Path(chunked), Path(config_path)
    context = chunked.parent / ('context-' + stable_hash([str(chunked), str(config_path), (chunked / 'artifact-bundle.json').read_text(), config_path.read_text()])[:12])
    if not context.exists():
        bundle = json.loads((chunked / 'artifact-bundle.json').read_text())
        records, _ = assemble_structural_chunks(bundle)
        fixture = chunked.parent / 'embedding-fixture.json'
        fixture.write_text(json.dumps({'embedding_vectors': {stable_hash(r['text']): [1., 0.] for r in records}}))
        prepare_claim_context(chunked, context, config_path, chunked.parent / 'cache', fixture,
                              on_progress=on_progress)
    return context


def generate_claims(chunked, output, config_path, fixture_path=None, **kwargs):
    return generate_prepared(chunked, output, config_path, fixture_path,
                             context_dir=prepare_fixture(chunked, config_path), **kwargs)


class ClaimContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.chunked = self.root / 'chunked'
        self.chunked.mkdir()
        bundle = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
        bundle['claims'], bundle['topics'] = [], []
        bundle['builds'][0].update(output_claim_ids=[], output_topic_ids=[])
        (self.chunked / 'artifact-bundle.json').write_text(json.dumps(bundle))
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps(fixture_settings()))

    def test_frozen_preparation_is_complete_unique_and_preserves_original_bytes(self):
        original = (self.chunked / 'artifact-bundle.json').read_bytes()
        directory = prepare_fixture(self.chunked, self.config)
        result = load_preparation(directory)
        self.assertEqual((directory / 'artifact-bundle.json').read_bytes(), original)
        self.assertEqual(sorted(c for g in result['clusters'] for c in g['original_chunk_ids']),
                         sorted(c['id'] for c in result['bundle']['chunks']))
        with self.assertRaisesRegex(ValueError, 'already exists'):
            prepare_claim_context(self.chunked, directory, self.config)

    def test_progress_announces_assembly_then_completed_generation_budget_checks(self):
        bundle = json.loads((self.chunked / 'artifact-bundle.json').read_text())
        records, _ = assemble_structural_chunks(bundle)
        fixture = self.root / 'vectors.json'
        fixture.write_text(json.dumps({'embedding_vectors': {stable_hash(r['text']): [1., 0.] for r in records}}))
        events = []
        from knowledge_foundry.generation_context import RequestCounter
        original_check = RequestCounter.check
        checked = []

        def check(counter, payload, label):
            result = original_check(counter, payload, label)
            if label.startswith('structural chunk '):
                checked.append(label)
            return result

        def progress(event):
            if event['state'] == 'checking_individual_structural_chunks_fit_claim_generation':
                self.assertEqual(event['completed'], len(checked))
            events.append(event)

        with patch.object(RequestCounter, 'check', check):
            report = prepare_claim_context(self.chunked, self.root / 'built', self.config,
                                           self.root / 'cache', fixture, on_progress=progress)
        self.assertEqual(events[0]['state'], 'assembling_structural_chunks')
        self.assertEqual(events[1], {'state': 'structural_chunks_assembled', 'structural_chunks': len(records)})
        checks = [e for e in events if e['state'] == 'checking_individual_structural_chunks_fit_claim_generation']
        self.assertEqual(checks[0]['completed'], 0)
        self.assertEqual(checks[-1]['completed'], len(records))
        cluster_checks = [e for e in events if e['state'] == 'checking_semantic_clusters_fit_claim_generation']
        for phase in ('initial', 'final'):
            checks = [e for e in cluster_checks if e['phase'] == phase]
            self.assertEqual(checks[0]['completed'], 0)
            self.assertEqual(checks[-1]['completed'], checks[-1]['total'])
        self.assertEqual(cluster_checks[-1]['total'], report['semantic_clusters'])
        self.assertIn('claim_generation_request_token_counting', report)
        self.assertNotIn('token_counting', report)

    def test_legacy_report_token_counting_remains_readable_without_rewriting(self):
        directory = prepare_fixture(self.chunked, self.config)
        report_path = directory / 'report.json'
        report = json.loads(report_path.read_text())
        report['token_counting'] = report.pop('claim_generation_request_token_counting')
        report_path.write_text(json.dumps(report))
        import hashlib
        manifest_path = directory / 'build-manifest.json'
        manifest = json.loads(manifest_path.read_text())
        for output in manifest['outputs']:
            if output['path'] == 'report.json':
                output['sha256'] = hashlib.sha256(report_path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        original = report_path.read_bytes()
        loaded = load_preparation(directory)
        self.assertIn('token_counting', loaded['report'])
        self.assertEqual(report_path.read_bytes(), original)

    def test_tampering_and_configuration_changes_rejected_without_calls(self):
        directory = prepare_fixture(self.chunked, self.config)
        settings = fixture_settings()
        settings['steps']['semantic_clusters']['minimum_similarity'] = .9
        with self.assertRaisesRegex(ValueError, 'configuration mismatch'):
            load_preparation(directory, settings)
        with patch('knowledge_foundry.claim_generation_and_classification._prompt', return_value=('changed', 'hash')):
            with self.assertRaisesRegex(ValueError, 'prompt/schema'):
                load_preparation(directory)
        path = directory / 'semantic-clusters.json'
        path.write_text('[]')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            load_preparation(directory)

    def test_generation_requires_explicit_preparation(self):
        with self.assertRaisesRegex(ValueError, 'context-dir'):
            generate_prepared(self.chunked, self.root / 'generated', self.config)

    def test_oversized_singleton_fails_before_embeddings(self):
        settings = fixture_settings()
        settings['steps']['claim_generation']['budget']['operational_input_cap_tokens'] = 1
        self.config.write_text(json.dumps(settings))
        events = []
        with patch('knowledge_foundry.embeddings.EmbeddingAdapter.embed', side_effect=AssertionError('no embeddings')):
            with self.assertRaisesRegex(ValueError, 'structural chunk.*complete generation request.*limit 1'):
                prepare_claim_context(self.chunked, self.root / 'failed', self.config,
                                      on_progress=events.append)
        checks = [e for e in events if e['state'] == 'checking_individual_structural_chunks_fit_claim_generation']
        self.assertEqual([e['completed'] for e in checks], [0])

    def test_schema_change_invalidates_preparation(self):
        directory = prepare_fixture(self.chunked, self.config)
        with patch('knowledge_foundry.claim_generation_and_classification.CANDIDATE_SCHEMA', {'type': 'object'}):
            with self.assertRaisesRegex(ValueError, 'prompt/schema'):
                load_preparation(directory)

    def test_multiple_cross_document_claims_survive_classification_export_and_evaluation(self):
        from knowledge_foundry.claim_generation_and_classification import digest, classify_claims
        from scripts.evaluate_claims import evaluate
        from scripts.validate_artifact_examples import check
        first = json.loads((self.chunked / 'artifact-bundle.json').read_text())
        second = json.loads((ROOT / 'examples/artifact-bundle-html.json').read_text())
        for key in ('sources', 'documents', 'elements', 'chunks'):
            first[key].extend(second[key])
        build = first['builds'][0]
        build['input_document_ids'] = [d['id'] for d in first['documents']]
        build['id'] = 'build:' + digest(build['pipeline_version'] + '\n' + build['config_sha256'] + '\n' +
                                       '\n'.join(sorted(build['input_document_ids'])))
        for chunk in first['chunks']:
            chunk['build_id'] = build['id']
        build['output_chunk_ids'] = [c['id'] for c in first['chunks']]
        check(first)
        (self.chunked / 'artifact-bundle.json').write_text(json.dumps(first))
        context = prepare_fixture(self.chunked, self.config)
        prepared = load_preparation(context)
        self.assertEqual(prepared['report']['cross_document_clusters'], 1)
        from knowledge_foundry.claim_generation_and_classification import _candidate_input
        payload = json.loads(_candidate_input(first['chunks'],
                            {e['id']: e for e in first['elements']}, first,
                            prepared['structural_chunks']))
        self.assertEqual(len({e['document_id'] for e in payload['eligible_evidence']}), 2)
        self.assertTrue(all('text' not in e for e in payload['eligible_evidence']))
        self.assertTrue(all('[' + e['id'] + ']' in '\n'.join(r['text'] for r in payload['structural_chunks'])
                            for e in payload['eligible_evidence']))
        ids = prepared['clusters'][0]['original_chunk_ids']
        fixture = self.root / 'candidates.json'
        fixture.write_text(json.dumps({'candidate_clusters': {','.join(ids): {'claims': [
            {'text': 'Proposition A avec deux sources.', 'evidence': ['E1', 'E2']},
            {'text': 'Proposition B avec deux sources.', 'evidence': ['E1', 'E2']}]}},
            'classifications': {'Proposition A avec deux sources.': 'topic:beneficial-ownership',
                                'Proposition B avec deux sources.': 'topic:beneficial-ownership'}}))
        generated, classified = self.root / 'generated', self.root / 'classified'
        report = generate_prepared(self.chunked, generated, self.config, fixture, context_dir=context)
        self.assertEqual((report['candidates'], report['cross_document_candidates']), (2, 2))
        classify_claims(generated, classified, self.config, fixture_path=fixture)
        exported = json.loads((classified / 'artifact-bundle.json').read_text())
        check(exported)
        self.assertEqual(len(exported['claims']), 2)
        for claim in exported['claims']:
            self.assertEqual(len({r['document_id'] for r in claim['evidence']}), 2)
        assessment = evaluate(generated, classified)
        self.assertEqual(assessment['automatic_gate'], 'pass', assessment['findings'])
        self.assertEqual(assessment['review_gate'], 'pending')
        self.assertIn('claim_generation_request_token_counting', assessment['compilation_telemetry'])
        self.assertNotIn('token_counting', assessment['compilation_telemetry'])
        self.assertEqual(assessment['preparation']['cross_document_clusters'], 1)
        # A rehashed invalid mapping must still fail semantic lineage validation.
        mapping_path = generated / 'candidate-clusters.json'
        mapping = json.loads(mapping_path.read_text()); mapping[0]['semantic_cluster_id'] = 'semantic:wrong'
        mapping_path.write_text(json.dumps(mapping))
        from knowledge_foundry.claim_generation_and_classification import file_digest
        manifest_path = generated / 'build-manifest.json'
        manifest = json.loads(manifest_path.read_text())
        for entry in manifest['outputs']:
            if entry['path'] == 'candidate-clusters.json':
                entry['sha256'] = file_digest(mapping_path)
        manifest_path.write_text(json.dumps(manifest))
        self.assertEqual(evaluate(generated, classified)['automatic_gate'], 'fail')

    def test_exact_request_size_refinement_and_unique_membership(self):
        import test_compilation_context as context_tests
        bundle = context_tests.CompilationContextTests().bundle(
            [('paragraph', 'Dogs rest.', 1), ('paragraph', 'Dogs sleep.', 1),
             ('paragraph', 'Dogs eat.', 1)], [[0], [1], [2]])
        (self.chunked / 'artifact-bundle.json').write_text(json.dumps(bundle))
        config = fixture_settings(); config['steps']['claim_generation']['budget']['operational_input_cap_tokens'] = 250
        self.config.write_text(json.dumps(config))
        def measured(_counter, payload):
            self.assertIn('schema', payload['text']['format'])
            self.assertIn('instructions', payload)
            request = json.loads(payload['input'])
            self.assertTrue(request['documents'])
            self.assertTrue(request['sources'])
            return 100 * len(request['structural_chunks'])
        events = []
        with patch('knowledge_foundry.generation_context.RequestCounter.count', measured):
            directory = prepare_fixture(self.chunked, self.config, on_progress=events.append)
        prepared = load_preparation(directory)
        self.assertEqual(len(prepared['clusters']), 2)
        self.assertEqual(len(prepared['report']['oversized_cluster_splits']), 1)
        splits = [e for e in events if e['state'] == 'splitting_oversized_semantic_clusters']
        self.assertEqual([e['status'] for e in splits], ['started', 'complete'])
        self.assertEqual(splits[-1]['resulting_clusters'], 2)
        checks = [e for e in events if e['state'] == 'checking_semantic_clusters_fit_claim_generation']
        self.assertEqual(checks[0], {'state': 'checking_semantic_clusters_fit_claim_generation',
                                    'phase': 'initial', 'completed': 0, 'total': 1})
        self.assertEqual(checks[-1], {'state': 'checking_semantic_clusters_fit_claim_generation',
                                     'phase': 'final', 'completed': 2, 'total': 2})
        self.assertEqual(sorted(c for g in prepared['clusters'] for c in g['original_chunk_ids']),
                         sorted(c['id'] for c in bundle['chunks']))
        self.assertTrue(all(g['input_tokens'] <= 250 for g in prepared['clusters']))

    def test_new_request_format_requires_refreshed_preparation(self):
        directory = prepare_fixture(self.chunked, self.config)
        config = fixture_settings()
        config['steps']['claim_generation']['version'] = 'claim-generation-v9'
        with self.assertRaisesRegex(ValueError, 'configuration mismatch'):
            load_preparation(directory, config)
        with patch('knowledge_foundry.claim_generation_and_classification._prompt', return_value=('old prompt', 'old')):
            with self.assertRaisesRegex(ValueError, 'prompt/schema changed'):
                load_preparation(directory)
        # Existing candidates still validate lineage without current prompt hashes.
        load_preparation(directory, verify_requests=False)

    def test_changed_budget_request_format_and_invalid_config_rejected(self):
        from knowledge_foundry.claim_generation_and_classification import _config
        directory = prepare_fixture(self.chunked, self.config)
        config = fixture_settings(); config['batch_size_chunks'] = 4
        self.config.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'invalid shape'):
            _config(self.config)
        with patch('knowledge_foundry.claim_context.request_for_members', return_value=({'changed': True}, [])):
            with self.assertRaisesRegex(ValueError, 'membership|request format'):
                load_preparation(directory)

    def test_resume_rejects_candidate_from_another_actual_request_cluster(self):
        from knowledge_foundry.claim_generation_and_classification import _load_generation_checkpoints, _candidate_record
        import test_compilation_context as context_tests
        from knowledge_foundry.claim_generation_and_classification import _evidence_choices
        bundle = context_tests.CompilationContextTests().bundle(
            [('paragraph', 'Dogs rest.', 1), ('paragraph', 'Cats eat.', 1)], [[0], [1]])
        elements = {e['id']: e for e in bundle['elements']}
        first, second = bundle['chunks']
        wrong = _candidate_record('build:test', {'text': 'Cats eat.', 'evidence': ['E1']},
                                  {'method': 'manual', 'version': 'test'}, _evidence_choices([second], elements))
        directory = self.root / 'checkpoints'; directory.mkdir()
        (directory / 'generation-checkpoints').mkdir()
        identity = {'generation_build_id': 'build:test'}
        (directory / 'generation-checkpoint.json').write_text(json.dumps(identity))
        record = {'cluster_number': 1, 'chunk_ids': [first['id']], 'candidates': [wrong],
                  'errors': [], 'usage_records': []}
        (directory / 'generation-checkpoints/cluster-000001.json').write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'chunk missing'):
            _load_generation_checkpoints(directory, identity, [[first], [second]],
                                         {c['id']: c for c in bundle['chunks']}, elements)
