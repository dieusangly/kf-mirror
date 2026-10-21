"""Bounded relevance selection over snapshot-scoped search candidates."""
import json
import time

from knowledge_foundry.embeddings import token_count
from knowledge_foundry.openai_tools import response_payload
from knowledge_foundry.retrieval.answering import estimate_cost, openai_stream, request_tokens
from knowledge_foundry.retrieval.config import ROOT
from knowledge_foundry.structural_chunks import stable_hash

PROMPT = (ROOT / 'src/knowledge_foundry/prompts/rag_rank_v1.txt').read_text()
SCHEMA = {'type': 'object', 'properties': {'selected_indices': {'type': 'array', 'items': {'type': 'integer'}}},
          'required': ['selected_indices'], 'additionalProperties': False}


def enabled(config):
    return config['search'].get('rerank', False) and config['answer']['provider'] == 'openai'


def rank_candidates(documents, question, config, stream=None):
    started = time.monotonic()
    limit = config['search']['top_k']
    if not enabled(config) or not documents:
        return documents[:limit], None
    payload = response_payload(config['answer']['model'], config['answer']['reasoning_effort'], PROMPT, '',
                               'relevant_records', SCHEMA, min(1024, config['budget']['output_tokens']))
    payload.update(store=False, stream=True)
    candidates, admitted = [], []
    # Source metadata is presentation context, not an automatically inferred filter.
    for doc in documents:
        row = {'index': len(admitted), 'kind': doc.meta['kind'], 'text': doc.content}
        row['source_ids'] = doc.meta['source_ids']
        row['source_titles'] = doc.meta.get('source_titles', [])
        trial = candidates + [row]
        payload['input'] = json.dumps({'question': question, 'maximum_results': limit, 'candidates': trial}, ensure_ascii=False)
        if request_tokens(payload, config) > config['budget']['input_tokens']:
            continue
        candidates, admitted = trial, admitted + [doc]
    payload['input'] = json.dumps({'question': question, 'maximum_results': limit, 'candidates': candidates}, ensure_ascii=False)
    if request_tokens(payload, config) > config['budget']['input_tokens']:
        raise ValueError('ranking prompt/question exceed the complete request budget')
    if not admitted:
        raise ValueError('no complete search candidate fits the ranking request budget; narrow the query or increase its budget')
    text, usage, model = '', {}, config['answer']['model']
    for event in (stream or openai_stream)(payload, config):
        if event['type'] == 'delta':
            text += event['text']
            if token_count(text, config['budget']['tokenizer']) > payload['max_output_tokens']:
                raise ValueError('ranking response exceeds output budget')
        elif event['type'] == 'usage':
            usage, model = event['usage'], event.get('model', model)
    value = json.loads(text)
    indices = value.get('selected_indices') if isinstance(value, dict) else None
    if not isinstance(indices, list) or len(indices) > limit or any(type(i) is not int or not 0 <= i < len(admitted) for i in indices) or len(set(indices)) != len(indices):
        raise ValueError('ranking returned invalid candidate identifiers')
    return [admitted[i] for i in indices], {
        'model': model, 'usage': usage or None,
        'estimated_cost_usd': estimate_cost(usage, config['answer'].get('pricing'), config['answer']['model']),
        'latency_seconds': round(time.monotonic() - started, 3), 'request_input_tokens': request_tokens(payload, config),
        'candidates_received': len(documents), 'candidates_admitted': len(admitted), 'selected': len(indices),
        'prompt_version': 'rag-rank-v1', 'prompt_sha256': stable_hash(PROMPT)}
