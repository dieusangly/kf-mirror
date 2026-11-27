"""Cached policy embeddings and exact cosine ranking, without customer assertions."""
from copy import deepcopy
from http.client import HTTPException
import json
import math
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from knowledge_foundry.shared.embeddings import EmbeddingAdapter, token_count, validate_response
from knowledge_foundry.shared.vectors import normalized
from knowledge_foundry.shared.hashing import stable_hash
from .config import ROOT, local_endpoint
from .llm import NoRedirect


def scenario_text(row):
    # Versioned policy-only inputs: never embed saved customer assertions.
    vocabulary = row.get('vocabulary', {})
    return '\n'.join([row['scope']['text']] + [s['text'] for s in row['policy_clauses']] +
                     [json.dumps(vocabulary, ensure_ascii=False, sort_keys=True)])


class ScenarioEmbeddings(EmbeddingAdapter):
    """Reuse the repository cache/batching with an optional local HTTP boundary."""
    def __init__(self, config, cache_dir, *, local_only=False):
        config = deepcopy(config)
        config['preprocessing_version'] += ':' + stable_hash(config['endpoint'])
        super().__init__(config, cache_dir)
        self.local_only = local_only
        self.queries = {}

    def embed_query(self, text):
        # User questions stay in memory; the persistent cache contains policies only.
        if text in self.queries:
            self.telemetry['cache_hits'] += 1
            return self.queries[text]
        tokens = token_count(text, self.config['tokenizer'])
        if not text.strip() or tokens > min(self.config['max_input_tokens'], self.config['max_request_tokens']):
            raise ValueError('question exceeds the embedding input budget')
        self.telemetry['requests'] += 1
        try:
            body = self._request([text])
            usage = body.get('usage', {}).get('prompt_tokens')
            if type(usage) is not int or usage < 0:
                self.telemetry['input_tokens'] = None
            elif self.telemetry['input_tokens'] is not None:
                self.telemetry['input_tokens'] += usage
            vector = validate_response(body, 1, self.config['dimensions'])[0]
        except Exception:
            self.telemetry['failed_requests'] += 1
            self.telemetry['input_tokens'] = None
            raise
        # Bound the session cache independently of conversation duration.
        if len(self.queries) >= 128:
            self.queries.pop(next(iter(self.queries)))
        self.queries[text] = vector
        return vector

    def _request(self, texts):
        if self.config['provider'] == 'openai':
            if self.local_only:
                raise ValueError('cloud embeddings are disabled in local-only mode')
            return super()._request(texts)
        if self.config['provider'] != 'local' or not local_endpoint(self.config['endpoint']):
            raise ValueError('local embeddings require a loopback endpoint')
        payload = {'model': self.config['model'], 'input': texts, 'encoding_format': 'float',
                   'dimensions': self.config['dimensions']}
        request = Request(self.config['endpoint'], data=json.dumps(payload).encode(),
                          headers={'Content-Type': 'application/json'})
        try:
            with build_opener(NoRedirect(), ProxyHandler({})).open(
                    request, timeout=self.config['timeout_seconds']) as response:
                body = response.read(8_000_001)
                if len(body) > 8_000_000:
                    raise ValueError('embedding response is too large')
                return json.loads(body)
        except (HTTPError, URLError, TimeoutError, OSError, HTTPException) as exc:
            raise ValueError('local embedding service request failed') from exc


class ScenarioSearch:
    def __init__(self, records, config, *, local_only=False, embedder=None):
        self.records = records
        self.embedder = embedder or ScenarioEmbeddings(config['embedding'], ROOT / config['cache_dir'],
                                                       local_only=local_only)
        self.vectors = None
        self.telemetry = {}

    def search(self, question, limit=6, preferred=None):
        started = time.monotonic()
        self.telemetry = {'method': 'vector', 'status': 'started'}
        before = deepcopy(self.embedder.telemetry)
        try:
            if self.vectors is None:
                inputs = [{'id': key, 'text': scenario_text(row)} for key, row in sorted(self.records.items())]
                self.vectors = {r['structural_chunk_id']: normalized(r['vector'])
                                for r in self.embedder.embed(inputs)}
            query = normalized(self.embedder.embed_query(question))
            if any(len(vector) != len(query) for vector in self.vectors.values()):
                raise ValueError('query and scenario embedding dimensions differ')
            scores = {key: math.fsum(a * b for a, b in zip(query, vector))
                      for key, vector in self.vectors.items()}
            # A follow-up may retain its policy, never a saved customer scenario.
            ranked = sorted(self.records, key=lambda key: (
                key != preferred, -scores[key], key))
            picked, seen = [], set()
            for key in ranked:
                row = self.records[key]
                identity = row['package_id'], row.get('policy_identity', row.get('case_id'))
                if identity in seen:
                    continue
                seen.add(identity)
                picked.append(key)
                if len(picked) == limit:
                    break
            self.telemetry.update(status='completed', candidates=[
                {'candidate_id': key, 'cosine_similarity': scores[key],
                 'conversation_context': key == preferred} for key in picked])
            return picked
        except Exception as exc:
            self.telemetry['status'] = 'failed'
            raise ValueError('Scenario vector search failed; check the embedding provider and cache.') from exc
        finally:
            usage = self.embedder.telemetry
            self.telemetry.update(provider=usage['provider'], model=usage['model'],
                                  latency_seconds=round(time.monotonic() - started, 3))
            for field in ('requests', 'cache_hits', 'input_tokens'):
                self.telemetry[field] = (usage[field] - before[field]
                                         if usage[field] is not None and before[field] is not None else None)
