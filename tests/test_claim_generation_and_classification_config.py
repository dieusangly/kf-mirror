"""Step isolation, pricing identity, subject policies and historical read compatibility."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from knowledge_foundry.claim_context import (
    execution_settings, load_preparation, saved_settings, validate_settings,
)
from knowledge_foundry.claim_generation_and_classification import (
    ROOT, FixtureProvider, _provider, generate_claims,
)
from knowledge_foundry.embeddings import EmbeddingAdapter
from knowledge_foundry.review import read_review_policy, review_policy_digest
from test_claim_context import fixture_settings, prepare_fixture


class ClaimConfigurationTests(unittest.TestCase):
    def test_strict_step_shapes_and_independent_classification_settings(self):
        config = fixture_settings()
        config['steps']['topic_classification'].update(model='gpt-6.1-sol', reasoning_effort='low', retries=3)
        validate_settings(config)
        with patch('knowledge_foundry.claim_generation_and_classification.FixtureProvider') as provider:
            _provider(config, ROOT / 'config/topics-v1.json', 'topic_classification')
            provider.assert_called_once()
        config['steps']['topic_classification']['unknown'] = 1
        with self.assertRaisesRegex(ValueError, 'topic_classification'):
            validate_settings(config)

    def test_each_provider_gets_its_steps_model_and_reasoning(self):
        config = json.loads((ROOT / 'config/claim-generation-and-classification.json').read_text())
        config['steps']['topic_classification'].update(model='gpt-6.1-sol', reasoning_effort='low')
        with patch('knowledge_foundry.claim_generation_and_classification.OpenAIProvider') as provider:
            _provider(config, None)
            _provider(config, None, 'topic_classification')
        self.assertEqual(provider.call_args_list[0].args, ('gpt-6-luna', 'medium'))
        self.assertEqual(provider.call_args_list[1].args, ('gpt-6.1-sol', 'low'))

    def test_execution_identity_excludes_prices_and_unrelated_classification(self):
        config = fixture_settings()
        changed = copy.deepcopy(config)
        changed['pricing']['version'] = 'new-pricing'
        changed['pricing']['providers']['fixture']['models']['gpt-6-luna']['short_context_usd_per_million_tokens']['input'] = 99
        self.assertEqual(execution_settings(config), execution_settings(changed))
        self.assertEqual(execution_settings(config, 'topic_classification'), execution_settings(changed, 'topic_classification'))
        changed['steps']['topic_classification']['model'] = 'gpt-6.1-sol'
        self.assertEqual(execution_settings(config), execution_settings(changed))
        self.assertNotEqual(execution_settings(config, 'topic_classification'), execution_settings(changed, 'topic_classification'))
        changed['steps']['claim_generation']['reasoning_effort'] = 'low'
        self.assertNotEqual(execution_settings(config), execution_settings(changed))

    def test_missing_invalid_and_wrong_kind_prices_are_rejected(self):
        for change in ('missing', 'negative', 'wrong_kind'):
            config = fixture_settings()
            models = config['pricing']['providers']['fixture']['models']
            if change == 'missing':
                del models['text-embedding-3-small']
            elif change == 'negative':
                models['text-embedding-3-small']['usd_per_million_input_tokens'] = -1
            else:
                models['text-embedding-3-small'] = copy.deepcopy(models['gpt-6-luna'])
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'pricing'):
                validate_settings(config)

    def test_resume_survives_price_and_classification_only_changes(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); chunked = root / 'chunked'; chunked.mkdir()
            bundle = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
            bundle['claims'], bundle['topics'] = [], []
            bundle['builds'][0].update(output_claim_ids=[], output_topic_ids=[])
            (chunked / 'artifact-bundle.json').write_text(json.dumps(bundle))
            config = fixture_settings(); path = root / 'config.json'; path.write_text(json.dumps(config))
            context = prepare_fixture(chunked, path)
            fixture = root / 'fixture.json'; fixture.write_text('{"candidate_clusters":{}}')
            output = root / 'generated'
            with patch('knowledge_foundry.claim_generation_and_classification._write_json', side_effect=RuntimeError('interrupted export')):
                with self.assertRaisesRegex(RuntimeError, 'interrupted export'):
                    generate_claims(chunked, output, path, fixture, context_dir=context)
            build = json.loads((output / 'generation-checkpoint.json').read_text())['generation_build_id']
            config['pricing']['version'] = 'new-pricing'
            config['steps']['topic_classification']['model'] = 'gpt-6.1-sol'
            path.write_text(json.dumps(config))
            with patch.object(FixtureProvider, 'candidates', side_effect=AssertionError('completed request must be reused')):
                report = generate_claims(chunked, output, path, fixture, context_dir=context, resume=True)
            self.assertEqual(report['generation_build_id'], build)
            self.assertEqual(report['telemetry']['rate_card']['version'], 'new-pricing')
            self.assertEqual(report['status'], 'complete')

    def test_subject_policy_hashes_and_versions_are_independent(self):
        source = json.loads((ROOT / 'config/review-policy.json').read_text())
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'policy.json'; path.write_text(json.dumps(source))
            claims = read_review_policy(path, 'claim_generation_and_classification')
            chunking = read_review_policy(path, 'chunking')
            self.assertEqual(claims['policy_version'], '1.1')
            self.assertEqual(chunking['policy_version'], '1.2')
            with self.assertRaisesRegex(ValueError, 'explicit.*subject'):
                read_review_policy(path)
            source['policies']['chunking']['minimum_weighted_score'] = 99
            path.write_text(json.dumps(source))
            self.assertEqual(review_policy_digest(claims, 'claim_generation_and_classification'),
                             review_policy_digest(read_review_policy(path, 'claim_generation_and_classification'), 'claim_generation_and_classification'))
            self.assertNotEqual(review_policy_digest(chunking, 'chunking'),
                                review_policy_digest(read_review_policy(path, 'chunking'), 'chunking'))

    def test_legacy_frozen_configuration_remains_readable_without_rewriting_files(self):
        from knowledge_foundry.claim_context import cluster_namespace
        config = fixture_settings(); steps = config['steps']
        old_embedding = copy.deepcopy(steps['embeddings'])
        old_embedding.update(usd_per_million_input_tokens=.02, rate_card_version='legacy',
                             documentation_url='https://developers.openai.com/api/docs/models/text-embedding-3-small')
        old = {'pipeline_version': config['pipeline_version'],
               **{key: steps['claim_generation'][key] for key in ('provider', 'model', 'reasoning_effort', 'retries')},
               'generation_version': steps['claim_generation']['version'],
               'classification_version': steps['topic_classification']['version'],
               'preparation': cluster_namespace(config), 'embeddings': old_embedding,
               'clustering': {k: v for k, v in steps['semantic_clusters'].items() if k != 'version'},
               'generation_budget': steps['claim_generation']['budget'],
               'pricing': {'rate_card_version': 'legacy', 'models': {
                   model: {k: v for k, v in card.items() if k not in {'verified_on', 'documentation_url'}}
                   for model, card in config['pricing']['providers']['fixture']['models'].items()
                   if 'short_context_limit_tokens' in card}}}
        adapted = saved_settings(old)
        validate_settings(adapted)
        self.assertEqual(execution_settings(adapted), execution_settings(config))
        self.assertIn('preparation', old)
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); chunked = root / 'chunked'; chunked.mkdir()
            bundle = json.loads((ROOT / 'examples/artifact-bundle-pdf.json').read_text())
            bundle['claims'], bundle['topics'] = [], []
            bundle['builds'][0].update(output_claim_ids=[], output_topic_ids=[])
            (chunked / 'artifact-bundle.json').write_text(json.dumps(bundle))
            path = root / 'settings.json'; path.write_text(json.dumps(config))
            context = prepare_fixture(chunked, path)
            manifest_path = context / 'build-manifest.json'
            manifest = json.loads(manifest_path.read_text()); manifest['config'] = old
            manifest_path.write_text(json.dumps(manifest))
            before = {p.name: p.read_bytes() for p in context.iterdir()}
            prepared = load_preparation(context, verify_requests=False)
            self.assertEqual(len(prepared['clusters']), 1)
            self.assertEqual(before, {p.name: p.read_bytes() for p in context.iterdir()})

    def test_embedding_cache_survives_shared_pricing_snapshot_change(self):
        from knowledge_foundry.structural_chunks import stable_hash
        config = fixture_settings()['steps']['embeddings']
        fixture = {'embedding_vectors': {stable_hash('Dog D'): [1, 0]}}
        records = [{'id': 'dog', 'text': 'Dog D'}]
        with tempfile.TemporaryDirectory() as name:
            original = EmbeddingAdapter(config, name, fixture, pricing={'usd_per_million_input_tokens': .02}, pricing_version='v1')
            first = original.embed(records)
            changed = EmbeddingAdapter(config, name, fixture, pricing={'usd_per_million_input_tokens': .04}, pricing_version='v2')
            with patch.object(changed, '_request', side_effect=AssertionError('cached vectors must be reused')):
                second = changed.embed(records)
            self.assertEqual(first[0]['cache_key'], second[0]['cache_key'])
            self.assertEqual(changed.telemetry['pricing_version'], 'v2')
            self.assertEqual(changed.telemetry['requests'], 0)
