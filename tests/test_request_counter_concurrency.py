"""Concurrent exact counts retain cache integrity and isolated telemetry."""

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier, Lock
from unittest.mock import patch

from claim_fixtures import fixture_settings
from knowledge_foundry.generation_context import RequestCounter


class Response:
    def __init__(self, count):
        self.count = count

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self):
        return json.dumps({'input_tokens': self.count}).encode()


def counter(tmp_path):
    config = fixture_settings()
    config['steps']['claim_generation'].update(provider='openai', retries=0)
    return RequestCounter(config, tmp_path, api_key='fixture-only')


def test_identical_concurrent_payloads_make_one_call_and_preserve_cache(tmp_path):
    measured = counter(tmp_path)
    start = Barrier(4)
    payload = {'model': 'test', 'input': 'same exact input'}

    def count(_):
        start.wait(timeout=10)
        return measured.count(payload)

    with patch('knowledge_foundry.generation_context.urlopen', return_value=Response(123)) as send:
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(count, range(8))) == [123] * 8
    assert send.call_count == measured.telemetry['requests'] == 1
    assert measured.telemetry['cache_hits'] == 7
    assert measured.telemetry['failed_requests'] == 0
    with patch('knowledge_foundry.generation_context.urlopen', side_effect=AssertionError('cache miss')):
        assert counter(tmp_path).count(payload) == 123


def test_distinct_payloads_overlap_and_telemetry_counts_every_call(tmp_path):
    measured = counter(tmp_path)
    requests = Barrier(4)
    lock = Lock()
    active = maximum = 0

    def send(request, **_kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        try:
            requests.wait(timeout=10)
        finally:
            with lock:
                active -= 1
        return Response(int(json.loads(request.data)['input']) + 1)

    with patch('knowledge_foundry.generation_context.urlopen', side_effect=send):
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(measured.count, [{'input': str(i)} for i in range(8)])) == list(range(1, 9))
    assert maximum == 4
    assert measured.telemetry['requests'] == 8
    assert measured.telemetry['cache_hits'] == measured.telemetry['failed_requests'] == 0
    assert len(list(tmp_path.glob('*.json'))) == 8
