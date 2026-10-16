"""Exact generation request construction and cached provider token counts."""

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from knowledge_foundry.embeddings import atomic_json
from knowledge_foundry.openai_tools import response_payload
from knowledge_foundry.structural_chunks import footnote_links, stable_hash

COUNT_VERSION = "response-input-count-v2"


def usable_input_budget(config):
    budget = config['context_window_tokens'] - config['reserved_output_tokens'] - config['safety_margin_tokens']
    cap = config['operational_input_cap_tokens']
    return min(budget, cap) if cap is not None else budget


def generation_payload(config, input_text, prompt, schema):
    config = config['steps']['claim_generation']
    return response_payload(config['model'], config['reasoning_effort'], prompt, input_text,
                            'claim_candidates', schema,
                            config['budget']['reserved_output_tokens'])


def _token_count_http_error(error, api_key):
    """Expose bounded provider diagnostics without request payloads or credentials."""
    message, code, parameter = 'provider error details unavailable', None, None
    try:
        body = json.loads(error.read(8192).decode('utf-8', errors='replace'))
        details = body.get('error') if isinstance(body, dict) else None
        if isinstance(details, dict):
            if isinstance(details.get('message'), str):
                message = details['message']
            code = details.get('code')
            parameter = details.get('param')
    except (OSError, ValueError, AttributeError):
        pass
    fields = [f'HTTP {error.code}']
    for label, value in (('code', code), ('param', parameter)):
        if isinstance(value, str) and value:
            fields.append(f'{label}={value}')
    request_id = error.headers.get('x-request-id') if error.headers else None
    if isinstance(request_id, str) and request_id:
        fields.append('request_id=' + request_id)
    diagnostic = f"OpenAI input token counting failed ({', '.join(fields)}): {message}"
    diagnostic = diagnostic.replace(api_key, '[redacted]')
    return RuntimeError(' '.join(diagnostic.split())[:2000])


class RequestCounter:
    """Count exact payloads; fixture byte estimates are explicitly offline-only."""
    def __init__(self, config, cache_dir, api_key=None):
        config = config['steps']['claim_generation']
        self.config, self.cache_dir, self.api_key = config, Path(cache_dir), api_key
        self.telemetry = {'requests': 0, 'cache_hits': 0, 'failed_requests': 0,
                          'estimated_cost_usd': None if config['provider'] == 'openai' else 0,
                          'method': 'provider' if config['provider'] == 'openai' else 'offline_utf8_byte_estimate'}

    def count(self, payload):
        identity = {'version': COUNT_VERSION, 'method': self.telemetry['method'], 'payload': payload}
        key = stable_hash(identity)
        path = self.cache_dir / (key + '.json')
        if path.exists():
            record = json.loads(path.read_text())
            if record.get('identity') != identity or type(record.get('input_tokens')) is not int or \
                    record['input_tokens'] < 1 or record.get('sha256') != stable_hash([identity, record['input_tokens']]):
                raise ValueError('token count cache integrity mismatch')
            self.telemetry['cache_hits'] += 1
            return record['input_tokens']
        if self.config['provider'] == 'fixture':
            count = len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        else:
            import os
            import time
            key_value = self.api_key or os.environ.get('OPENAI_API_KEY')
            if not key_value:
                raise ValueError('OPENAI_API_KEY is required for request token counting')
            for attempt in range(self.config['retries'] + 1):
                self.telemetry['requests'] += 1
                try:
                    request = Request('https://api.openai.com/v1/responses/input_tokens',
                                      # The counting endpoint accepts input fields, but not this output limit.
                                      data=json.dumps({key: value for key, value in payload.items()
                                                       if key != 'max_output_tokens'}, ensure_ascii=False).encode(),
                                      headers={'Authorization': 'Bearer ' + key_value, 'Content-Type': 'application/json'})
                    with urlopen(request, timeout=90) as response:
                        count = json.load(response).get('input_tokens')
                    if type(count) is not int or count < 1:
                        raise ValueError('invalid provider input token count')
                    break
                except HTTPError as exc:
                    self.telemetry['failed_requests'] += 1
                    failure = _token_count_http_error(exc, key_value)
                    retryable = exc.code in {408, 409, 429} or 500 <= exc.code <= 599
                    if not retryable or attempt == self.config['retries']:
                        raise failure from exc
                    time.sleep(min(2 ** attempt, 4))
                except Exception:
                    self.telemetry['failed_requests'] += 1
                    if attempt == self.config['retries']:
                        raise
                    time.sleep(min(2 ** attempt, 4))
        atomic_json(path, {'identity': identity, 'input_tokens': count,
                           'sha256': stable_hash([identity, count])})
        return count

    def check(self, payload, label):
        tokens = self.count(payload)
        limit = usable_input_budget(self.config['budget'])
        if tokens > limit:
            raise ValueError(f'{label}: complete generation request has {tokens} input tokens; limit {limit}; '
                             'inspect structural links or rebuild structural chunks and semantic clusters with a verified larger budget; '
                             'structural splitting is outside scope')
        return tokens
