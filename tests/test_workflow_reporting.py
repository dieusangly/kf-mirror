"""Synthetic telemetry tests accounting only; it never supplies pilot approval."""
import copy

import pytest

from knowledge_foundry import workflow as w
from knowledge_foundry.workflow_reporting import BUILD_REPORTS, build_report


@pytest.fixture
def reporting(tmp_path):
    state = {'run_id': 'fixture', 'created_at': w.now(), 'updated_at': w.now(), 'status': 'blocked',
             'identity': {}, 'stages': {}, 'history': [], 'elapsed_seconds': 12,
             'workflow_config': {'corpus': 'fixture-only', 'test_only': True},
             'paid_stages_started': 0, 'review_sessions_started': 0}

    def add(stage, data=None, *, attempt=1, status='complete'):
        directory = tmp_path / stage / f'attempt-{attempt}'
        path = directory / ('build/report.json' if stage in BUILD_REPORTS else 'stdout.log' if stage == 'index' else 'report.json')
        if data is not None:
            w.write(path, data)
        entry = {'directory': str(directory), 'attempt': attempt, 'executions': 1, 'status': status}
        state['stages'][stage] = entry
        return entry, path

    def summary():
        return build_report(state, w.STAGES, tmp_path / 'state.json', tmp_path / 'config.json', 12)
    return state, add, summary


def measured(cost, **fields):
    return {'estimated_cost_usd': cost, **fields}


def test_stage_costs_exclude_nested_upstream_copies_and_projections(reporting):
    state, add, summary = reporting
    add('structure', {'embeddings': measured(1), 'claim_generation_request_token_counting': measured(0)})
    add('generate', {'telemetry': measured(2), 'preparation': {'embeddings': measured(999)},
                     'projected_full_generation': measured(999)})
    add('classify', {'telemetry': measured(3), 'projected_full_build': measured(999)})
    add('summaries', {'generation': measured(4), 'embeddings': measured(.5), 'request_token_counting': measured(0)})
    add('index', {'embedding_usage': measured(.1), 'snapshot_id': 'fixture-ready'})
    add('retrieval', {'metrics': [{'hit_rate': 1}], 'rows': [{'embedding_usage': measured(.2),
                                                            'query_rewrite': measured(.3), 'ranking': None}]})
    report = summary()
    assert report['cost']['known_subtotal_usd'] == pytest.approx(11.1)
    assert report['cost']['estimated_total_usd'] == pytest.approx(11.1)
    assert report['cost']['complete'] and not report['published']
    assert report['test_only'] and not report['recorded_checks_complete']
    snapshot = next(s for s in report['stages'] if s['stage'] == 'index')['attempts'][0]
    assert snapshot['snapshot_id'] == 'fixture-ready'


@pytest.mark.parametrize('bad_cost', [None, True, float('nan'), -1])
def test_unknown_or_invalid_cost_is_not_zero(reporting, bad_cost):
    _, add, summary = reporting
    add('generate', {'telemetry': measured(1)})
    add('classify', {'telemetry': measured(bad_cost, requests=1)})
    cost = summary()['cost']
    assert cost['known_subtotal_usd'] == 1
    assert cost['estimated_total_usd'] is None and not cost['complete']
    assert cost['unpriced_components'][0]['stage'] == 'classify'


def test_review_sessions_keep_usage_and_unknown_cost_separate(reporting):
    _, add, summary = reporting
    entry, path = add('chunk_review', {'automatic_gate': 'pass', 'review_gate': 'pending_review',
                                      'gate': 'pending_review', 'telemetry': measured(999)}, status='pending')
    for number, cost in [(1, .25), (2, None)]:
        w.write(path.parent / f'session-{number}/measurement.json', {'cost_usd': cost,
            'resolved': {'settings': {'model': 'configured-review-model', 'model_reasoning_effort': 'high'}},
            'thread_ids': ['fixture-' + str(number)], 'usage': [{'input_tokens': 12, 'output_tokens': 3}]})
    report = summary()
    stage = next(s for s in report['stages'] if s['stage'] == 'chunk_review')
    assert stage['status'] == 'pending' and stage['attempts'][0]['gates']['gate'] == 'pending_review'
    assert stage['attempts'][0]['cost_components'][1]['usage'][0]['input_tokens'] == 12
    assert report['cost']['known_subtotal_usd'] == .25
    assert report['cost']['estimated_total_usd'] is None
    assert len(report['cost']['unpriced_components']) == 1


