"""Concurrency limits, isolated request usage, deterministic artifacts and resume."""

import json
from threading import Barrier, Event, Lock, get_ident
from unittest.mock import patch

import pytest

from claim_fixtures import bundle, fixture_settings, prepare_claim_context
from knowledge_foundry import claim_generation_and_classification as claims
from knowledge_foundry.concurrency import run_bounded
from knowledge_foundry.structural_chunks import assemble_structural_chunks, stable_hash


@pytest.fixture
def pipeline(tmp_path):
    chunked = tmp_path / 'chunked'
    chunked.mkdir()
    source = bundle([('paragraph', f'Requirement {i}.', i + 1) for i in range(8)],
                    [[i] for i in range(8)])
    (chunked / 'artifact-bundle.json').write_text(json.dumps(source))
    config = fixture_settings()
    config['steps']['embeddings'].update(dimensions=8, expected_dimensions=8)
    for step in ('claim_generation', 'topic_classification'):
        config['steps'][step]['retries'] = 1
    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps(config))
    structural, _ = assemble_structural_chunks(source)
    fixture = {'embedding_vectors': {
        stable_hash(row['text']): [float(i == j) for j in range(8)]
        for i, row in enumerate(structural)}, 'candidate_clusters': {}, 'classifications': {}}
    for i, chunk in enumerate(source['chunks']):
        text = f'Claim {i}.'
        fixture['candidate_clusters'][chunk['id']] = {'claims': [{'text': text, 'evidence': ['E1']}]}
        fixture['classifications'][text] = 'topic:ongoing-vigilance' if i else None
    fixture_path = tmp_path / 'fixture.json'
    fixture_path.write_text(json.dumps(fixture))
    context = tmp_path / 'context'
    prepare_claim_context(chunked, context, config_path, tmp_path / 'cache', fixture_path)
    assert len(json.loads((context / 'semantic-clusters.json').read_text())) == 8
    return context, config_path, fixture_path, fixture


class Requests:
    def __init__(self, workers):
        self.barrier = Barrier(workers)
        self.lock = Lock()
        self.active = self.maximum = 0
        self.calls = []

    def visit(self, identity):
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.calls.append(identity)
        try:
            self.barrier.wait(timeout=10)
        finally:
            with self.lock:
                self.active -= 1


    def provider_factory(self, fixture):
        tracker = self

        class Provider(claims.FixtureProvider):
            def candidates(self, ids, *args):
                tracker.visit(tuple(ids))
                self.usage_records.append(usage())
                return super().candidates(ids, *args)

            def classify(self, context, *args):
                tracker.visit(context['claim'])
                self.usage_records.append(usage())
                return super().classify(context, *args)

        return lambda *_args: Provider(fixture)

def test_preparation_concurrency_is_bounded_and_cluster_plan_is_deterministic(tmp_path):
    from knowledge_foundry.generation_context import RequestCounter
    chunked = tmp_path / 'chunks'
    chunked.mkdir()
    source = bundle([('paragraph', f'Requirement {i}.', i + 1) for i in range(8)],
                    [[i] for i in range(8)])
    (chunked / 'artifact-bundle.json').write_text(json.dumps(source))
    settings = fixture_settings()
    settings['steps']['embeddings'].update(dimensions=8, expected_dimensions=8)
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(settings))
    structural, _ = assemble_structural_chunks(source)
    fixture = tmp_path / 'fixture.json'
    fixture.write_text(json.dumps({'embedding_vectors': {
        stable_hash(row['text']): [float(i == j) for j in range(8)]
        for i, row in enumerate(structural)}}))
    original_count = RequestCounter.count
    outputs = []
    for workers in (1, 4):
        tracker = Requests(workers)
        def measured(counter, payload):
            tracker.visit(json.loads(payload['input'])['original_chunks'][0]['chunk_id'])
            return original_count(counter, payload)
        output = tmp_path / f'context-{workers}'
        with patch.object(RequestCounter, 'count', measured):
            prepare_claim_context(chunked, output, config, tmp_path / f'cache-{workers}',
                                  fixture, workers=workers)
        assert tracker.maximum == workers
        assert len(tracker.calls) == 24  # singleton, independent group, final check
        outputs.append(output)
    for filename in ('semantic-clusters.json', 'structural-chunks.jsonl', 'embeddings.jsonl'):
        assert (outputs[0] / filename).read_bytes() == (outputs[1] / filename).read_bytes()


