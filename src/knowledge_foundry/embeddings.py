"""Small bounded embedding adapter and content-addressed persistent cache."""

import json
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen

from knowledge_foundry.semantic_clusters import normalized
from knowledge_foundry.structural_chunks import stable_hash


def atomic_json(path, value, *, indent=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=indent)
        if indent is not None:
            handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def token_count(text, encoding='cl100k_base'):
    import tiktoken
    return len(tiktoken.get_encoding(encoding).encode(text, disallowed_special=()))


def validate_response(body, count, dimensions):
    rows = body.get('data')
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError('embedding response count mismatch')
    result = {}
    for row in rows:
        index = row.get('index')
        vector = row.get('embedding')
        if type(index) is not int or not 0 <= index < count or index in result:
            raise ValueError('embedding response indices are invalid')
        if not isinstance(vector, list) or len(vector) != dimensions:
            raise ValueError('embedding response dimension mismatch')
        normalized(vector)
        result[index] = vector
    return [result[i] for i in range(count)]


class EmbeddingAdapter:
    def __init__(self, config, cache_dir, fixture=None, pricing=None, pricing_version=None):
        self.pricing = pricing
        self.pricing_version = pricing_version
        self.config, self.cache_dir, self.fixture = config, Path(cache_dir), fixture
        self.telemetry = {'provider': config['provider'], 'model': config['model'],
                          'requests': 0, 'failed_requests': 0, 'cache_hits': 0,
                          'input_tokens': 0, 'estimated_cost_usd': 0., 'errors': [], 'pricing_version': pricing_version, 'rate_card': pricing}

    def _request(self, texts):
        if self.config['provider'] == 'fixture':
            vectors = (self.fixture or {}).get('embedding_vectors', {})
            return {'data': [{'index': i, 'embedding': vectors[stable_hash(text)]}
                             for i, text in enumerate(texts)], 'usage': {'prompt_tokens': 0}}
        key = os.environ.get('OPENAI_API_KEY')
        if not key:
            raise ValueError('OPENAI_API_KEY is required for embeddings')
        payload = {'model': self.config['model'], 'input': texts, 'encoding_format': 'float'}
        if self.config['dimensions'] is not None:
            payload['dimensions'] = self.config['dimensions']
        request = Request('https://api.openai.com/v1/embeddings', data=json.dumps(payload).encode(),
                          headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        with urlopen(request, timeout=self.config['timeout_seconds']) as response:
            return json.load(response)

    def embed(self, records):
        config = self.config
        dimensions = config['dimensions'] or config['expected_dimensions']
        result, pending = {}, []
        # Preflight every indivisible input before making any embedding calls.
        for record in records:
            text = record['text']
            tokens = token_count(text, config['tokenizer'])
            limit = min(config['max_input_tokens'], config['max_request_tokens'])
            if not text.strip() or tokens > limit:
                raise ValueError(f"structural chunk {record['id']} has {tokens} embedding tokens; limit {limit}; "
                                 'inspect structural links or configure a verified larger embedding model; '
                                 'structural splitting is outside scope')
            identity = {'text': text, 'provider': config['provider'], 'model': config['model'],
                        'dimensions': config['dimensions'], 'expected_dimensions': dimensions,
                        'preprocessing_version': config['preprocessing_version'],
                        'fixture_sha256': stable_hash(self.fixture) if config['provider'] == 'fixture' else None}
            key = stable_hash(identity)
            path = self.cache_dir / (key + '.json')
            if path.exists():
                saved = json.loads(path.read_text())
                if saved.get('identity') != identity or saved.get('cache_key') != key or \
                        saved.get('vector_sha256') != stable_hash(saved.get('vector')):
                    raise ValueError('embedding cache integrity mismatch: ' + str(path))
                vector = validate_response({'data': [{'index': 0, 'embedding': saved['vector']}]}, 1, dimensions)[0]
                self.telemetry['cache_hits'] += 1
                result[record['id']] = self._record(record, vector, key, True)
            else:
                pending.append((record, identity, key, tokens))
        while pending:
            request, total = [], 0
            for item in pending:
                if len(request) >= config['max_request_inputs'] or total + item[3] > config['max_request_tokens']:
                    break
                request.append(item)
                total += item[3]
            vectors = None
            for attempt in range(config['retries'] + 1):
                body = None
                try:
                    if config['provider'] != 'fixture':
                        self.telemetry['requests'] += 1
                    body = self._request([item[0]['text'] for item in request])
                    usage = body.get('usage', {}).get('prompt_tokens')
                    if type(usage) is int and usage >= 0:
                        if self.telemetry['input_tokens'] is not None:
                            self.telemetry['input_tokens'] += usage
                    else:
                        self.telemetry['input_tokens'] = None
                    vectors = validate_response(body, len(request), dimensions)
                    break
                except Exception as exc:
                    if config['provider'] != 'fixture' and body is None:
                        self.telemetry['input_tokens'] = None
                    self.telemetry['failed_requests'] += config['provider'] != 'fixture'
                    self.telemetry['errors'].append(str(exc))
                    if attempt == config['retries']:
                        self._cost()
                        raise ValueError(f"embedding request exhausted retries for {[x[0]['id'] for x in request]}: {exc}") from exc
                    time.sleep(min(2 ** attempt, 4))
            for (record, identity, key, _), vector in zip(request, vectors):
                atomic_json(self.cache_dir / (key + '.json'), {'identity': identity, 'cache_key': key,
                            'vector': vector, 'vector_sha256': stable_hash(vector)})
                result[record['id']] = self._record(record, vector, key, False)
            pending = pending[len(request):]
        self._cost()
        return [result[record['id']] for record in records]

    def _cost(self):
        usage = self.telemetry['input_tokens']
        self.telemetry['estimated_cost_usd'] = (0. if self.config['provider'] == 'fixture' or usage == 0 else
            None if usage is None or self.pricing is None else
            usage * self.pricing['usd_per_million_input_tokens'] / 1_000_000)

    def _record(self, record, vector, key, hit):
        return {'structural_chunk_id': record['id'], 'input_sha256': stable_hash(record['text']),
                'cache_key': key, 'cache_hit': hit, 'vector': vector,
                'vector_sha256': stable_hash(vector), 'provider': self.config['provider'],
                'model': self.config['model'], 'dimensions': len(vector),
                'preprocessing_version': self.config['preprocessing_version']}
