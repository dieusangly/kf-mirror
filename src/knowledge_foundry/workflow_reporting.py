"""Derived run summaries using explicit stage telemetry, never projected build costs."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path


BUILD_REPORTS = {'parse', 'chunk', 'structure', 'summaries', 'generate', 'classify'}
MODEL_STAGES = {'structure', 'summaries', 'generate', 'classify', 'index', 'retrieval'}
REVIEW_STAGES = {'parsing_review', 'chunk_review', 'claim_review'}


def load(path, issues):
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError('expected a JSON object')
        return value
    except (OSError, ValueError) as exc:
        issues.append({'path': str(path), 'reason': str(exc)})
        return {}


def amount(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def component(name, telemetry, path, reason=None):
    result = {k: telemetry[k] for k in ('provider', 'model', 'requests', 'failed_requests', 'cache_hits',
              'usage', 'input_tokens', 'rate_card', 'pricing_version') if k in telemetry}
    result.update(component=name, source_path=str(path), estimated_cost_usd=amount(telemetry.get('estimated_cost_usd')))
    if result['estimated_cost_usd'] is None and telemetry.get('requests') == 0:
        result['estimated_cost_usd'] = 0
    usage = telemetry.get('usage')
    token_counts = list(usage.values()) if isinstance(usage, dict) else []
    token_counts.append(telemetry.get('input_tokens'))
    if (result['estimated_cost_usd'] == 0 and (amount(telemetry.get('requests')) or 0) > 0
            and telemetry.get('provider') != 'fixture' and not any((amount(n) or 0) > 0 for n in token_counts)):
        result['estimated_cost_usd'] = None
        reason = 'requests were reported without measured token usage'
    if result['estimated_cost_usd'] is None:
        result['unpriced_reason'] = reason or 'USD estimate was not reported'
    return result


def summary_generation(report, bundle, issues):
    telemetry = dict(report.get('generation', report.get('telemetry', {})))
    if 'estimated_cost_usd' not in telemetry:
        if telemetry.get('requests') == 0:
            telemetry['estimated_cost_usd'] = 0
        elif report.get('usage_records'):
            saved = bundle / 'build-manifest.json'
            try:
                # Use the rates saved with this build, never today's possibly changed config.
                from knowledge_foundry.claim_context import pricing_for_step
                from knowledge_foundry.claim_generation_and_classification import _cost
                config = load(saved, issues)['config']
                rates = pricing_for_step(config, 'summary_generation')
                telemetry['estimated_cost_usd'] = _cost(report['usage_records'], rates)
                telemetry['usage'] = {k: sum(r[k] for r in report['usage_records']) for k in
                                      ('input_tokens', 'cached_input_tokens', 'cache_write_tokens', 'output_tokens')}
                telemetry['rate_card'] = {'version': config['pricing']['version'], 'rates': rates}
            except (KeyError, TypeError, ValueError) as exc:
                issues.append({'path': str(saved), 'reason': 'summary pricing unavailable: ' + str(exc)})
    return telemetry


def costs(stage, report, path, directory, issues, bundle=None):
    """Select each charge once; copied preparation/projections/review telemetry are excluded."""
    selected = {}
    if stage == 'structure':
        selected = {k: report[k] for k in ('embeddings', 'claim_generation_request_token_counting') if k in report}
    elif stage in {'generate', 'classify'}:
        selected = {k: report[k] for k in ('telemetry', 'claim_generation_request_token_counting') if k in report}
    elif stage == 'summaries':
        selected = {k: report[k] for k in ('embeddings', 'request_token_counting') if k in report}
        selected['generation'] = summary_generation(report, bundle or directory / 'build', issues)
    elif stage == 'index' and 'embedding_usage' in report:
        selected['embedding_usage'] = report['embedding_usage']
    elif stage == 'retrieval':
        for index, row in enumerate(report.get('rows', [])):
            if 'error' in row:
                selected[f'rows[{index}].unreported_calls'] = {}
            for key in ('embedding_usage', 'query_rewrite', 'ranking'):
                if isinstance(row.get(key), dict):
                    selected[f'rows[{index}].{key}'] = row[key]
    result = []
    for name, telemetry in selected.items():
        telemetry = dict(telemetry) if isinstance(telemetry, dict) else {}
        prior_key = {'telemetry': 'prior_telemetry',
                     'claim_generation_request_token_counting': 'prior_request_token_counting'}.get(name)
        prior = report.get('recovery', {}).get(prior_key) if stage == 'generate' and prior_key else None
        excluded = None
        if prior:
            current, previous = amount(telemetry.get('estimated_cost_usd')), amount(prior.get('estimated_cost_usd'))
            excluded = previous
            telemetry['estimated_cost_usd'] = max(0, current - previous) if (
                current is not None and previous is not None and current + 1e-12 >= previous) else None
            if type(telemetry.get('requests')) is int and type(prior.get('requests')) is int:
                telemetry['requests'] -= prior['requests']
            if isinstance(telemetry.get('usage'), dict) and isinstance(prior.get('usage'), dict):
                telemetry['usage'] = {k: v - prior['usage'].get(k, 0) for k, v in telemetry['usage'].items()}
        item = component(name, telemetry, path)
        if prior:
            item['excluded_recovery_cost_usd'] = excluded
            item['recovery_source_directory'] = report['recovery'].get('source_directory')
        result.append(item)
    if stage in MODEL_STAGES and not result:
        result.append(component('unreported_calls', {}, path, 'model-backed stage has no usable telemetry'))
    if stage in MODEL_STAGES and (report.get('errors') or any(t.get('failed_requests', 0) for t in selected.values() if isinstance(t, dict))):
        result.append(component('failure_usage_coverage', {}, path, 'failed calls may have usage absent from stage telemetry'))
    return result


def review_component(measurement, issues, name):
    saved = load(measurement, issues)
    resolved = saved.get('resolved') or {}
    settings = resolved.get('settings') if isinstance(resolved, dict) else None
    if not isinstance(settings, dict):
        issues.append({'path': str(measurement), 'reason': 'reviewer model settings are unavailable'})
        settings = {}
    telemetry = {'estimated_cost_usd': saved.get('cost_usd'), 'usage': saved.get('usage'), **settings}
    item = component(name, telemetry, measurement, 'independent reviewer USD cost is unavailable')
    item['thread_ids'] = saved.get('thread_ids', [])
    item['model_settings'] = settings or None
    return item


def attempt_report(stage, entry, current, issues):
    directory = Path(entry['directory'])
    bundle = Path(entry.get('bundle_directory', directory / 'build'))
    path = bundle / 'report.json' if stage in BUILD_REPORTS else directory / ('stdout.log' if stage == 'index' else 'report.json')
    data = load(path, issues) if path.exists() else {}
    failure = bundle / 'failure.json'
    if not data and failure.exists():
        path, data = failure, load(failure, issues)
    row = {k: entry[k] for k in ('attempt', 'status', 'executions', 'started_at', 'finished_at', 'returncode',
                                 'error', 'invalidated_at', 'repair_note', 'execution_seconds') if k in entry}
    row.update(current=current, directory=str(directory), report_path=str(path) if path.exists() else None,
               bundle_directory=str(bundle) if bundle.is_dir() else None,
               gates={k: data[k] for k in ('automatic_gate', 'review_gate', 'gate') if k in data})
    if 'metrics' in data:
        row['retrieval_metrics'] = data['metrics']
    if 'snapshot_id' in data:
        row['snapshot_id'] = data['snapshot_id']
    if stage in REVIEW_STAGES:
        row['review_path'] = str(directory / 'review.json') if (directory / 'review.json').exists() else None
        components = []
        for measurement in sorted(directory.glob('session-*/measurement.json')):
            components.append(review_component(measurement, issues, measurement.parent.name))
        if not components:
            components.append(component('unreported_review', {}, directory, 'independent review usage was not recorded'))
    else:
        try:
            components = costs(stage, data, path, directory, issues, bundle)
        except (AttributeError, KeyError, TypeError, ValueError, ArithmeticError) as exc:
            issues.append({'path': str(path), 'reason': 'unusable telemetry: ' + str(exc)})
            components = [component('unusable_telemetry', {}, path, 'stage telemetry could not be interpreted')]
        if stage in MODEL_STAGES and entry.get('status') in {'running', 'interrupted', 'failed'}:
            components.append(component('unfinished_execution', {}, directory, 'unfinished execution may include unreported calls'))
    row['cost_components'] = components
    row['known_cost_subtotal_usd'] = sum(c['estimated_cost_usd'] for c in components if c['estimated_cost_usd'] is not None)
    row['cost_complete'] = all(c['estimated_cost_usd'] is not None for c in components)
    return row


def build_report(state, stage_order, state_path, config_path, elapsed_seconds):
    issues, stages, unpriced = [], [], []
    subtotal = 0
    for name in stage_order:
        attempts = {e['directory']: (e, False) for e in state.get('history', []) if e.get('stage') == name}
        current = state['stages'].get(name)
        if current:
            attempts[current['directory']] = (current, True)
        rows = [attempt_report(name, entry, active, issues) for entry, active in attempts.values()]
        for row in rows:
            subtotal += row['known_cost_subtotal_usd']
            unpriced.extend({'stage': name, 'attempt': row.get('attempt'), 'component': c['component'],
                             'source_path': c['source_path'], 'reason': c['unpriced_reason']}
                            for c in row['cost_components'] if c['estimated_cost_usd'] is None)
        stages.append({'stage': name, 'status': current['status'] if current else 'not_started',
                       'attempt_count': len(rows), 'attempts': rows,
                       'known_cost_subtotal_usd': sum(r['known_cost_subtotal_usd'] for r in rows),
                       'cost_complete': all(r['cost_complete'] for r in rows)})
    external_reviews = []
    accounted_paths = {str(Path(c['source_path']).resolve()) for stage in stages
                       for row in stage['attempts'] for c in row['cost_components']}
    for review in state.get('external_reviews', []):
        measurement = Path(review['measurement'])
        if str(measurement.resolve()) in accounted_paths:
            continue
        accounted_paths.add(str(measurement.resolve()))
        item = review_component(measurement, issues, review.get('purpose', 'external_review'))
        external_reviews.append({**review, 'cost_component': item})
        if item['estimated_cost_usd'] is None:
            unpriced.append({'stage': 'external_review', 'component': item['component'],
                             'source_path': item['source_path'], 'reason': item['unpriced_reason']})
        else:
            subtotal += item['estimated_cost_usd']
    recorded_config = state.get('workflow_config', {})
    return {'schema_version': 1, 'run_id': state['run_id'], 'corpus': recorded_config.get('corpus'),
            'execution_mode': state.get('execution_mode', 'automated'),
            'test_only': bool(recorded_config.get('test_only')) if recorded_config else None, 'status': state['status'],
            'created_at': state['created_at'], 'updated_at': state.get('updated_at'),
            'elapsed_seconds': elapsed_seconds, 'state_path': str(state_path), 'workflow_config_path': str(config_path),
            'recorded_input_identity_sha256': hashlib.sha256(json.dumps(state['identity'], sort_keys=True).encode()).hexdigest(),
            'corpus_readiness': state.get('corpus_readiness'),
            'recorded_checks_complete': all(state['stages'].get(s, {}).get('status') == 'complete' for s in stage_order if s != 'publish'),
            'published': state['stages'].get('publish', {}).get('status') == 'complete',
            'reviewer': state.get('reviewer'),
            'last_error': state.get('last_error') if state['status'] == 'blocked' else None,
            'stage_counts': dict(Counter(s['status'] for s in stages)), 'stages': stages,
            'execution_budget': (state.get('budget_history') or [None])[-1],
            'paid_stages_started': state['paid_stages_started'], 'review_sessions_started': state['review_sessions_started'],
            'external_reviews': external_reviews,
            'cost': {'currency': 'USD', 'known_subtotal_usd': subtotal, 'complete': not unpriced,
                     'estimated_total_usd': subtotal if not unpriced else None, 'unpriced_components': unpriced,
                     'scope': 'Reported model/API usage for this run, including prior attempts; excludes hardware/subscription charges.',
                     'basis': 'Stage estimates and saved build rate cards; not a billing statement or future-work projection.'},
            'reporting_issues': issues}
