"""Reusable application boundary shared by the chatbot, CLIs and evaluation."""
import time

from knowledge_foundry.embeddings import EmbeddingAdapter, token_count
from knowledge_foundry.retrieval.answering import generate
from knowledge_foundry.retrieval.artifacts import profile
from knowledge_foundry.retrieval.config import ROOT, load_config
from knowledge_foundry.retrieval.pipeline import retrieval_pipeline


class DocumentAssistant:
    def __init__(self, store, config=None, *, embedding_fixture=None, answer_stream=None):
        self.store, self.config = store, config or load_config()
        self.embedding_fixture, self.answer_stream = embedding_fixture, answer_stream

    def retrieve(self, corpus, question, *, snapshot_id=None, history=None, method=None,
                 mode=None, source_ids=None, document_ids=None):
        started = time.monotonic()
        config = self.config
        method, mode = method or config['search']['method'], mode or config['search']['mode']
        if not question.strip() or token_count(question, config['budget']['tokenizer']) > config['budget']['max_question_tokens']:
            raise ValueError('question is empty or exceeds the configured question budget')
        snapshot = self.store.snapshot(corpus, snapshot_id)
        turns = config['search']['followup_turns']
        history = list(history or [])[-turns:] if turns else []
        while history and token_count('\n'.join(history), config['budget']['tokenizer']) > config['budget']['history_tokens']:
            history.pop(0)
        query = '\n'.join(history + [question])
        vector, embedding_usage = None, None
        if method in {'vector', 'combined'}:
            if snapshot['embedding_profile'] != profile(config['embedding']):
                raise ValueError('query embedding model/dimensions/preprocessing differ from snapshot')
            adapter = EmbeddingAdapter(config['embedding'], ROOT / config['cache_dir'], self.embedding_fixture,
                                       pricing=config.get('embedding_pricing'))
            vector = adapter.embed([{'id': 'query', 'text': query}])[0]['vector']
            embedding_usage = adapter.telemetry
        pipeline = retrieval_pipeline(self.store, snapshot, config)
        filters = {'source_ids': source_ids, 'document_ids': document_ids}
        output = pipeline.run({'retrieve': {'query': query, 'method': method, 'mode': mode, 'vector': vector, **filters},
                               'resolve': {'query': query, **filters},
                               'context': {'question': question, 'history': history, **filters}},
                              include_outputs_from={'retrieve', 'resolve'})
        return {**output['context']['context'], 'snapshot_id': snapshot['id'],
                'passage_ids': [d.id for d in output['resolve']['documents']],
                'navigation_ids': [d.id for d in output['retrieve']['documents']],
                'method': method, 'mode': mode, 'query': query,
                'embedding_usage': embedding_usage, 'latency_seconds': round(time.monotonic() - started, 3)}

    def stream(self, corpus, question, *, token, session_id, snapshot_id=None, **options):
        started = time.monotonic()
        snapshot = self.store.snapshot(corpus, snapshot_id)
        # Resolve and pin once, including when publication changes mid-request.
        sid = snapshot['id']
        messages = self.store.history(session_id, token, sid)
        history = [m['content'] for m in messages if m['role'] == 'user']
        retrieved = self.retrieve(corpus, question, snapshot_id=sid, history=history, **options)
        yield {'type': 'retrieval', 'retrieval': {k: v for k, v in retrieved.items() if k != 'payload'}}
        for event in generate(self.config, retrieved['payload'], retrieved['evidence'], self.answer_stream):
            if event['type'] != 'answer':
                yield event
                continue
            answer = {**event['answer'], 'snapshot_id': sid,
                      'retrieval': {k: v for k, v in retrieved.items() if k != 'payload'}}
            answer['telemetry']['total_latency_seconds'] = round(time.monotonic() - started, 3)
            answer['message_id'] = self.store.save_turn(session_id, token, sid, question, answer)
            yield {'type': 'answer', 'answer': answer}

    def ask(self, corpus, question, **options):
        for event in self.stream(corpus, question, **options):
            if event['type'] == 'answer':
                return event['answer']
