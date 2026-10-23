"""Classification reuse preserves nulls, validates inputs and survives interruption."""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

import tempfile
from claim_fixtures import prepare_claim_review
from claim_fixtures import generate_claims
from knowledge_foundry.claim_generation_and_classification import (
    FixtureProvider, classify_claims,
)
from scripts.auto_review_claims import _load
from scripts.classify_claims import main


class ClassificationReuseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        scenario = prepare_claim_review(directory.name)
        self.root, self.source, self.classified = scenario.root, scenario.generated, scenario.classified
        self.config, self.fixture, self.chunked = scenario.config, scenario.fixture, scenario.chunked
        self.taxonomy = scenario.classified.parent / 'taxonomy.json'
        from knowledge_foundry.claim_generation_and_classification import ROOT
        self.taxonomy.write_bytes((ROOT / 'config/topics-v1.json').read_bytes())
        fixture = json.loads(self.fixture.read_text())
        for response in fixture['candidate_clusters'].values():
            response['claims'].append({'text': 'New recovered statement.', 'evidence': ['E1']})
        fixture['classifications']['New recovered statement.'] = 'topic:ongoing-vigilance'
        self.new_fixture = self.root / 'new-fixture.json'
        self.new_fixture.write_text(json.dumps(fixture))
        self.generated = self.root / 'new-generated'
        generate_claims(self.chunked, self.generated, self.config, self.new_fixture)

    def run_classification(self, output, provider, **kwargs):
        with patch('knowledge_foundry.claim_generation_and_classification._provider', return_value=provider):
            return classify_claims(self.generated, output, self.config, self.taxonomy,
                                   reuse_classification_from=self.classified,
                                   reuse_candidates_dir=self.source, **kwargs)

    def provider(self):
        provider = FixtureProvider({'classifications': {'New recovered statement.': 'topic:ongoing-vigilance'}})
        provider.classify = Mock(wraps=provider.classify)
        return provider

    def test_only_new_candidate_calls_model_and_null_outcome_is_preserved(self):
        provider = self.provider()
        baseline = {p.relative_to(self.classified): p.read_bytes()
                    for p in self.classified.rglob('*') if p.is_file()}
        target = self.root / 'reused'
        report = self.run_classification(target, provider)
        self.assertEqual(provider.classify.call_count, 1)
        self.assertEqual(report['classification_reuse']['reused_candidates'], 3)
        self.assertEqual(report['classification_reuse']['newly_classified_candidates'], 1)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(len(report['unclassified_candidate_ids']), 1)
        self.assertEqual(report['claims'], 3)
        loaded = _load(self.generated, target, self.taxonomy)
        old = _load(self.source, self.classified, self.taxonomy)
        for cid, outcome in old['outcomes'].items():
            self.assertEqual(loaded['outcomes'][cid]['topic_id'], outcome['topic_id'])
        self.assertEqual(baseline, {p.relative_to(self.classified): p.read_bytes()
                                    for p in self.classified.rglob('*') if p.is_file()})
        self.assertFalse((target / 'classification-checkpoint.json').exists())

    def test_resume_after_interruption_does_not_repeat_paid_calls(self):
        target, provider = self.root / 'interrupted', self.provider()
        with patch('knowledge_foundry.claim_generation_and_classification._write_json',
                   side_effect=RuntimeError('export interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'export interrupted'):
                self.run_classification(target, provider)
        self.assertEqual(provider.classify.call_count, 1)
        self.assertTrue((target / 'classification-checkpoint.json').exists())
        provider = self.provider()
        report = self.run_classification(target, provider, resume=True)
        provider.classify.assert_not_called()
        self.assertEqual(report['claims'], 3)
        _load(self.generated, target, self.taxonomy)

    def test_resume_mid_loop_keeps_completed_candidate_results(self):
        target, provider = self.root / 'mid-loop', self.provider()

        def interrupt(event):
            if event['state'] == 'progress' and event['completed'] == 2:
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            self.run_classification(target, provider, on_progress=interrupt)
        completed_calls = provider.classify.call_count
        provider = self.provider()
        self.run_classification(target, provider, resume=True)
        self.assertEqual(completed_calls + provider.classify.call_count, 1)
        _load(self.generated, target, self.taxonomy)

    def test_resume_rejects_corrupt_checkpoint(self):
        target = self.root / 'interrupted'
        with patch('knowledge_foundry.claim_generation_and_classification._write_json',
                   side_effect=RuntimeError('export interrupted')):
            with self.assertRaises(RuntimeError):
                self.run_classification(target, self.provider())
        record = next((target / 'classification-checkpoints').glob('*.json'))
        value = json.loads(record.read_text())
        value['classification']['topic_id'] = 'topic:tampered'
        record.write_text(json.dumps(value))
        provider = self.provider()
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            self.run_classification(target, provider, resume=True)
        provider.classify.assert_not_called()

    def test_reuse_rejects_changed_reasoning_and_taxonomy_before_calls_or_output(self):
        config = json.loads(self.config.read_text())
        config['steps']['topic_classification']['reasoning_effort'] = 'high'
        self.config.write_text(json.dumps(config))
        target, provider = self.root / 'bad-settings', self.provider()
        with self.assertRaisesRegex(ValueError, 'identical settings'):
            self.run_classification(target, provider)
        provider.classify.assert_not_called()
        self.assertFalse(target.exists())
        config['steps']['topic_classification']['reasoning_effort'] = 'medium'
        self.config.write_text(json.dumps(config))
        taxonomy = json.loads(self.taxonomy.read_text())
        taxonomy['topics'][0]['scope'] += ' Changed scope.'
        self.taxonomy.write_text(json.dumps(taxonomy))
        with self.assertRaisesRegex(ValueError, 'taxonomy'):
            self.run_classification(target, provider)
        provider.classify.assert_not_called()
        self.assertFalse(target.exists())

    def test_existing_output_rejected_before_provider_creation(self):
        target = self.root / 'existing'
        target.mkdir()
        with patch('knowledge_foundry.claim_generation_and_classification._provider') as provider:
            with self.assertRaisesRegex(ValueError, 'already exists'):
                classify_claims(self.generated, target, self.config, self.taxonomy)
            provider.assert_not_called()

    def test_cli_reuses_prior_outcomes_and_writes_combined_artifact(self):
        target, out = self.root / 'cli-reused', io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            result = main(['--candidates-dir', str(self.generated), '--output-dir', str(target),
                           '--reuse-classification-from', str(self.classified),
                           '--reuse-candidates-dir', str(self.source), '--config', str(self.config),
                           '--taxonomy', str(self.taxonomy), '--fixture', str(self.new_fixture)])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(out.getvalue())['classification_reuse']['reused_candidates'], 3)
        _load(self.generated, target, self.taxonomy)

    def test_prior_classification_errors_are_retried_instead_of_reused_as_nulls(self):
        failed = self.root / 'partial-classification'
        fixture = self.root / 'partial-classification-fixture.json'
        fixture.write_text(json.dumps({'classifications': {'Assertion A.': 'topic:beneficial-ownership'}}))
        report = classify_claims(self.source, failed, self.config, self.taxonomy, fixture)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(len(report['errors']), 2)
        provider = FixtureProvider(json.loads(self.new_fixture.read_text()))
        provider.classify = Mock(wraps=provider.classify)
        with patch('knowledge_foundry.claim_generation_and_classification._provider', return_value=provider):
            report = classify_claims(self.generated, self.root / 'retried-errors', self.config, self.taxonomy,
                                     reuse_classification_from=failed, reuse_candidates_dir=self.source)
        self.assertEqual(provider.classify.call_count, 3)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['classification_reuse']['reused_candidates'], 1)
