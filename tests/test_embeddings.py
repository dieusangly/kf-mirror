import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from knowledge_foundry.embeddings import EmbeddingAdapter, validate_response
from knowledge_foundry.structural_chunks import stable_hash
from test_claim_context import fixture_settings


class EmbeddingTests(unittest.TestCase):
    def test_response_indices_dimensions_and_values(self):
        good = {'data': [{'index': 1, 'embedding': [0, 1]}, {'index': 0, 'embedding': [1, 0]}]}
        self.assertEqual(validate_response(good, 2, 2), [[1, 0], [0, 1]])
        invalid = [[], [0, 0], [float('nan'), 1], [float('inf'), 1], [True, 1], [1, 0, 0]]
        for vector in invalid:
            with self.subTest(vector=vector), self.assertRaises(ValueError):
                validate_response({'data': [{'index': 0, 'embedding': vector}]}, 1, 2)
        for index in [1, -1, True, 0.0]:
            with self.subTest(index=index), self.assertRaises(ValueError):
                validate_response({'data': [{'index': index, 'embedding': [1, 0]}]}, 1, 2)
        with self.assertRaises(ValueError):
            validate_response({'data': [good['data'][0], good['data'][0]]}, 2, 2)

    def test_persistent_cache_exact_input_identity_and_invalidation(self):
        with tempfile.TemporaryDirectory() as name:
            config = fixture_settings()['steps']['embeddings']
            fixture = {'embedding_vectors': {stable_hash('dog D'): [1, 0], stable_hash('dog D '): [0, 1]}}
            adapter = EmbeddingAdapter(config, name, fixture)
            first = adapter.embed([{'id': 's1', 'text': 'dog D'}])
            second = EmbeddingAdapter(config, name, fixture)
            with patch.object(second, '_request', side_effect=AssertionError('cache must avoid calls')):
                cached = second.embed([{'id': 's1', 'text': 'dog D'}])
            self.assertTrue(cached[0]['cache_hit'])
            self.assertEqual(second.telemetry['requests'], 0)
            changed = copy.deepcopy(config)
            changed['preprocessing_version'] += '-changed'
            other = EmbeddingAdapter(changed, name, fixture).embed([{'id': 's1', 'text': 'dog D'}])
            self.assertNotEqual(first[0]['cache_key'], other[0]['cache_key'])
            other = adapter.embed([{'id': 's1', 'text': 'dog D '}])
            self.assertNotEqual(first[0]['cache_key'], other[0]['cache_key'])
            entry = Path(name) / (first[0]['cache_key'] + '.json')
            saved = json.loads(entry.read_text()); saved['vector'] = [0, 1]; entry.write_text(json.dumps(saved))
            with self.assertRaisesRegex(ValueError, 'cache integrity'):
                second.embed([{'id': 's1', 'text': 'dog D'}])

    def test_request_bounds_failure_and_embedding_singleton(self):
        with tempfile.TemporaryDirectory() as name:
            config = fixture_settings()['steps']['embeddings']
            config.update(max_request_inputs=1, retries=1)
            adapter = EmbeddingAdapter(config, name, {'embedding_vectors': {}})
            with patch('knowledge_foundry.embeddings.time.sleep'), self.assertRaisesRegex(ValueError, 'exhausted retries'):
                adapter.embed([{'id': 'dog', 'text': 'dog D'}])
            self.assertEqual(len(adapter.telemetry['errors']), 2)
            config['max_input_tokens'] = 1
            with patch.object(adapter, '_request', side_effect=AssertionError('no calls')):
                with self.assertRaisesRegex(ValueError, 'dog.*embedding tokens.*limit 1'):
                    adapter.embed([{'id': 'dog', 'text': 'dog D repeated words'}])

    def test_interruption_reuses_already_persisted_embeddings(self):
        with tempfile.TemporaryDirectory() as name:
            config = fixture_settings()['steps']['embeddings']; config['max_request_inputs'] = 1
            fixture = {'embedding_vectors': {stable_hash('dog'): [1, 0], stable_hash('cat'): [0, 1]}}
            adapter = EmbeddingAdapter(config, name, fixture)
            request = adapter._request
            def interrupted(texts):
                if texts == ['cat']:
                    raise KeyboardInterrupt()
                return request(texts)
            records = [{'id': 'dog', 'text': 'dog'}, {'id': 'cat', 'text': 'cat'}]
            with patch.object(adapter, '_request', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
                adapter.embed(records)
            resumed = EmbeddingAdapter(config, name, fixture)
            rows = resumed.embed(records)
            self.assertEqual([row['cache_hit'] for row in rows], [True, False])

    def test_openai_request_usage_and_cache_are_separate_from_generation_reasoning(self):
        from unittest.mock import MagicMock
        config = fixture_settings()['steps']['embeddings']; config.update(provider='openai', retries=0)
        response = MagicMock()
        response.read.return_value = json.dumps({'data': [
            {'index': 1, 'embedding': [0, 1]}, {'index': 0, 'embedding': [1, 0]}],
            'usage': {'prompt_tokens': 7}}).encode()
        with tempfile.TemporaryDirectory() as name, patch.dict('os.environ', {'OPENAI_API_KEY': 'test'}), \
                patch('knowledge_foundry.embeddings.urlopen') as send:
            send.return_value.__enter__.return_value = response
            adapter = EmbeddingAdapter(config, name)
            records = [{'id': 'dog', 'text': 'dog D'}, {'id': 'cat', 'text': 'cat'}]
            rows = adapter.embed(records)
            payload = json.loads(send.call_args.args[0].data)
            self.assertNotIn('reasoning', payload)
            self.assertEqual(payload['dimensions'], 2)
            self.assertEqual([r['vector'] for r in rows], [[1, 0], [0, 1]])
            self.assertEqual(adapter.telemetry['requests'], 1)
            self.assertEqual(adapter.telemetry['input_tokens'], 7)
            cached = EmbeddingAdapter(config, name); cached.embed(records)
            self.assertEqual(cached.telemetry['requests'], 0)
            self.assertEqual(cached.telemetry['cache_hits'], 2)
            self.assertEqual(cached.telemetry['estimated_cost_usd'], 0)

    def test_model_dimensions_and_fixture_changes_invalidate_cache(self):
        with tempfile.TemporaryDirectory() as name:
            config = fixture_settings()['steps']['embeddings']
            records = [{'id': 'dog', 'text': 'dog'}]
            fixture = {'embedding_vectors': {stable_hash('dog'): [1, 0]}}
            first = EmbeddingAdapter(config, name, fixture).embed(records)[0]
            changed = copy.deepcopy(config); changed['model'] = 'different-model'
            other = EmbeddingAdapter(changed, name, fixture).embed(records)[0]
            self.assertNotEqual(first['cache_key'], other['cache_key'])
            changed = copy.deepcopy(config); changed['dimensions'] = 1
            adapter = EmbeddingAdapter(changed, name, fixture)
            with self.assertRaisesRegex(ValueError, 'dimension mismatch'):
                adapter.embed(records)
            changed_fixture = {'embedding_vectors': {stable_hash('dog'): [0, 1]}}
            other = EmbeddingAdapter(config, name, changed_fixture).embed(records)[0]
            self.assertNotEqual(first['cache_key'], other['cache_key'])
