"""Bounded structured model boundary; Responses-compatible local servers are optional."""
import hashlib
from http.client import HTTPException
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import jsonschema

from knowledge_foundry.embeddings import token_count
from knowledge_foundry.openai_tools import _output_text, _usage_record, response_payload
from .config import ROOT, local_endpoint
from .diagnostics import exception_diagnostic


class ModelError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ModelError('model endpoint redirects are disabled')


def object_schema(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


TEXT_SCHEMA = object_schema({'text': {'type': 'string', 'minLength': 1}})
EVIDENCE_SCHEMA = object_schema({'message_id': {'type': 'string'},
                                 'quote': {'type': 'string', 'minLength': 1}})
ROUTE_SCHEMA = object_schema({
    'route': {'type': 'string', 'enum': ['reasoning', 'documents', 'general', 'clarify']},
    'language': {'type': 'string', 'enum': ['en', 'fr']},
    'general_requested': {'type': 'boolean'},
    'candidate_id': {'type': ['string', 'null']},
    'context_match': {'type': 'string', 'enum': ['supported_policy', 'ambiguous', 'unsupported', 'none']},
    'use_history': {'type': 'boolean'},
    'interpretation': {'type': 'string', 'minLength': 1},
    'clarification': {'type': ['string', 'null']},
    'predicate': {'type': ['string', 'null']},
    'args': {'type': 'array', 'items': {'type': 'string'}},
    'variables': {'type': 'array', 'items': object_schema({'name': {'type': 'string'}, 'type': {'type': 'string'}})},
    'select': {'type': 'array', 'items': {'type': 'string'}},
    'entities': {'type': 'array', 'maxItems': 48, 'items': object_schema({
        'name': {'type': 'string', 'minLength': 1}, 'type': {'type': 'string'}, 'evidence': EVIDENCE_SCHEMA})},
    'facts': {'type': 'array', 'maxItems': 96, 'items': object_schema({
        'predicate': {'type': 'string'}, 'args': {'type': 'array', 'items': {'type': 'string'}},
        'evidence': EVIDENCE_SCHEMA})},
    'question_evidence': {'anyOf': [EVIDENCE_SCHEMA, {'type': 'null'}]},
    'scope_evidence': {'anyOf': [object_schema({
        'company': {'type': 'string', 'minLength': 1},
        'institution': {'type': 'string', 'minLength': 1},
        'company_evidence': EVIDENCE_SCHEMA,
        'institution_evidence': EVIDENCE_SCHEMA,
        'date_evidence': EVIDENCE_SCHEMA,
    }), {'type': 'null'}]},
})

WORDING_SCHEMA = object_schema({
    'status': {'type': 'string', 'enum': ['entailed', 'unknown', 'conflict']},
    'answer': {'type': 'string', 'minLength': 1},
    'steps': {'type': 'array', 'items': object_schema({
        'proof_id': {'type': 'string'}, 'text': {'type': 'string', 'minLength': 1}})},
})


class LanguageModels:
    def __init__(self, config, transport=None):
        self.config, self.transport, self.records = config, transport, []

    def call(self, role, prompt_name, data, schema):
        step = self.config['steps'][role]
        if self.config['local_only'] and (step['provider'] != 'local' or not local_endpoint(step['endpoint'])):
            raise ModelError('cloud calls are disabled in local-only mode')
        prompt = (ROOT / 'src/knowledge_foundry/prompts' / prompt_name).read_text()
        payload = response_payload(step['model'], step['reasoning_effort'], prompt,
                                   json.dumps(data, ensure_ascii=False), role, schema, step['max_output_tokens'])
        payload['store'] = False
        if step['reasoning_effort'] is None:
            payload.pop('reasoning')
        size = token_count(json.dumps(payload, ensure_ascii=False), 'cl100k_base')
        if size > step['max_input_tokens']:
            raise ModelError('model request exceeds the configured input budget')
        record = {'role': role, 'provider': step['provider'], 'requested_model': step['model'],
                  'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(), 'input_tokens_estimate': size}
        started = time.monotonic()
        try:
            body = (self.transport or self._request)(payload, step)
            if isinstance(body, dict):
                record['response_status'] = body.get('status') if body.get('status') in {
                    'completed', 'incomplete', 'failed', 'cancelled', 'queued', 'in_progress'} else 'unknown'
                reason = (body.get('incomplete_details') or {}).get('reason')
                if reason in {'max_output_tokens', 'content_filter'}:
                    record['incomplete_reason'] = reason
            if not isinstance(body, dict) or body.get('status') != 'completed':
                raise ModelError('model response was refused or incomplete')
            record.update(model=body.get('model', step['model']), usage=_usage_record(body))
            pricing, usage = self.config.get('pricing', {}), record['usage']
            if step['provider'] == 'local':
                record['estimated_api_cost_usd'] = 0.0
            elif isinstance(body.get('usage'), dict) and pricing.get('model') == step['model']:
                cached = usage['cached_input_tokens']
                written = usage['cache_write_tokens']
                record['estimated_api_cost_usd'] = (
                    max(0, usage['input_tokens'] - cached - written) * pricing['input_usd_per_million'] +
                    cached * pricing['cached_input_usd_per_million'] + written * pricing['cache_write_usd_per_million'] +
                    usage['output_tokens'] * pricing['output_usd_per_million']) / 1_000_000
            else:
                record['estimated_api_cost_usd'] = None
            output = _output_text(body)
            if token_count(output, 'cl100k_base') > step['max_output_tokens']:
                raise ModelError('model output exceeds its budget')
            value = json.loads(output)
            jsonschema.validate(value, schema)
            record['status'] = 'completed'
            return value
        except (ValueError, TypeError, KeyError, jsonschema.ValidationError, RuntimeError) as exc:
            record['status'] = 'failed'
            record['diagnostic'] = exception_diagnostic(exc, role)
            raise ModelError('model output could not be validated') from exc
        finally:
            record['latency_seconds'] = round(time.monotonic() - started, 3)
            self.records.append(record)

    def _request(self, payload, step):
        headers = {'Content-Type': 'application/json'}
        key = os.environ.get(self.config['api_key_env']) if step['provider'] == 'openai' else None
        if step['provider'] == 'openai' and not key:
            raise ModelError('OPENAI_API_KEY is not configured in the launching shell')
        if key:
            headers['Authorization'] = 'Bearer ' + key
        request = Request(step['endpoint'], data=json.dumps(payload, ensure_ascii=False).encode(), headers=headers)
        try:
            handlers = [NoRedirect()]
            if step['provider'] == 'local':
                handlers.append(ProxyHandler({}))
            with build_opener(*handlers).open(request, timeout=step['timeout_seconds']) as response:
                # Explicit cap includes metadata and reasoning output; never retain raw bodies on disk.
                body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    raise ModelError('model response is too large')
                return json.loads(body)
        except (HTTPError, URLError, TimeoutError, OSError, HTTPException) as exc:
            raise ModelError('model service request failed; check provider configuration') from exc
