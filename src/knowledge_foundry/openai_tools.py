"""Minimal OpenAI Responses API client for structured compilation outputs."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_MODEL = "gpt-6.1-sol"
RESPONSES_URL = "https://api.openai.com/v1/responses"
REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}


class OpenAIResponseError(RuntimeError):
    """The configured OpenAI request could not produce a structured response."""


def _output_text(response):
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    raise OpenAIResponseError("OpenAI response has no output text")


def _usage_record(response):
    usage = response.get("usage")
    details = usage.get("input_tokens_details", {}) if isinstance(usage, dict) else {}

    def count(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    return {"input_tokens": count(usage.get("input_tokens")) if isinstance(usage, dict) else 0,
            "cached_input_tokens": count(details.get("cached_tokens")),
            "cache_write_tokens": count(details.get("cache_write_tokens")),
            "output_tokens": count(usage.get("output_tokens")) if isinstance(usage, dict) else 0}


class OpenAIResponsesTool:
    """Call the Responses API without making the SDK a runtime dependency."""

    def __init__(self, model=DEFAULT_MODEL, reasoning_effort="medium", api_key=None,
                 url=RESPONSES_URL, timeout=90):
        self.model = model
        if reasoning_effort not in REASONING_EFFORTS:
            raise ValueError("reasoning_effort is not supported")
        self.reasoning_effort = reasoning_effort
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.url = url
        self.timeout = timeout
        self.usage_records = []
        if not self.api_key:
            raise OpenAIResponseError("OPENAI_API_KEY is required for provider openai")

    def json_response(self, *, instructions, input_text, schema_name, schema):
        payload = {
            "model": self.model,
            "reasoning": {"effort": self.reasoning_effort},
            "instructions": instructions,
            "input": input_text,
            "text": {"format": {"type": "json_schema", "name": schema_name,
                                "strict": True, "schema": schema}},
        }
        request = Request(self.url, data=json.dumps(payload).encode("utf-8"), method="POST",
                          headers={"Authorization": f"Bearer {self.api_key}",
                                   "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise OpenAIResponseError(f"OpenAI request failed: {exc}") from exc
        self.usage_records.append(_usage_record(body))
        try:
            return json.loads(_output_text(body))
        except json.JSONDecodeError as exc:
            raise OpenAIResponseError("OpenAI response was not JSON") from exc
