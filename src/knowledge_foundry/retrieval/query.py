"""Bounded, language-independent conversational query rewriting."""
import json
import time

from knowledge_foundry.embeddings import token_count
from knowledge_foundry.openai_tools import response_payload
from knowledge_foundry.retrieval.answering import estimate_cost, openai_stream, request_tokens
from knowledge_foundry.retrieval.config import ROOT
from knowledge_foundry.structural_chunks import stable_hash

PROMPT = (ROOT / 'src/knowledge_foundry/prompts/rag_query_v1.txt').read_text()
SCHEMA = {'type': 'object', 'properties': {'query': {'type': 'string'}, 'uses_history': {'type': 'boolean'}},
          'required': ['query', 'uses_history'], 'additionalProperties': False}


def rewrite_query(config, question, history, stream=None):
    """Return the original standalone question or a validated contextual query.

    No language patterns, source aliases or domain identifiers drive this step.
    Offline requests deliberately use the current question and report the limit.
    Conversation text is never cached on disk by the rewriter.
    """
    original = {'query': question, 'history': [], 'telemetry': None, 'warnings': []}
    if not history:
        return original
    if config['answer']['provider'] != 'openai' and stream is None:
        return {**original, 'warnings': ['Follow-up rewriting is unavailable in source excerpt mode; ask a standalone question.']}
    started = time.monotonic()
    payload = response_payload(config['answer']['model'], config['answer']['reasoning_effort'], PROMPT,
                               json.dumps({'question': question, 'previous_user_questions': history}, ensure_ascii=False),
                               'retrieval_query', SCHEMA, min(config['budget']['max_question_tokens'], config['budget']['output_tokens']))
    payload.update(store=False, stream=True)
    if request_tokens(payload, config) > config['budget']['input_tokens']:
        raise ValueError('query rewrite exceeds the complete request budget')
    text, usage, model = '', {}, config['answer']['model']
    try:
        for event in (stream or openai_stream)(payload, config):
            if event['type'] == 'delta':
                text += event['text']
                if token_count(text, config['budget']['tokenizer']) > config['budget']['max_question_tokens']:
                    raise ValueError('query rewrite exceeds output budget')
            elif event['type'] == 'usage':
                usage, model = event['usage'], event.get('model', model)
        result = json.loads(text)
        if set(result) != {'query', 'uses_history'} or type(result['uses_history']) is not bool or not isinstance(result['query'], str):
            raise ValueError('invalid query rewrite')
        query = result['query'].strip() if result['uses_history'] else question
        if not query or token_count(query, config['budget']['tokenizer']) > config['budget']['max_question_tokens']:
            raise ValueError('invalid rewritten question length')
        original.update(query=query, history=history if result['uses_history'] else [])
    except (ValueError, TypeError, OSError):
        original['warnings'] = ['Follow-up rewriting failed; retrieval used only the current question. Ask a standalone question.']
    original['telemetry'] = {'model': model, 'usage': usage or None,
                             'estimated_cost_usd': estimate_cost(usage, config['answer'].get('pricing'), config['answer']['model']),
                             'latency_seconds': round(time.monotonic() - started, 3),
                             'request_input_tokens': request_tokens(payload, config),
                             'prompt_version': 'rag-query-v1', 'prompt_sha256': stable_hash(PROMPT)}
    return original
