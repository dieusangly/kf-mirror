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
PROMPT_VERSION = 'rag-answer-v1'
EXTRACTIVE_VERSION = 'extractive-v2'
PROMPT = (ROOT / 'src/knowledge_foundry/prompts/rag_answer_v1.txt').read_text()


def request_payload(config, question, history, evidence):
    # No auxiliary claim/summary text goes into the answer prompt.
    citable = [e for e in evidence if e['element_kind'] != 'heading']
    data = {'question': question, 'previous_user_questions': history,
            'governing_headings': [{'source_title': e['source']['title'], 'edition': e['source']['version'],
                                    'location': e['location'], 'text': e['text']}
                                   for e in evidence if e['element_kind'] == 'heading'],
            'evidence': [{'label': e['label'], 'source_title': e['source']['title'],
                          'edition': e['source']['version'], 'source_class': e['source']['class'],
                          'location': e['location'], 'capture_date': e['document']['retrieved_on'],
                          'text': e['text']} for e in citable]}
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
    labels = {e['label']: e for e in evidence if e['element_kind'] != 'heading'}
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


def excerpt_groups(evidence):
    """Quote coherent passage spans with qualifications, including list clauses."""
    groups = {}
    by_id = {e['id']: e for e in evidence}
    for e in evidence:
        if not e.get('is_context'):
            groups.setdefault(e.get('retrieval_passage', 'passage'), []).append(e)
    for group in sorted(groups.values(), key=lambda rows: min(e.get('retrieval_rank', 0) for e in rows)):
        group.sort(key=lambda e: e.get('retrieval_order', 0))
        context_ids = {k for e in group for k in e['context_ids']}
        visited = set()
        while context_ids - visited:
            for key in sorted(context_ids - visited):
                visited.add(key)
                if key in by_id:
                    context_ids.update(by_id[key]['context_ids'])
        group += [by_id[k] for k in sorted(visited) if k in by_id and by_id[k] not in group]
        excluded = {'heading', 'figure'}
        yield [e for e in group if e['element_kind'] not in excluded and not (
            e['element_kind'] == 'table' and (e.get('lexical_score', 0) == 0))]


def generate(config, payload, evidence, stream=None):
    started = time.monotonic()
    provider = config['answer']['provider']
    usage, identity, text = {}, EXTRACTIVE_VERSION, ''
    warnings = []
    if not evidence or not json.loads(payload['input'])['evidence']:
        result, citations, status = INSUFFICIENT, [], 'insufficient'
        identity = 'not called'
    elif provider == 'extractive':
        # A transparent, deterministic demonstration, not a synthetic LLM answer.
        selected, parts = [], []
        seen = set()
        for group in excerpt_groups(evidence):
            additions = [e for e in group if e['id'] not in seen]
            pieces = [e['text'] + ' [' + e['label'] + ']' for e in additions]
            if token_count('\n\n'.join(parts + pieces), config['budget']['tokenizer']) > config['budget']['output_tokens']:
                warnings.append('A complete source excerpt was omitted because the output token budget was full.')
                continue
            selected.extend(additions)
            parts.extend(pieces)
            seen.update(e['id'] for e in additions)
        # Quotes may contain brackets/URLs, which are source data rather than
        # model-produced syntax. Their citations are constructed by this code.
        result, citations, status = ('\n\n'.join(parts), selected, 'extractive') if parts else (INSUFFICIENT, [], 'insufficient')
        yield {'type': 'delta', 'text': result}
    else:
        identity = config['answer']['model']
        for event in (stream or openai_stream)(payload, config):
            if event['type'] == 'delta':
                text += event['text']
                if token_count(text, config['budget']['tokenizer']) > config['budget']['output_tokens']:
                    raise ValueError('answer exceeds output budget')
                yield event
            elif event['type'] == 'usage':
                usage, identity = event['usage'], event.get('model', identity)
        result, citations, status = validate_answer(text, evidence)
    pricing = config['answer'].get('pricing')
    cost = 0. if provider == 'extractive' or not evidence else None
    if provider == 'openai' and usage:
        cost = estimate_cost(usage, pricing, config['answer']['model'])
    yield {'type': 'answer', 'answer': {'text': result, 'citations': citations, 'status': status, 'warnings': sorted(set(warnings)),
           'telemetry': {'provider': provider, 'model': identity, 'usage': usage or None,
                         'requested_model': config['answer']['model'] if provider == 'openai' else EXTRACTIVE_VERSION,
                         'estimated_cost_usd': cost, 'pricing': pricing if cost else None,
                         'latency_seconds': round(time.monotonic() - started, 3),
                         'request_input_tokens': request_tokens(payload, config),
                         'prompt_version': PROMPT_VERSION, 'prompt_sha256': stable_hash(PROMPT)}}}


def estimate_cost(usage, pricing, model):
    """Apply only the configured, dated price card for the requested model."""
    if not usage or not pricing or pricing.get('model') != model:
        return None
    cached, written = usage.get('cached_input_tokens', 0), usage.get('cache_write_tokens', 0)
    return (max(0, usage['input_tokens'] - cached - written) * pricing['input_usd_per_million'] +
            cached * pricing['cached_input_usd_per_million'] + written * pricing['cache_write_usd_per_million'] +
            usage['output_tokens'] * pricing['output_usd_per_million']) / 1_000_000