def test_external_review_cost_is_unknown_and_duplicate_measurements_are_not_recharged(reporting, tmp_path):
    state, add, summary = reporting
    add('generate', {'telemetry': measured(.75)})
    measurement = tmp_path / 'library-source-review/measurement.json'
    w.write(measurement, {'cost_usd': None, 'usage': None,
                         'resolved': {'settings': {'model': 'configured-review-model'}}})
    review = {'purpose': 'library_source_labels', 'measurement': str(measurement)}
    state['external_reviews'] = [review, copy.deepcopy(review)]
    report = summary()
    assert report['cost']['known_subtotal_usd'] == .75
    assert report['cost']['estimated_total_usd'] is None
    assert len(report['external_reviews']) == len(report['cost']['unpriced_components']) == 1
    w.write(measurement, {'cost_usd': .25, 'usage': {'input_tokens': 20},
                         'resolved': {'settings': {'model': 'configured-review-model'}}})
    assert summary()['cost']['estimated_total_usd'] == 1


def test_external_review_measurement_already_in_stage_is_counted_once(reporting):
    state, add, summary = reporting
    _, path = add('chunk_review', {})
    measurement = path.parent / 'session-1/measurement.json'
    w.write(measurement, {'cost_usd': .25, 'usage': {'input_tokens': 20},
                         'resolved': {'settings': {'model': 'configured-review-model'}}})
    state['external_reviews'] = [{'purpose': 'source_review', 'measurement': str(measurement)}]
    report = summary()
    assert report['cost']['estimated_total_usd'] == .25
    assert not report['external_reviews']


def test_repairs_and_resumes_count_cumulative_telemetry_once(reporting):
    state, add, summary = reporting
    old, _ = add('generate', {'telemetry': measured(2, requests=2, usage={'input_tokens': 20})})
    state['history'] = [{'stage': 'generate', **old}, {'stage': 'generate', **old}]
    current, path = add('generate', {'telemetry': measured(5, requests=5, usage={'input_tokens': 50}),
        'recovery': {'prior_telemetry': measured(2, requests=2, usage={'input_tokens': 20}),
                     'source_directory': old['directory']}}, attempt=2)
    before = copy.deepcopy(state)
    report = summary()
    assert report['cost']['known_subtotal_usd'] == 5  # Old $2 plus new $3, not $7.
    assert summary() == report and state == before
    row = next(s for s in report['stages'] if s['stage'] == 'generate')['attempts'][1]
    assert row['cost_components'][0]['usage']['input_tokens'] == 30
    current['executions'] = 2
    updated = w.read(path)
    updated['telemetry']['estimated_cost_usd'] = 7
    w.write(path, updated)
    assert summary()['cost']['known_subtotal_usd'] == 7  # Latest cumulative report replaces $5.


def test_failed_attempts_preserve_measured_cost_and_unreported_calls(reporting):
    _, add, summary = reporting
    entry, path = add('structure', status='interrupted')
    w.write(path.parent / 'failure.json', {'embeddings': measured(.4), 'error': 'fixture interruption'})
    report = summary()
    assert report['cost']['known_subtotal_usd'] == .4
    assert report['cost']['estimated_total_usd'] is None
    stage = next(s for s in report['stages'] if s['stage'] == 'structure')
    assert stage['attempts'][0]['report_path'].endswith('failure.json')