def usage():
    return {'input_tokens': 10, 'cached_input_tokens': 0, 'cache_write_tokens': 0, 'output_tokens': 5}


def test_default_four_workers_isolates_usage_and_preserves_outputs(pipeline, tmp_path):
    context, config, fixture_path, fixture = pipeline
    generated = []
    classified = []
    for workers in (1, 4):
        tracker = Requests(workers)
        output = tmp_path / f'generated-{workers}'
        options = {'workers': 1} if workers == 1 else {}  # Exercise the API default.
        with patch.object(claims, '_provider', side_effect=tracker.provider_factory(fixture)):
            report = claims.generate_claims(None, output, config, fixture_path, context_dir=context, **options)
        assert tracker.maximum == workers
        assert report['telemetry']['requests'] == 8
        assert report['telemetry']['usage']['input_tokens'] == 80
        assert report['claim_generation_request_token_counting']['frozen_counts_reused'] == 8
        claims._candidate_manifest(output)
        generated.append(output)

        tracker = Requests(workers)
        target = tmp_path / f'classified-{workers}'
        with patch.object(claims, '_provider', side_effect=tracker.provider_factory(fixture)):
            report = claims.classify_claims(generated[0], target, config, fixture_path=fixture_path, **options)
        assert tracker.maximum == workers
        assert report['telemetry']['requests'] == 8
        assert report['telemetry']['usage']['input_tokens'] == 80
        assert len(report['unclassified_candidate_ids']) == 1
        classified.append(target)
    for name in ('claim-candidates.jsonl', 'artifact-bundle.json', 'evidence-accounting.json',
                 'candidate-clusters.json'):
        assert (generated[0] / name).read_bytes() == (generated[1] / name).read_bytes()
    for name in ('claims.jsonl', 'artifact-bundle.json', 'chunks.jsonl'):
        assert (classified[0] / name).read_bytes() == (classified[1] / name).read_bytes()


@pytest.mark.parametrize('first_workers', [1, 4])
def test_generation_resume_changes_workers_without_repeating_saved_requests(pipeline, tmp_path, first_workers):
    context, config, fixture_path, fixture = pipeline
    output = tmp_path / 'resume-generation'

    def interrupt(event):
        if event['phase'] == 'generation' and event['state'] == 'progress' and event['completed'] == 1:
            raise KeyboardInterrupt()

    tracker = Requests(first_workers)
    with patch.object(claims, '_provider', side_effect=tracker.provider_factory(fixture)), pytest.raises(KeyboardInterrupt):
        claims.generate_claims(None, output, config, fixture_path, context_dir=context,
                               workers=first_workers, on_progress=interrupt)
    saved = next((output / 'generation-checkpoints').glob('*.json')).read_bytes()
    # Seven remaining requests can run with any new bound.
    tracker = Requests(1)
    with patch.object(claims, '_provider', side_effect=tracker.provider_factory(fixture)):
        report = claims.generate_claims(None, output, config, fixture_path, context_dir=context,
                                        workers=4 if first_workers == 1 else 1, resume=True)
    assert tuple(json.loads(saved)['chunk_ids']) not in tracker.calls
    assert len(tracker.calls) == 7
    assert report['telemetry']['requests'] == 8
    assert report['telemetry']['usage']['input_tokens'] == 80
    claims._candidate_manifest(output)


