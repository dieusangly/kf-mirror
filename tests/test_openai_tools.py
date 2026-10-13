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
                         ("gpt-6.1-sol", DEFAULT_MODEL, {"effort": "medium"}))
        self.assertEqual(value, {"topic_id": "topic:test"})
        self.assertEqual(tool.usage_records, [{"input_tokens": 100, "cached_input_tokens": 30,
                                                "cache_write_tokens": 10, "output_tokens": 20}])