def test_recovery_does_not_recharge_token_counting(reporting):
    state, add, summary = reporting
    previous = {'telemetry': measured(1), 'claim_generation_request_token_counting': measured(.1)}
    old, _ = add('generate', previous)
    state['history'].append({'stage': 'generate', **old})
    add('generate', {'telemetry': measured(3), 'claim_generation_request_token_counting': measured(.3),
                    'recovery': {'prior_telemetry': previous['telemetry'],
                                 'prior_request_token_counting': previous['claim_generation_request_token_counting']}}, attempt=2)
    assert summary()['cost']['known_subtotal_usd'] == pytest.approx(3.3)


def test_older_state_and_malformed_review_measurement_keep_unknowns_visible(reporting):
    state, add, summary = reporting
    state.pop('workflow_config')
    _, path = add('claim_review', {})
    w.write(path.parent / 'session-1/measurement.json', {'resolved': None, 'cost_usd': None})
    report = summary()
    assert report['corpus'] is None and report['test_only'] is None
    assert report['reporting_issues'] and report['cost']['estimated_total_usd'] is None


def test_malformed_telemetry_is_visible_without_breaking_reporting(reporting):
    _, add, summary = reporting
    _, path = add('generate', {'telemetry': {}, 'recovery': 'malformed fixture'})
    report = summary()
    assert report['reporting_issues'] and report['cost']['estimated_total_usd'] is None
    path.write_text('truncated JSON')
    report = summary()
    assert report['reporting_issues'][0]['path'] == str(path)
    assert report['cost']['unpriced_components']


@pytest.mark.parametrize('flat', [False, True])
def test_summary_generation_uses_its_saved_build_rate_card(reporting, flat):
    _, add, summary = reporting
    usage = {'input_tokens': 10, 'cached_input_tokens': 2, 'cache_write_tokens': 1, 'output_tokens': 5}
    entry, path = add('summaries', {'generation': {'provider': 'openai', 'model': 'configured-test-model', 'requests': 1},
                               'usage_records': [usage], 'embeddings': measured(0), 'request_token_counting': measured(0)})
    if flat:
        destination = path.parent.parent / 'report.json'
        path.rename(destination)
        entry['bundle_directory'] = entry['directory']
        path = destination
    rates = {'input': 1, 'cached_input': .2, 'cache_write': .5, 'output': 2}
    card = {'short_context_limit_tokens': 100, 'short_context_usd_per_million_tokens': rates,
            'long_context_usd_per_million_tokens': rates}
    w.write(path.parent / 'build-manifest.json', {'config': {'steps': {'summary_generation': {
        'provider': 'openai', 'model': 'configured-test-model'}}, 'pricing': {'version': 'frozen-fixture',
        'providers': {'openai': {'models': {'configured-test-model': card}}}}}})
    report = summary()
    assert report['cost']['estimated_total_usd'] == pytest.approx(17.9 / 1_000_000)
    row = next(s for s in report['stages'] if s['stage'] == 'summaries')['attempts'][0]
    assert row['cost_components'][-1]['rate_card']['version'] == 'frozen-fixture'


def test_zero_request_cache_hits_are_free_but_missing_usage_is_unpriced(reporting):
    _, add, summary = reporting
    add('structure', {'embeddings': measured(0), 'claim_generation_request_token_counting':
                      {'requests': 0, 'cache_hits': 5, 'estimated_cost_usd': None}})
    assert summary()['cost']['estimated_total_usd'] == 0
    add('summaries', {'generation': {'requests': 1}, 'usage_records': []})
    assert summary()['cost']['estimated_total_usd'] is None


def test_reported_zero_is_unknown_when_calls_have_no_usage_measurement(reporting):
    _, add, summary = reporting
    add('generate', {'telemetry': measured(0, provider='openai', requests=1,
                                          usage={'input_tokens': 0, 'output_tokens': 0})})
    cost = summary()['cost']
    assert cost['estimated_total_usd'] is None
    assert 'without measured token usage' in cost['unpriced_components'][0]['reason']
