"""Bounded answer requests, streamed drafts and fail-closed citation validation."""
import json
import os
import re
import time
from urllib.request import Request, urlopen

from knowledge_foundry.embeddings import token_count
from knowledge_foundry.openai_tools import RESPONSES_URL, _usage_record
from knowledge_foundry.retrieval.config import ROOT
from knowledge_foundry.structural_chunks import stable_hash

INSUFFICIENT = 'The selected sources do not provide sufficient evidence to answer this question.'
PROMPT = (ROOT / 'src/knowledge_foundry/prompts/rag_answer_v1.txt').read_text()


def request_payload(config, question, history, evidence):
    # No auxiliary claim/summary text goes into the answer prompt.
    data = {'question': question, 'previous_user_questions': history,
            'evidence': [{'label': e['label'], 'source_title': e['source']['title'],
                          'edition': e['source']['version'], 'source_class': e['source']['class'],
                          'location': e['location'], 'capture_date': e['document']['retrieved_on'],
                          'text': e['text']} for e in evidence]}
    return {'model': config['answer']['model'], 'instructions': PROMPT,
            'input': json.dumps(data, ensure_ascii=False), 'store': False,
            'reasoning': {'effort': config['answer']['reasoning_effort']},
            'max_output_tokens': config['budget']['output_tokens'],
            'truncation': 'disabled', 'stream': True}


def request_tokens(payload, config):
    return token_count(json.dumps(payload, ensure_ascii=False), config['budget']['tokenizer'])


def validate_answer(text, evidence):
    """Validate every label and require citations on each substantive paragraph.

    This checks citation identity/coverage, not semantic entailment. Evidence
    support still requires evaluation or human review.
    """
    if text.strip() == 'INSUFFICIENT_EVIDENCE':
        return INSUFFICIENT, [], 'insufficient'
    labels = {e['label']: e for e in evidence}
    cited = list(dict.fromkeys(re.findall(r'\[(E\d+)\]', text)))
    malformed = re.findall(r'\[([^\]]+)\]', text)
    if not text.strip() or not cited or any(c not in labels for c in cited) or any(c not in labels for c in malformed):
        return INSUFFICIENT, [], 'citation_validation_failed'
    # Forbid arbitrary links/HTML in persisted model output. The UI constructs
    # citation links itself after this validation.
    if re.search(r'https?://|<[^>]+>|\]\(', text):
        return INSUFFICIENT, [], 'citation_validation_failed'
    if any(not re.search(r'\[E\d+\]', p) for p in text.split('\n\n') if p.strip()):
        return INSUFFICIENT, [], 'citation_validation_failed'
    return text.strip(), [labels[c] for c in cited], 'draft'


def openai_stream(payload, config):
    key = os.environ.get('OPENAI_API_KEY')
    if not key:
        raise ValueError('OPENAI_API_KEY is required for OpenAI answers')
    request = Request(RESPONSES_URL, data=json.dumps(payload).encode(),
                      headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    complete = False
    with urlopen(request, timeout=config['answer']['timeout_seconds']) as response:
        for line in response:
            if not line.startswith(b'data: '):
                continue
            raw = line[6:].strip()
            if raw == b'[DONE]':
                continue
            event = json.loads(raw)
            if event['type'] == 'response.output_text.delta':
                yield {'type': 'delta', 'text': event['delta']}
            elif event['type'] == 'response.completed':
                complete = True
                yield {'type': 'usage', 'usage': _usage_record(event['response']),
                       'model': event['response'].get('model', payload['model'])}
            elif event['type'] in {'response.failed', 'response.incomplete', 'error'}:
                raise ValueError('answer provider did not complete the response')
    if not complete:
        raise ValueError('answer stream ended before completion')


def generate(config, payload, evidence, stream=None):
    started = time.monotonic()
    provider = config['answer']['provider']
    usage, identity, text = {}, 'extractive-v1', ''
    if not evidence:
        result, citations, status = INSUFFICIENT, [], 'insufficient'
    elif provider == 'extractive':
        # A transparent, deterministic demonstration, not a synthetic LLM answer.
        selected, parts = [], []
        for e in evidence:
            if e.get('is_context') or e['element_kind'] in {'heading', 'figure'}:
                continue
            piece = e['text'] + ' [' + e['label'] + ']'
            if token_count('\n\n'.join(parts + [piece])) <= config['budget']['output_tokens']:
                selected.append(e)
                parts.append(piece)
            if len(parts) >= 2:
                break
        # Quotes may contain brackets/URLs, which are source data rather than
        # model-produced syntax. Their citations are constructed by this code.
        result, citations, status = ('\n\n'.join(parts), selected, 'extractive') if parts else (INSUFFICIENT, [], 'insufficient')
        yield {'type': 'delta', 'text': result}
    else:
        identity = config['answer']['model']
        for event in (stream or openai_stream)(payload, config):
            if event['type'] == 'delta':
                text += event['text']
                if token_count(text) > config['budget']['output_tokens']:
                    raise ValueError('answer exceeds output budget')
                yield event
            elif event['type'] == 'usage':
                usage, identity = event['usage'], event.get('model', identity)
        result, citations, status = validate_answer(text, evidence)
    pricing = config['answer'].get('pricing')
    cost = 0. if provider == 'extractive' or not evidence else None
    if provider == 'openai' and usage and pricing and pricing.get('model') == config['answer']['model']:
        cached = usage.get('cached_input_tokens', 0)
        written = usage.get('cache_write_tokens', 0)
        cost = (max(0, usage['input_tokens'] - cached - written) * pricing['input_usd_per_million'] +
                cached * pricing['cached_input_usd_per_million'] +
                written * pricing['cache_write_usd_per_million'] +
                usage['output_tokens'] * pricing['output_usd_per_million']) / 1_000_000
    yield {'type': 'answer', 'answer': {'text': result, 'citations': citations, 'status': status,
           'telemetry': {'provider': provider, 'model': identity, 'usage': usage or None,
                         'requested_model': config['answer']['model'] if provider == 'openai' else 'extractive-v1',
                         'estimated_cost_usd': cost, 'pricing': pricing if cost else None,
                         'latency_seconds': round(time.monotonic() - started, 3),
                         'request_input_tokens': request_tokens(payload, config),
                         'prompt_version': 'rag-answer-v1', 'prompt_sha256': stable_hash(PROMPT)}}}
