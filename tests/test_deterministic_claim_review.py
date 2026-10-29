"""Source-review gates are deterministic; fixtures test mechanics, not model quality."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


from knowledge_foundry.claim_generation_and_classification import ROOT, canonical, classify_claims, digest
from knowledge_foundry.review import apply_reviews, read_review_policy
from scripts.deterministic_claim_review import DEFAULT_POLICY, _select, evaluate, main


from claim_fixtures import generate_claims, prepare_claim_review


class CompilationEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.__dict__.update(vars(prepare_claim_review(self.directory.name)))

    def evaluate(self, **kwargs):
        return evaluate(self.generated, self.classified, **kwargs)

    def reviewed(self, report=None):
        report = report or self.evaluate()
        review = copy.deepcopy(report['review_template'])
        review['reviewer'] = {'kind': 'human', 'name': 'Test reviewer', 'model': None}
        for row in review['reviews']:
            row.update(status='pass', severity=None, notes='Saved source inspected; fixture judgment only.')
        return review

    def save(self, review):
        path = self.root / 'review.json'
        path.write_text(json.dumps(review))
        return path

    def test_initial_evaluation_is_pending_and_covers_all_dimensions(self):
        before = {str(p): p.read_bytes() for d in (self.generated, self.classified) for p in d.rglob('*') if p.is_file()}
        with patch('knowledge_foundry.openai_tools.OpenAIResponsesTool.json_response', side_effect=AssertionError('no model calls')):
            first = self.evaluate()
        self.assertEqual(first, self.evaluate())
        self.assertEqual((first['automatic_gate'], first['review_gate'], first['gate']), ('pass', 'pending', 'pending_review'))
        self.assertEqual(set(first['dimensions']), {'generation', 'classification', 'completeness', 'topics'})
        self.assertEqual(first['review']['weighted_score'], None)
        self.assertTrue(any('unclassified' in s['reasons'] for s in first['review_sample']))
        self.assertTrue(all(s['sources'][0]['source_file'] for s in first['review_sample']))
        for name, value in before.items():
            self.assertEqual(Path(name).read_bytes(), value)
        self.assertEqual(first['compilation_telemetry']['generation']['provider'], 'fixture')

    def test_complete_review_passes_and_reports_measured_or_unknown_telemetry(self):
        review = self.reviewed()
        review['telemetry'].update(started_at='2026-10-15T10:00:00+00:00', finished_at='2026-10-15T10:02:00+00:00')
        result = self.evaluate(review_path=self.save(review))
        self.assertEqual(result['gate'], 'pass')
        self.assertEqual(result['review']['weighted_score'], 100)
        self.assertEqual(result['review']['review_coverage'], 1)
        self.assertEqual(result['review_telemetry']['duration_seconds'], 120)
        self.assertIsNone(result['review_telemetry']['estimated_cost_usd'])
        self.assertEqual(result['reviewer']['kind'], 'human')
        self.assertIsNotNone(result['review_sha256'])

    def test_critical_and_dimension_failure_cannot_hide_in_an_overall_high_score(self):
        for severity in ['critical', 'major', 'minor']:
            with self.subTest(severity=severity):
                review = self.reviewed()
                row = next(r for r in review['reviews'] if r['dimension'] == 'completeness')
                row.update(status='fail', severity=severity, notes='PDF p3: missing governing context.')
                result = self.evaluate(review_path=self.save(review))
                self.assertEqual(result['gate'], 'fail')
                self.assertEqual(result['dimensions']['completeness']['status'], 'fail')
                if severity != 'critical':
                    self.assertGreaterEqual(result['review']['weighted_score'], 90)

    def test_pending_rows_and_omitted_rows_never_pass(self):
        initial = self.evaluate()
        self.assertEqual(self.evaluate(review_path=self.save(initial['review_template']))['gate'], 'pending_review')
        review = self.reviewed(initial)
        review['reviews'].pop()
        result = self.evaluate(review_path=self.save(review))
        self.assertEqual(result['gate'], 'pending_review')
        self.assertEqual(result['review']['pending'], 1)

    def test_stale_snapshot_rejects_changed_sampling_or_policy(self):
        review = self.save(self.reviewed())
        with self.assertRaisesRegex(ValueError, 'stale review snapshot'):
            self.evaluate(review_path=review, sample_count=1)
        policy = json.loads(DEFAULT_POLICY.read_text())
        policy['policies']['claim_generation_and_classification']['minimum_weighted_score'] = 99
        path = self.root / 'policy.json'
        path.write_text(json.dumps(policy))
        with self.assertRaisesRegex(ValueError, 'stale review snapshot'):
            self.evaluate(review_path=review, review_policy_path=path)

    def test_review_snapshot_ignores_other_subject_policy_changes(self):
        initial = self.evaluate()
        review = self.save(self.reviewed(initial))
        catalogue = json.loads(DEFAULT_POLICY.read_text())
        catalogue['policies']['chunking']['minimum_weighted_score'] = 99
        path = self.root / 'changed-chunking-policy.json'
        path.write_text(json.dumps(catalogue))
        result = self.evaluate(review_path=review, review_policy_path=path)
        self.assertEqual(result['snapshot_sha256'], initial['snapshot_sha256'])
        self.assertEqual(result['gate'], 'pass')

    def test_invalid_review_ids_duplicates_notes_severity_and_identity_are_rejected(self):
        changes = [
            lambda r: r.update(build_id='build:wrong'),
            lambda r: r['reviews'].append(copy.deepcopy(r['reviews'][0])),
            lambda r: r['reviews'][0].update(sample_id='generation:candidate:unknown'),
            lambda r: r['reviews'][0].update(dimension='wrong'),
            lambda r: r['reviews'][0].update(status='fail', severity='major', notes=None),
            lambda r: r['reviews'][0].update(status='fail', severity='unknown', notes='p3'),
            lambda r: r.update(reviewer={'kind': 'agent', 'name': ''}),
            lambda r: r['reviewer'].update(model_reasoning_effort=''),
            lambda r: r['reviewer'].update(model_reasoning_effort=3),
            lambda r: r['reviewer'].update(launch_mode='standalone'),
            lambda r: r['reviewer'].update(launch_mode=[]),
            lambda r: r['telemetry'].update(estimated_cost_usd=-1),
        ]
        for change in changes:
            review = self.reviewed()
            change(review)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.evaluate(review_path=self.save(review))

    def test_session_attribution_is_preserved_without_changing_snapshot(self):
        initial = self.evaluate()
        review = self.reviewed(initial)
        for mode in ('manual_workflow', 'automated_workflow'):
            review['reviewer'].update(model='gpt-6.1-sol', model_reasoning_effort='medium', launch_mode=mode)
            result = self.evaluate(review_path=self.save(review))
            self.assertEqual(result['reviewer'], review['reviewer'])
            self.assertEqual(result['snapshot_sha256'], initial['snapshot_sha256'])
            self.assertEqual(result['gate'], 'pass')
        review['reviewer'].pop('model_reasoning_effort')
        review['reviewer'].pop('launch_mode')
        self.assertEqual(self.evaluate(review_path=self.save(review))['reviewer'], review['reviewer'])

    def test_unsampled_failures_are_counted_once_and_cannot_inflate_score(self):
        initial = self.evaluate(sample_count=1)
        review = self.reviewed(initial)
        chosen = {r['sample_id'] for r in review['reviews']}
        candidates = [json.loads(l) for l in (self.generated / 'claim-candidates.jsonl').read_text().splitlines()]
        cid = next(c['id'] for c in candidates if 'generation:' + c['id'] not in chosen)
        review['additional_findings'] = [{'sample_id': 'generation:' + cid, 'dimension': 'generation',
            'status': 'fail', 'severity': 'critical', 'notes': 'PDF p3: unsupported statement.'}]
        result = self.evaluate(review_path=self.save(review), sample_count=1)
        self.assertEqual(result['gate'], 'fail')
        self.assertEqual(result['review']['severity_counts']['critical'], 1)
        self.assertEqual(result['review']['to_review'], len(initial['review_sample']) + 1)
        review['additional_findings'][0]['status'] = 'pass'
        with self.assertRaisesRegex(ValueError, 'additional findings'):
            self.evaluate(review_path=self.save(review), sample_count=1)

    def test_artifact_tampering_fails_automatic_gate(self):
        path = self.classified / 'claims.jsonl'
        path.write_text(path.read_text() + '\n')
        result = self.evaluate()
        self.assertEqual((result['automatic_gate'], result['gate']), ('fail', 'fail'))
        self.assertIn('hash mismatch', result['findings'][0]['reason'])

    def test_partial_classification_has_failed_gate_and_error_accounting(self):
        fixture = json.loads(self.fixture.read_text())
        del fixture['classifications']['Assertion B.']
        self.fixture.write_text(json.dumps(fixture))
        partial = self.root / 'partial'
        classify_claims(self.generated, partial, self.config, fixture_path=self.fixture)
        result = evaluate(self.generated, partial)
        self.assertEqual(result['gate'], 'fail')
        self.assertTrue(any(f['code'] == 'STAGE_INCOMPLETE' for f in result['findings']))
        self.assertTrue(any('classification_error' in s['reasons'] for s in result['review_sample']))

    def test_source_hash_mismatch_fails_without_judging_legal_effectiveness(self):
        manifest = json.loads((ROOT / 'data/manifest.json').read_text())
        document = json.loads((self.generated / 'artifact-bundle.json').read_text())['documents'][0]
        source = next(s for s in manifest['sources'] if s['source_id'] == document['source_id'])
        source['local_path'] = 'wrong.pdf'
        (self.root / 'wrong.pdf').write_bytes(b'%PDF wrong')
        path = self.root / 'manifest.json'
        path.write_text(json.dumps(manifest))
        result = self.evaluate(manifest_path=path, source_root=self.root)
        self.assertEqual(result['automatic_gate'], 'fail')
        self.assertTrue(any(f['code'] == 'SAVED_SOURCE_INVALID' for f in result['findings']))

    def test_cli_initializes_once_and_matches_chunking_exit_codes(self):
        args = ['--candidates-dir', str(self.generated), '--classified-dir', str(self.classified)]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(['prepare', *args]), 0)
            review = self.classified / 'compilation-review.json'
            self.assertTrue(review.is_file())
            pending = json.loads(review.read_text())
            pending['reviews'][0]['notes'] = 'Preserve this source-review note while resuming.'
            review.write_text(json.dumps(pending))
            original_review = review.read_bytes()
            self.assertEqual(main(['finalize', *args, '--review-file', str(review)]), 1)
            self.assertEqual(review.read_bytes(), original_review)
            report = json.loads((self.classified / 'claim_review_report.json').read_text())
            self.assertEqual((report['automatic_gate'], report['gate']), ('pass', 'pending_review'))
            with self.assertRaises(SystemExit) as error:
                main(['prepare', *args])
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(review.read_bytes(), original_review)
            reviewed = self.reviewed()
            review.write_text(json.dumps(reviewed))
            self.assertEqual(main(['finalize', *args, '--review-file', str(review)]), 0)

    def test_cli_rejects_wrong_phase_options_without_writing(self):
        args = ['--candidates-dir', str(self.generated), '--classified-dir', str(self.classified)]
        review = self.root / 'new-review.json'
        for options in (
            ['finalize', *args],
            ['prepare', *args, '--review-file', str(review)],
            ['finalize', *args, '--review-file', str(review), '--initialize-review'],
        ):
            with self.subTest(options=options), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    main(options)
                self.assertEqual(error.exception.code, 2)
        self.assertFalse(review.exists())
        self.assertFalse((self.classified / 'claim_review_report.json').exists())

    def test_cli_refuses_to_overwrite_compilation_inputs(self):
        args = ['--candidates-dir', str(self.generated), '--classified-dir', str(self.classified)]
        path = self.classified / 'report.json'
        saved = path.read_bytes()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main(['prepare', *args, '--output', str(path)])
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(path.read_bytes(), saved)

    def test_invalid_artifacts_still_write_failure_report_without_initializing_review(self):
        path = self.classified / 'claims.jsonl'
        path.write_text(path.read_text() + '\n')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(['prepare', '--candidates-dir', str(self.generated), '--classified-dir', str(self.classified),
                                   '--initialize-review']), 1)
        self.assertEqual(json.loads((self.classified / 'claim_review_report.json').read_text())['gate'], 'fail')
        self.assertFalse((self.classified / 'compilation-review.json').exists())

    def test_known_critical_failure_blocks_gate_even_with_pending_samples(self):
        review = self.reviewed()
        rows = [r for r in review['reviews'] if r['dimension'] == 'generation']
        rows[0].update(status='fail', severity='critical', notes='PDF p3: fabricated rule.')
        rows[1].update(status='pending', notes='PDF p3 source verification pending.')
        result = self.evaluate(review_path=self.save(review))
        self.assertEqual(result['gate'], 'fail')
        self.assertGreater(result['review']['pending'], 0)

    def test_new_source_ids_and_matching_external_manifest_need_no_reference_fixture(self):
        bundle = json.loads((self.chunked / 'artifact-bundle.json').read_text())
        source, document, element, chunk, build = [bundle[name][0] for name in ('sources', 'documents', 'elements', 'chunks', 'builds')]
        original = next(s for s in json.loads((ROOT / 'data/manifest.json').read_text())['sources']
                        if s['source_id'] == source['id'])
        (self.root / 'new.pdf').write_bytes((ROOT / original['local_path']).read_bytes())
        source.update(id='src:new-source', uri='https://example.org/new.pdf', title='Another collection', version='new-edition')
        document.update(source_id=source['id'], source_version=source['version'])
        document['id'] = 'doc:' + digest(source['id'] + '\n' + document['content_sha256'])
        element['document_id'] = document['id']
        element['id'] = 'el:' + digest(document['id'] + '\n' + element['locator_key'])
        chunk['document_id'] = document['id']
        for ref in chunk['evidence']:
            ref.update(source_id=source['id'], document_id=document['id'], element_id=element['id'])
        build['input_document_ids'] = [document['id']]
        build['id'] = 'build:' + digest(build['pipeline_version'] + '\n' + build['config_sha256'] + '\n' + document['id'])
        chunk['build_id'] = build['id']
        chunk['id'] = 'chunk:' + digest(document['id'] + '\n' + chunk['chunker_version'] + '\n' + canonical(chunk['evidence']) + '\n' + chunk['text'])
        build['output_chunk_ids'] = [chunk['id']]
        (self.chunked / 'artifact-bundle.json').write_text(json.dumps(bundle))
        fixture = json.loads(self.fixture.read_text())
        fixture['candidate_clusters'] = {chunk['id']: next(iter(fixture['candidate_clusters'].values()))}
        self.fixture.write_text(json.dumps(fixture))
        generated, classified = self.root / 'new-generated', self.root / 'new-classified'
        generate_claims(self.chunked, generated, self.config, self.fixture)
        classify_claims(generated, classified, self.config, fixture_path=self.fixture)
        manifest = self.root / 'new-manifest.json'
        manifest.write_text(json.dumps({'sources': [{**original, 'source_id': source['id'], 'source_version': source['version'],
            'publisher_url': source['uri'], 'title': source['title'], 'local_path': 'new.pdf'}]}))
        result = evaluate(generated, classified, manifest_path=manifest, source_root=self.root)
        self.assertEqual(result['automatic_gate'], 'pass')
        self.assertTrue(all(s['sources'][0]['source_id'] == 'src:new-source' for s in result['review_sample']))

    def test_sampling_matches_risk_first_then_reading_order_without_duplicates(self):
        rows = [{'id': i, 'reasons': ['footnote'] if i == 4 else ['table'] if i == 7 else []} for i in range(10)]
        chosen = _select(rows, 5, ('table', 'footnote'))
        self.assertEqual([r['id'] for r in chosen[:2]], [7, 4])
        self.assertEqual(len({r['id'] for r in chosen}), 5)
        self.assertEqual(chosen, _select(rows, 5, ('table', 'footnote')))

    def test_policy_and_major_failure_scoring_match_chunking(self):
        policy = read_review_policy(DEFAULT_POLICY, 'claim_generation_and_classification')
        chunking = read_review_policy(ROOT / 'config/review-policy.json', 'chunking')
        self.assertEqual({k: v for k, v in policy.items() if k != 'policy_version'},
                         {k: v for k, v in chunking.items() if k != 'policy_version'})
        samples = [{'sample_id': str(i)} for i in range(20)]
        review = {'build_id': 'build:test', 'reviews': [{'sample_id': str(i), 'status': 'pass'} for i in range(20)]}
        review['reviews'][0].update(status='fail', severity='major', notes='p1 missing condition')
        result, _ = apply_reviews(review, 'build:test', samples, policy, 'sample_id')
        self.assertEqual((result['status'], result['weighted_score']), ('pass', 96))
        review['reviews'][1].update(status='fail', severity='major', notes='p2 missing condition')
        result, _ = apply_reviews(review, 'build:test', samples, policy, 'sample_id')
        self.assertEqual((result['status'], result['weighted_score']), ('pass', 92))
        review['reviews'][2].update(status='fail', severity='major', notes='p3 missing condition')
        result, _ = apply_reviews(review, 'build:test', samples, policy, 'sample_id')
        self.assertEqual((result['status'], result['weighted_score']), ('fail', 88))
        self.assertEqual(result, apply_reviews(review, 'build:test', samples, chunking, 'sample_id')[0])
