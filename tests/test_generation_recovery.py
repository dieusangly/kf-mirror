"""Recover only failed generation clusters without altering the saved baseline."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from claim_fixtures import fixture_settings
from claim_fixtures import prepare_claim_context
from knowledge_foundry.claim_generation_and_classification import (
    ROOT, FixtureProvider, _build_id, _candidate_manifest, classify_claims, generate_claims,
)
from knowledge_foundry.structural_chunks import assemble_structural_chunks, stable_hash
from scripts.generate_claims import main


class GenerationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.chunked = self.root / 'chunked'
        self.chunked.mkdir()
        bundle = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
        other = json.loads((ROOT / 'examples/artifact-bundle-html.json').read_text())
        for key in ('sources', 'documents', 'elements', 'chunks'):
            bundle[key].extend(other[key])
        bundle['claims'], bundle['topics'] = [], []
        build = bundle['builds'][0]
        build['input_document_ids'] = sorted(d['id'] for d in bundle['documents'])
        build['id'] = _build_id(build['pipeline_version'], build['config_sha256'], build['input_document_ids'])
        build['output_claim_ids'], build['output_topic_ids'] = [], []
        build['output_chunk_ids'] = sorted(c['id'] for c in bundle['chunks'])
        for chunk in bundle['chunks']:
            chunk['build_id'] = build['id']
        (self.chunked / 'artifact-bundle.json').write_text(json.dumps(bundle))
        self.config = self.root / 'config.json'
        settings = fixture_settings()
        settings['steps']['claim_generation']['retries'] = 0
        self.config.write_text(json.dumps(settings))
        structural, _ = assemble_structural_chunks(bundle)
        fixture = self.root / 'embeddings.json'
        fixture.write_text(json.dumps({'embedding_vectors': {
            stable_hash(record['text']): [float(i == 0), float(i == 1)]
            for i, record in enumerate(structural)}}))
        self.context = self.root / 'context'
        prepare_claim_context(self.chunked, self.context, self.config, self.root / 'cache', fixture)
        self.clusters = json.loads((self.context / 'semantic-clusters.json').read_text())
        self.assertEqual(len(self.clusters), 2)

    def provider(self, fail=False, empty=False):
        failing_ids = self.clusters[1]['original_chunk_ids']

        class Provider(FixtureProvider):
            def candidates(inner, ids, *_args):
                inner.calls.append(ids)
                inner.usage_records.append({'input_tokens': 10, 'cached_input_tokens': 0,
                                            'cache_write_tokens': 0, 'output_tokens': 5})
                if fail and ids == failing_ids:
                    raise RuntimeError('provider unavailable')
                if empty and ids != failing_ids:
                    return {'claims': [], 'coverage': [
                        {'evidence_id': 'E1', 'status': 'non_substantive',
                         'claim_indices': [], 'reason': 'Bibliographic context only.'}]}
                # Wrong model indices must not discard otherwise valid output.
                return {'claims': [{'text': 'Saved source statement.', 'evidence': ['E1']}],
                        'coverage': [{'evidence_id': 'E1', 'status': 'represented',
                                      'claim_indices': [99], 'reason': 'Source statement retained.'}]}

        provider = Provider({})
        provider.calls = []
        return provider

    def generate(self, directory, provider, **kwargs):
        def make_provider(*_args):
            worker = type(provider)(provider.fixture)
            worker.calls = provider.calls
            return worker

        with patch('knowledge_foundry.claim_generation_and_classification._provider', side_effect=make_provider):
            return generate_claims(None, directory, self.config, context_dir=self.context, **kwargs)

    def test_recovery_preserves_candidates_baseline_and_cumulative_usage(self):
        source, target = self.root / 'partial', self.root / 'recovered'
        partial = self.generate(source, self.provider(fail=True))
        self.assertEqual(partial['status'], 'partial')
        baseline = {p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()}
        saved = (source / 'claim-candidates.jsonl').read_text().splitlines()
        provider = self.provider()
        report = self.generate(target, provider, retry_failed_from=source)
        self.assertEqual(provider.calls, [self.clusters[1]['original_chunk_ids']])
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['recovery']['reused_clusters'], 1)
        self.assertEqual(report['recovery']['retried_cluster_numbers'], [2])
        self.assertEqual(report['telemetry']['requests'], 3)
        self.assertEqual(report['telemetry']['usage']['input_tokens'], 30)
        self.assertAlmostEqual(report['telemetry']['estimated_cost_usd'],
                               partial['telemetry']['estimated_cost_usd'] * 1.5)
        self.assertTrue(set(saved) <= set((target / 'claim-candidates.jsonl').read_text().splitlines()))
        self.assertEqual(baseline, {p.relative_to(source): p.read_bytes()
                                    for p in source.rglob('*') if p.is_file()})
        _candidate_manifest(target)
        with self.assertRaisesRegex(ValueError, 'partial generation'):
            self.generate(self.root / 'unneeded', self.provider(), retry_failed_from=target)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.generate(self.root / 'wrong-scope', self.provider(), retry_failed_from=source,
                          cluster_numbers=[2])
        self.assertFalse((self.root / 'wrong-scope').exists())

    def test_interrupted_recovery_resumes_without_repeating_successful_clusters(self):
        source, target = self.root / 'partial', self.root / 'recovered'
        self.generate(source, self.provider(fail=True))
        with patch('knowledge_foundry.claim_generation_and_classification._write_json',
                   side_effect=RuntimeError('interrupted export')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted export'):
                self.generate(target, self.provider(), retry_failed_from=source)
        provider = self.provider()
        report = self.generate(target, provider, retry_failed_from=source, resume=True)
        self.assertEqual(provider.calls, [])
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['telemetry']['requests'], 3)
        _candidate_manifest(target)

    def test_zero_claim_success_is_reused_and_source_tampering_is_rejected(self):
        source = self.root / 'partial'
        self.generate(source, self.provider(fail=True, empty=True))
        provider = self.provider()
        report = self.generate(self.root / 'recovered', provider, retry_failed_from=source)
        self.assertEqual(provider.calls, [self.clusters[1]['original_chunk_ids']])
        self.assertEqual(report['candidates'], 1)
        _candidate_manifest(self.root / 'recovered')
        with self.assertRaisesRegex(ValueError, 'separate directories'):
            self.generate(source / 'nested', self.provider(), retry_failed_from=source)
        (source / 'report.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.generate(self.root / 'tampered', self.provider(), retry_failed_from=source)
        self.assertFalse((self.root / 'tampered').exists())

    def test_cli_partial_is_nonzero_and_recovery_option_is_forwarded(self):
        report = {'status': 'partial', 'telemetry': {'requests': 0, 'estimated_cost_usd': 0}}

        def generate(*args, **kwargs):
            kwargs['on_progress']({'phase': 'complete', 'state': 'partial',
                                   'telemetry': report['telemetry']})
            return report

        with patch('scripts.generate_claims.generate_claims', side_effect=generate) as call, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--context-dir', 'context', '--output-dir', 'fresh',
                                   '--retry-failed-from', 'partial']), 1)
        self.assertEqual(call.call_args.kwargs['retry_failed_from'], Path('partial'))

    def test_classification_rejects_partial_generation_before_provider_or_output(self):
        source, output = self.root / 'partial', self.root / 'classified'
        self.generate(source, self.provider(fail=True))
        with patch('knowledge_foundry.claim_generation_and_classification._provider') as provider:
            with self.assertRaisesRegex(ValueError, 'classification requires complete generation'):
                classify_claims(source, output, self.config)
            provider.assert_not_called()
        self.assertFalse(output.exists())