def test_classification_resume_changes_workers_without_repeating_saved_requests(pipeline, tmp_path):
    context, config, fixture_path, fixture = pipeline
    generated, output = tmp_path / 'generated', tmp_path / 'classified'
    claims.generate_claims(None, generated, config, fixture_path, context_dir=context)

    def interrupt(event):
        if event['phase'] == 'classification' and event['state'] == 'progress' and event['completed'] == 2:
            raise KeyboardInterrupt()

    tracker = Requests(1)
    with patch.object(claims, '_provider', side_effect=tracker.provider_factory(fixture)), pytest.raises(KeyboardInterrupt):
        claims.classify_claims(generated, output, config, fixture_path=fixture_path,
                               workers=1, on_progress=interrupt)
    first_calls = set(tracker.calls)
    tracker = Requests(1)
    with patch.object(claims, '_provider', side_effect=tracker.provider_factory(fixture)):
        report = claims.classify_claims(generated, output, config, fixture_path=fixture_path,
                                        workers=4, resume=True)
    assert not first_calls.intersection(tracker.calls)
    assert len(tracker.calls) == 6
    assert report['telemetry']['requests'] == 8
    assert report['telemetry']['usage']['input_tokens'] == 80


def test_parallel_retries_keep_feedback_and_usage_with_the_correct_request(pipeline, tmp_path):
    context, config, fixture_path, fixture = pipeline
    tracker = Requests(4)
    target_ids = next(iter(fixture['candidate_clusters']))

    class Provider(claims.FixtureProvider):
        def candidates(self, ids, prompt, schema):
            if not self.usage_records:
                tracker.visit(tuple(ids))
            self.usage_records.append(usage())
            if target_ids in ids and len(self.usage_records) == 1:
                return {'claims': [{'text': 'Invalid.', 'evidence': ['E999']}]}
            if target_ids in ids:
                assert 'unknown evidence label' in prompt
            return super().candidates(ids, prompt, schema)

        def classify(self, context, taxonomy, prompt, schema):
            if not self.usage_records:
                tracker.visit(context['claim'])
            self.usage_records.append(usage())
            if context['claim'] == 'Claim 0.' and len(self.usage_records) == 1:
                return {'topic_id': 'topic:unknown'}
            if context['claim'] == 'Claim 0.':
                assert 'uncurated topic ID' in prompt
            return super().classify(context, taxonomy, prompt, schema)

    events = []
    with patch.object(claims, '_provider', side_effect=lambda *_: Provider(fixture)), patch.object(claims.time, 'sleep'):
        generated = tmp_path / 'retried-generation'
        report = claims.generate_claims(None, generated, config, fixture_path, context_dir=context,
                                        on_progress=events.append)
        assert report['status'] == 'complete'
        assert report['telemetry']['requests'] == 9
        assert report['telemetry']['usage']['input_tokens'] == 90
        report = claims.classify_claims(generated, tmp_path / 'retried-classification', config,
                                        fixture_path=fixture_path, on_progress=events.append)
        assert report['status'] == 'complete'
        assert report['telemetry']['requests'] == 9
        assert report['telemetry']['usage']['input_tokens'] == 90
    assert len([event for event in events if event['state'] == 'retry']) == 2


