import json
import unittest
from unittest.mock import MagicMock, patch

from knowledge_foundry.openai_tools import DEFAULT_MODEL, OpenAIResponsesTool


class OpenAIToolsTests(unittest.TestCase):
    @patch("knowledge_foundry.openai_tools.urlopen")
    def test_json_response_sends_default_model_and_medium_reasoning(self, urlopen):
        response = MagicMock()
        response.read.return_value = json.dumps({"output_text": '{"topic_id":"topic:test"}', "usage": {
            "input_tokens": 100, "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": 30, "cache_write_tokens": 10}}}).encode("utf-8")
        urlopen.return_value.__enter__.return_value = response
        tool = OpenAIResponsesTool(api_key="test-key")
        value = tool.json_response(instructions="test", input_text="input", schema_name="result",
                                   schema={"type": "object"})
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual((tool.model, payload["model"], payload["reasoning"]),
                         ("gpt-6-luna", DEFAULT_MODEL, {"effort": "medium"}))
        self.assertEqual(value, {"topic_id": "topic:test"})
        self.assertEqual(tool.usage_records, [{"input_tokens": 100, "cached_input_tokens": 30,
                                                "cache_write_tokens": 10, "output_tokens": 20}])

    def test_complete_payload_counts_schema_metadata_and_output_reasoning_reserve(self):
        import tempfile
        from knowledge_foundry.generation_context import RequestCounter, generation_payload, usable_input_budget
        from test_claim_context import fixture_settings
        config = fixture_settings()
        config['steps']['claim_generation']['provider'] = 'openai'
        payload = generation_payload(config, '{"sources":["source metadata"],"eligible_evidence":["exact"]}',
                                     'instructions', {'type': 'object', 'properties': {'claims': {'type': 'array'}}})
        body = MagicMock()
        body.read.return_value = b'{"input_tokens":123}'
        with tempfile.TemporaryDirectory() as name, patch('knowledge_foundry.generation_context.urlopen') as send:
            send.return_value.__enter__.return_value = body
            counter = RequestCounter(config, name, api_key='test')
            self.assertEqual(counter.check(payload, 'cluster'), 123)
            sent = json.loads(send.call_args.args[0].data)
            self.assertEqual(sent, {key: value for key, value in payload.items() if key != 'max_output_tokens'})
            self.assertNotIn('max_output_tokens', sent)
            self.assertEqual(payload['max_output_tokens'], config['steps']['claim_generation']['budget']['reserved_output_tokens'])
            self.assertIn('schema', sent['text']['format'])
            self.assertEqual(counter.count(payload), 123)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(counter.telemetry['cache_hits'], 1)
            payload['instructions'] += ' retry feedback'
            counter.count(payload)
            self.assertEqual(send.call_count, 2)
        self.assertEqual(usable_input_budget(config['steps']['claim_generation']['budget']), 24000)

    def test_provider_counter_failure_is_explicit_and_bounded(self):
        import tempfile
        from knowledge_foundry.generation_context import RequestCounter
        from test_claim_context import fixture_settings
        config = fixture_settings(); config['steps']['claim_generation'].update(provider='openai', retries=1)
        with tempfile.TemporaryDirectory() as name, \
                patch('knowledge_foundry.generation_context.urlopen', side_effect=TimeoutError('offline')) as send, \
                patch('time.sleep'):
            counter = RequestCounter(config, name, api_key='test')
            with self.assertRaises(TimeoutError):
                counter.count({'model': config['steps']['claim_generation']['model'], 'input': 'test'})
            self.assertEqual(send.call_count, 2)
            self.assertEqual(counter.telemetry['failed_requests'], 2)

    def test_strict_token_count_endpoint_accepts_input_fields_and_generation_retains_output_limit(self):
        import copy
        import io
        import tempfile
        from urllib.error import HTTPError
        from knowledge_foundry.generation_context import RequestCounter, generation_payload
        from test_claim_context import fixture_settings
        config = fixture_settings()
        config['steps']['claim_generation'].update(provider='openai', retries=0)
        payload = generation_payload(config, '{"sources":["metadata"],"eligible_evidence":["exact text"]}',
                                     'complete instructions', {'type': 'object'})
        original = copy.deepcopy(payload)
        documented_fields = {'conversation', 'input', 'instructions', 'model', 'parallel_tool_calls',
                             'personality', 'previous_response_id', 'reasoning', 'text', 'tool_choice',
                             'tools', 'truncation'}
        def strict_endpoint(request, **kwargs):
            data = json.loads(request.data)
            unknown = set(data) - documented_fields
            if unknown:
                message = json.dumps({'error': {'message': 'Unknown parameter: ' + sorted(unknown)[0]}}).encode()
                raise HTTPError(request.full_url, 400, 'Bad Request', {}, io.BytesIO(message))
            self.assertEqual(data['input'], original['input'])
            self.assertEqual(data['instructions'], original['instructions'])
            self.assertEqual(data['text']['format']['schema'], original['text']['format']['schema'])
            response = MagicMock(); response.read.return_value = b'{"input_tokens":321}'
            context = MagicMock(); context.__enter__.return_value = response
            return context
        with tempfile.TemporaryDirectory() as name, patch('knowledge_foundry.generation_context.urlopen', side_effect=strict_endpoint):
            counter = RequestCounter(config, name, api_key='test-key')
            self.assertEqual(counter.check(payload, 'semantic cluster'), 321)
            self.assertEqual(counter.telemetry['requests'], 1)
        self.assertEqual(payload, original)
        self.assertEqual(payload['max_output_tokens'], 32768)

    def test_permanent_count_error_preserves_provider_details_without_retry_or_cache(self):
        import io
        import tempfile
        from pathlib import Path
        from urllib.error import HTTPError
        from knowledge_foundry.generation_context import RequestCounter
        from test_claim_context import fixture_settings
        config = fixture_settings(); config['steps']['claim_generation'].update(provider='openai', retries=2)
        body = {'error': {'message': 'Unknown parameter: max_output_tokens; credential=test-secret',
                          'code': 'unknown_parameter', 'param': 'max_output_tokens'}}
        error = HTTPError('https://api.openai.com/v1/responses/input_tokens', 400, 'Bad Request',
                          {'x-request-id': 'request-test'}, io.BytesIO(json.dumps(body).encode()))
        with tempfile.TemporaryDirectory() as name, \
                patch('knowledge_foundry.generation_context.urlopen', side_effect=error) as send, \
                patch('time.sleep') as sleep:
            counter = RequestCounter(config, name, api_key='test-secret')
            with self.assertRaisesRegex(RuntimeError, 'HTTP 400.*param=max_output_tokens.*request_id=request-test') as caught:
                counter.count({'model': 'test', 'input': 'private request contents'})
            self.assertIn('Unknown parameter: max_output_tokens', str(caught.exception))
            self.assertNotIn('test-secret', str(caught.exception))
            self.assertNotIn('private request contents', str(caught.exception))
            self.assertEqual(send.call_count, 1)
            sleep.assert_not_called()
            self.assertEqual(counter.telemetry['failed_requests'], 1)
            self.assertEqual(list(Path(name).iterdir()), [])

    def test_transient_count_error_retries_and_caches_success(self):
        import io
        import tempfile
        from urllib.error import HTTPError
        from knowledge_foundry.generation_context import RequestCounter
        from test_claim_context import fixture_settings
        config = fixture_settings(); config['steps']['claim_generation'].update(provider='openai', retries=1)
        for status in (429, 503):
            error = HTTPError('https://api.openai.com/v1/responses/input_tokens', status, 'transient', {},
                              io.BytesIO(b'{"error":{"message":"Try again later"}}'))
            response = MagicMock(); response.read.return_value = b'{"input_tokens":25}'
            context = MagicMock(); context.__enter__.return_value = response
            with self.subTest(status=status), tempfile.TemporaryDirectory() as name, \
                    patch('knowledge_foundry.generation_context.urlopen', side_effect=[error, context]) as send, \
                    patch('time.sleep') as sleep:
                counter = RequestCounter(config, name, api_key='test-key')
                payload = {'model': 'test', 'input': 'text'}
                self.assertEqual(counter.count(payload), 25)
                self.assertEqual(counter.count(payload), 25)
                self.assertEqual(send.call_count, 2)
                self.assertEqual(counter.telemetry['cache_hits'], 1)
                self.assertEqual(counter.telemetry['failed_requests'], 1)
                sleep.assert_called_once()

    def test_non_json_http_error_has_clear_bounded_fallback(self):
        import io
        import tempfile
        from urllib.error import HTTPError
        from knowledge_foundry.generation_context import RequestCounter
        from test_claim_context import fixture_settings
        config = fixture_settings(); config['steps']['claim_generation'].update(provider='openai', retries=0)
        error = HTTPError('https://api.openai.com/v1/responses/input_tokens', 400, 'Bad Request', None,
                          io.BytesIO(b'<html>proxy error</html>'))
        with tempfile.TemporaryDirectory() as name, patch('knowledge_foundry.generation_context.urlopen', side_effect=error):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 400.*provider error details unavailable'):
                RequestCounter(config, name, api_key='test-key').count({'model': 'test', 'input': 'text'})

    def test_counting_version_refreshes_old_counts_without_overwriting_them(self):
        import tempfile
        from pathlib import Path
        from knowledge_foundry.generation_context import RequestCounter
        from knowledge_foundry.structural_chunks import stable_hash
        from test_claim_context import fixture_settings
        config = fixture_settings(); config['steps']['claim_generation']['provider'] = 'openai'
        payload = {'model': 'test', 'input': 'full input', 'max_output_tokens': 100}
        legacy = {'version': 'response-input-count-v1', 'method': 'provider', 'payload': payload}
        saved = {'identity': legacy, 'input_tokens': 999, 'sha256': stable_hash([legacy, 999])}
        response = MagicMock(); response.read.return_value = b'{"input_tokens":20}'
        with tempfile.TemporaryDirectory() as name, patch('knowledge_foundry.generation_context.urlopen') as send:
            path = Path(name) / (stable_hash(legacy) + '.json'); path.write_text(json.dumps(saved))
            original = path.read_bytes()
            send.return_value.__enter__.return_value = response
            counter = RequestCounter(config, name, api_key='test-key')
            self.assertEqual(counter.count(payload), 20)
            self.assertEqual(counter.count(payload), 20)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(len(list(Path(name).glob('*.json'))), 2)