def test_summaries_default_four_workers_respects_levels_and_combines_usage(pipeline, tmp_path):
    from knowledge_foundry import summaries
    context, _, _, _ = pipeline
    config = json.loads((claims.ROOT / 'config/summaries.json').read_text())
    config['steps']['embeddings'].update(provider='fixture', dimensions=2, expected_dimensions=2, retries=0)
    config['pricing']['providers']['fixture'] = config['pricing']['providers']['openai']
    config['steps']['summary_clusters'].update(max_levels=2, max_members=2)
    config_path = tmp_path / 'summary-config.json'
    config_path.write_text(json.dumps(config))
    clusters = json.loads((context / 'semantic-clusters.json').read_text())
    fixture = {'embedding_vectors': {stable_hash('Summary ' + cluster['id']): [1., 0.] for cluster in clusters}}
    fixture_path = tmp_path / 'summary-fixture.json'
    fixture_path.write_text(json.dumps(fixture))
    loaded = []

    for workers in (1, 4):
        tracker = Requests(workers)

        class Tool:
            def __init__(self, *_args, **_kwargs):
                self.usage_records = []

            def json_response(self, *, input_text, **_kwargs):
                value = json.loads(input_text)
                if not self.usage_records:
                    tracker.visit((value['level'], value['input_cluster_id']))
                self.usage_records.append(usage())
                if value['input_cluster_id'] == clusters[0]['id'] and len(self.usage_records) == 1:
                    return {'text': ' '}
                return {'text': 'Summary ' + value['input_cluster_id']}

        output = tmp_path / f'summaries-{workers}'
        options = {'workers': 1} if workers == 1 else {}
        with patch.object(summaries, 'OpenAIResponsesTool', Tool), \
                patch.object(summaries.RequestCounter, 'count', return_value=100), \
                patch.object(summaries.time, 'sleep'):
            report = summaries.generate_summaries(context, output, config_path,
                                                  tmp_path / f'summary-cache-{workers}', fixture_path, **options)
        assert tracker.maximum == workers
        assert [level for level, _ in tracker.calls] == [1] * 8 + [2] * 4
        assert report['generation']['requests'] == 13
        assert report['generation']['failed_requests'] == 1
        assert len(report['usage_records']) == 13
        assert sum(row['input_tokens'] for row in report['usage_records']) == 130
        loaded.append(summaries.load_summaries(output))
    assert loaded[0]['summaries'] == loaded[1]['summaries']
    assert loaded[0]['summary_clusters'] == loaded[1]['summary_clusters']
    assert loaded[0]['report']['usage_records'] == loaded[1]['report']['usage_records']


def test_bounded_coordinator_saves_fast_results_before_stragglers():
    release = Event()
    caller = get_ident()
    results = []

    def work(item):
        if item == 0:
            assert release.wait(timeout=10)
        return item

    def save(item):
        assert get_ident() == caller
        results.append(item)
        if item == 1:
            release.set()

    run_bounded(work, range(8), 4, save)
    assert results.index(1) < results.index(0)
    assert sorted(results) == list(range(8))


def test_interruption_stops_scheduling_remaining_requests():
    tracker = Requests(4)

    def save(_result):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        run_bounded(tracker.visit, range(100), 4, save)
    assert sorted(tracker.calls) == [0, 1, 2, 3]


@pytest.mark.parametrize('module_name,function,args', [
    ('generate_claims', 'generate_claims', ['--context-dir', 'context', '--output-dir', 'output']),
    ('classify_claims', 'classify_claims', ['--candidates-dir', 'candidates', '--output-dir', 'output']),
    ('generate_summaries', 'generate_summaries', ['--context-dir', 'context', '--output-dir', 'output']),
])
def test_cli_worker_default_override_and_validation(module_name, function, args, capsys):
    from importlib import import_module
    module = import_module('scripts.' + module_name)

    def execute(*_args, **kwargs):
        kwargs['on_progress']({'phase': 'complete', 'state': 'complete',
                               'telemetry': {'requests': 0, 'estimated_cost_usd': 0}})
        return {'status': 'complete'}

    with patch.object(module, function, side_effect=execute) as call:
        for options, expected in [([], 4), (['--workers', '2'], 2), (['--workers', '1'], 1)]:
            assert module.main(args + options) == 0
            assert call.call_args.kwargs['workers'] == expected
        for value in ('0', '-1', 'bad'):
            call.reset_mock()
            with pytest.raises(SystemExit) as error:
                module.main(args + ['--workers', value])
            assert error.value.code == 2
            call.assert_not_called()
    capsys.readouterr()


@pytest.mark.parametrize('workers', [0, -1, True, 1.5, '4'])
def test_invalid_workers_rejected_before_creating_outputs(tmp_path, workers):
    output = tmp_path / 'output'
    with pytest.raises(ValueError, match='workers must be a positive integer'):
        claims.generate_claims(None, output, workers=workers)
    with pytest.raises(ValueError, match='workers must be a positive integer'):
        claims.classify_claims(tmp_path / 'missing', output, workers=workers)
    from knowledge_foundry.summaries import generate_summaries
    with pytest.raises(ValueError, match='workers must be a positive integer'):
        generate_summaries(tmp_path / 'missing', output, workers=workers)
    assert not output.exists()
