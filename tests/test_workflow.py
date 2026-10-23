"""Controller fixtures test mechanics only; they never approve the real pilot."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_foundry import workflow as w


def limits(**changes):
    return SimpleNamespace(**({'allow_paid': True, 'max_seconds': 3600, 'max_paid_stages': 20,
                              'max_review_sessions': 6, 'max_stage_attempts': 3} | changes))


def test_reviewer_runtime_preserves_configuration_and_cleans_private_files(tmp_path, monkeypatch):
    configured = tmp_path / 'configured-home'
    configured.mkdir()
    (configured / 'config.toml').write_text('model = "configured-example"\n')
    (configured / 'auth.json').write_text('{"test_credential": "not-a-real-secret"}')
    (configured / 'sessions').mkdir()
    monkeypatch.setenv('CODEX_HOME', str(configured))
    before = w.fingerprints(configured)
    with pytest.raises(RuntimeError, match='review failed'):
        with w.reviewer_environment('independent-library-audit') as env:
            runtime = Path(env['CODEX_HOME'])
            assert runtime != configured
            assert (runtime.stat().st_mode & 0o777) == 0o700
            assert w.digest(runtime / 'config.toml') == w.digest(configured / 'config.toml')
            assert (runtime / 'auth.json').stat().st_mode & 0o777 == 0o600
            assert not (runtime / 'sessions').exists()
            (runtime / 'auth.json').write_text('refreshed test credential')
            raise RuntimeError('review failed')
    assert not runtime.exists()
    assert w.fingerprints(configured) == before
    assert w.os.environ['CODEX_HOME'] == str(configured)


@pytest.fixture
def controller(tmp_path):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'data').mkdir()
    (tmp_path / 'requirements.txt').write_text('')
    (tmp_path / 'data/source.md').write_text('# Fixture\n\nFixture policy only.')
    w.write(tmp_path / 'data/manifest.json', {'schema_version': '1.0.0', 'sources': [{'manifest_id': 'X',
            'source_id': 'src:fixture', 'local_path': 'data/source.md', 'title': 'Fixture', 'class': 'test',
            'source_version': 'test-v1', 'publisher_url': 'https://example.org/fixture', 'retrieved_on': '2026-10-22',
            'media_type': 'text/markdown', 'size_bytes': (tmp_path / 'data/source.md').stat().st_size,
            'sha256': w.digest(tmp_path / 'data/source.md')}]})
    w.write(tmp_path / 'config/pipeline.json', {'root': '..', 'manifest_file': 'data/manifest.json', 'manifest_ids': ['X']})
    for name in ('structural', 'claims', 'summaries', 'retrieval', 'taxonomy'):
        w.write(tmp_path / 'config' / (name + '.json'), {})
    w.write(tmp_path / 'cases.json', [{'id': 'fixture-only'}])
    config = {'pipeline': 'config/pipeline.json', **{name: 'config/' + name + '.json' for name in
              ('structural', 'claims', 'summaries', 'retrieval', 'taxonomy')},
              'corpus': 'isolated-test', 'retrieval_cases': 'cases.json', 'retrieval_acceptance': None,
              'sample_per_document': 12, 'minimum_retrieval_hit_rate': .875, 'test_only': True}
    w.write(tmp_path / 'config/workflow.json', config)
    return w.Workflow('fixture-run', root=tmp_path)


@pytest.fixture
def commands(monkeypatch):
    calls = []
    def execute(command, directory, timeout, **kwargs):
        stage = directory.parent.name
        calls.append((stage, command))
        directory.mkdir(parents=True, exist_ok=True)
        if stage in w.REVIEWS:
            review_path = directory / 'review.json'
            if not review_path.exists():
                w.write(review_path, {'build_id': 'fixture', 'reviews': [{'chunk_id': 'x', 'status': 'pending', 'notes': 'fixture only'}]})
            review = w.read(review_path)
            statuses = {r['status'] for r in review['reviews']}
            status = 'fail' if 'fail' in statuses else 'pending_review' if 'pending' in statuses else 'pass'
            w.write(directory / 'report.json', {'automatic_gate': 'pass', 'review_gate': status, 'gate': status})
            return 0  # Deliberately 0 even when pending/failed: gate fields must win.
        if stage in {'parse', 'ingest'}:
            w.write(directory / ('build/report.json' if stage == 'parse' else 'report.json'),
                    {'outcomes': [{'status': 'parsed' if stage == 'parse' else 'ingested'}]})
        elif stage == 'parsing_evaluation':
            w.write(directory / 'report.json', {'gate': {'status': 'pass'}})
        elif stage in {'structure', 'summaries', 'generate', 'classify'}:
            w.write(directory / 'build/report.json', {'status': 'complete', 'errors': [], 'telemetry': {'estimated_cost_usd': 0}})
        elif stage == 'graph':
            (directory / 'graph.html').write_text('fixture graph')
        elif stage == 'index':
            w.write(directory / 'stdout.log', {'snapshot_id': 'fixture-snapshot',
                    'vector_coverage': {'passage': {'vectors': 2, 'records': 2}}})
        elif stage == 'retrieval':
            pairs = [(mode, method) for mode in ('passage', 'claim', 'summary') for method in ('keyword', 'vector', 'combined')]
            w.write(directory / 'report.json', {'rows': [{'id': 'fixture-only', 'hit': True, 'mode': mode, 'method': method} for mode, method in pairs],
                    'metrics': [{'mode': mode, 'method': method, 'hit_rate': 1, 'completed': 1, 'cases': 1} for mode, method in pairs]})
        return 0
    monkeypatch.setattr(w, 'execute', execute)
    return calls, execute


def fixture_judgment(self, stage, out):
    review = w.read(out / 'review.json')
    for row in review['reviews']:
        row.update(status='pass', notes='Fixture judgment; never real approval.')
    w.write(out / 'review.json', review)


def test_end_to_end_order_reuse_and_publication_is_explicit(controller, commands, monkeypatch):
    monkeypatch.setattr(w.Workflow, 'independent_review', fixture_judgment)
    controller.run(limits())
    calls, _ = commands
    assert set(stage for stage, _ in calls) == set(w.STAGES) - {'publish'}
    assert [s for s, _ in calls].index('summaries') < [s for s, _ in calls].index('claim_review')
    parsing = next(cmd for stage, cmd in calls if stage == 'parse')
    assert parsing[parsing.index('--run-id') + 1] == controller.state['run_id']
    retrieval = next(cmd for stage, cmd in calls if stage == 'retrieval')
    assert '--unpublished' in retrieval and '--snapshot-id' in retrieval
    assert controller.state['status'] == 'awaiting_explicit_publication'
    report = w.read(controller.directory / 'report.json')
    assert report['recorded_checks_complete'] and not report['published']
    assert report['stage_counts']['complete'] == len(w.STAGES) - 1
    assert report['test_only']
    count = len(calls)
    resumed = w.Workflow('fixture-run', root=controller.root)
    resumed.run(limits())
    assert len(calls) == count
    with pytest.raises(ValueError, match='fixture/test-only'):
        resumed.run(limits(), publish=True)
    assert not any(stage == 'publish' for stage, _ in calls)


@pytest.mark.parametrize('review_stage', ['parsing_review', 'chunk_review'])
def test_pending_exit_zero_is_inspected_and_can_resume(controller, commands, monkeypatch, review_stage):
    inspections = []
    def inaccessible(self, stage, out):
        if stage != review_stage:
            return fixture_judgment(self, stage, out)
        inspections.append(stage)  # no source inspection means no pass judgment
    monkeypatch.setattr(w.Workflow, 'independent_review', inaccessible)
    with pytest.raises(ValueError, match=review_stage + ' gate pending'):
        controller.run(limits())
    assert inspections == [review_stage]
    assert 'structure' not in controller.state['stages']
    pending_dir = controller.output(review_stage)
    assert w.read(pending_dir / 'repair-task.json')['status'] == 'pending'
    report = w.read(controller.directory / 'report.json')
    assert report['status'] == 'blocked' and report['last_error']
    assert next(s for s in report['stages'] if s['stage'] == review_stage)['status'] == 'pending'
    monkeypatch.setattr(w.Workflow, 'independent_review', fixture_judgment)
    controller.run(limits(), stop_after=review_stage)
    assert controller.output(review_stage) == pending_dir
    assert controller.state['stages'][review_stage]['executions'] == 2


@pytest.mark.parametrize('review_stage,builder', [('parsing_review', 'parse'), ('chunk_review', 'chunk')])
def test_failed_review_preserves_findings_requires_rebuild_and_fresh_review(controller, commands, monkeypatch, review_stage, builder):
    def defect(self, stage, out):
        if stage != review_stage:
            return fixture_judgment(self, stage, out)
        review = w.read(out / 'review.json')
        review['reviews'][0].update(status='fail', severity='critical', notes='Fixture defect at source location 1')
        w.write(out / 'review.json', review)
    monkeypatch.setattr(w.Workflow, 'independent_review', defect)
    with pytest.raises(ValueError, match='gate failed'):
        controller.run(limits())
    old = controller.output(review_stage)
    with pytest.raises(ValueError, match='resolve defect'):
        controller.run(limits())
    with pytest.raises(ValueError, match='requires rebuilding'):
        controller.repair(review_stage, 'Attempting to reset a failed review')
    controller.repair(builder, 'Corrected fixture chunk builder issue; rebuild and review')
    monkeypatch.setattr(w.Workflow, 'independent_review', fixture_judgment)
    controller.run(limits(), stop_after=review_stage)
    assert old != controller.output(review_stage)
    assert w.read(old / 'review.json')['reviews'][0]['status'] == 'fail'
    assert len([e for e in controller.state['history'] if e.get('stage') == review_stage]) == 1
    report = w.read(controller.directory / 'report.json')
    attempts = next(s for s in report['stages'] if s['stage'] == review_stage)['attempts']
    assert [e['status'] for e in attempts] == ['failed', 'complete']
    assert not attempts[0]['current'] and attempts[1]['current']


def test_stale_inputs_and_artifacts_are_rejected(controller, commands, monkeypatch):
    controller.run(limits(), stop_after='ingest')
    (controller.output('ingest') / 'report.json').write_text('{}')
    with pytest.raises(ValueError, match='saved artifacts changed'):
        controller.run(limits())
    controller.repair('ingest', 'Corrected damaged fixture report; regenerate downstream outputs')
    controller.run(limits(), stop_after='ingest')
    (controller.root / 'cases.json').write_text('[]')
    assert any('inputs/configuration' in issue for issue in controller.stale())
    with pytest.raises(ValueError, match='require --repair-from ingest'):
        controller.repair('parse', 'Updated fixture case identity deliberately')


def test_interruption_resume_preserves_attempt_and_restarts_frozen_builder(controller, commands, monkeypatch):
    _, original_execute = commands
    interrupted = False
    def execute(command, out, timeout, **kwargs):
        nonlocal interrupted
        if out.parent.name == 'parse' and not interrupted:
            interrupted = True
            (out / 'partial.txt').write_text('retain me')
            raise KeyboardInterrupt()
        return original_execute(command, out, timeout, **kwargs)
    monkeypatch.setattr(w, 'execute', execute)
    with pytest.raises(KeyboardInterrupt):
        controller.run(limits())
    old = controller.output('parse')
    assert controller.state['stages']['parse']['status'] == 'interrupted'
    report = w.read(controller.directory / 'report.json')
    assert next(s for s in report['stages'] if s['stage'] == 'parse')['attempts'][0]['execution_seconds'] >= 0
    resumed = w.Workflow('fixture-run', root=controller.root)
    resumed.run(limits(), stop_after='parse')
    assert resumed.output('parse') != old and (old / 'partial.txt').read_text() == 'retain me'
    assert resumed.state['stages']['ingest']['executions'] == 1


def test_lock_is_exclusive_and_recovers_after_release(controller):
    with w.run_lock(controller.directory):
        with pytest.raises(ValueError, match='holds this run lock'):
            with w.run_lock(controller.directory):
                pass
    with w.run_lock(controller.directory):
        pass


def test_report_command_regenerates_summary_without_running_or_mutating_state(controller, commands, monkeypatch, capsys):
    controller.run(limits(), stop_after='ingest')
    state_before = controller.path.read_bytes()
    artifacts_before = w.fingerprints(controller.output('ingest'))
    report_path = controller.directory / 'report.json'
    report_path.unlink()  # Also supports older runs that have no overall report.
    calls_before = len(commands[0])
    original = w.Workflow
    monkeypatch.setattr(w, 'Workflow', lambda run_id, config: original(run_id, config, root=controller.root))
    assert w.main(['report', '--run-id', 'fixture-run', '--config', str(controller.config_path)]) == 0
    assert json.loads(capsys.readouterr().out)['report_path'] == str(report_path)
    assert w.read(report_path)['cost']['estimated_total_usd'] == 0
    assert controller.path.read_bytes() == state_before and len(commands[0]) == calls_before
    assert w.fingerprints(controller.output('ingest')) == artifacts_before
    with w.run_lock(controller.directory):
        assert w.main(['report', '--run-id', 'fixture-run', '--config', str(controller.config_path)]) == 1
    assert 'holds this run lock' in capsys.readouterr().err


def test_budgets_and_missing_sources_block_before_paid_work(controller, commands, monkeypatch):
    monkeypatch.setattr(w.Workflow, 'independent_review', fixture_judgment)
    with pytest.raises(ValueError, match='paid work disabled'):
        controller.run(limits(allow_paid=False))
    assert 'structure' not in controller.state['stages']
    with pytest.raises(ValueError, match='paid-stage budget exhausted'):
        controller.run(limits(max_paid_stages=0))
    controller.state['elapsed_seconds'] = 4000
    with pytest.raises(ValueError, match='wall-time budget exhausted'):
        controller.run(limits())
    manifest = w.read(controller.manifest_path)
    manifest['sources'][0]['sha256'] = '0' * 64
    w.write(controller.manifest_path, manifest)
    controller.repair('ingest', 'Fixture manifest changed; verify source-integrity guard')
    with pytest.raises(ValueError, match='hash_mismatch'):
        controller.run(limits())


def test_gate_fields_and_nonzero_exit_take_precedence(controller, tmp_path):
    w.write(tmp_path / 'report.json', {'automatic_gate': 'pass', 'review_gate': 'pending', 'gate': 'pending_review'})
    assert controller.validate_stage('chunk_review', tmp_path, 0) == 'pending'
    assert controller.validate_stage('chunk_review', tmp_path, 2) == 'failed'
    w.write(tmp_path / 'report.json', {'automatic_gate': 'pass', 'review_gate': 'fail', 'gate': 'fail'})
    assert controller.validate_stage('chunk_review', tmp_path, 0) == 'failed'
    w.write(tmp_path / 'report.json', {'automatic_gate': 'fail', 'review_gate': 'pass', 'gate': 'pass'})
    assert controller.validate_stage('chunk_review', tmp_path, 0) == 'failed'


def test_model_resolution_has_no_fallback_and_layers_project_settings(tmp_path, monkeypatch):
    home = tmp_path / 'codex-home'
    home.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setattr(w.Path, 'exists', lambda path: False)  # isolate real system/ancestor configuration
    with pytest.raises(ValueError, match='explicitly configured'):
        w.resolve_reviewer(tmp_path)
    monkeypatch.undo()
    monkeypatch.setenv('CODEX_HOME', str(home))
    (home / 'config.toml').write_text('model="configured-test-model"\nmodel_reasoning_effort="high"\n')
    project = tmp_path / 'project'
    (project / '.codex').mkdir(parents=True)
    (project / '.codex/config.toml').write_text('model_reasoning_effort="medium"\n')
    resolved = w.resolve_reviewer(project)
    assert resolved['settings']['model'] == 'configured-test-model'
    assert resolved['settings']['model_reasoning_effort'] == 'medium'
    resolved = w.resolve_reviewer(project, {'model': 'workflow-test-model', 'model_reasoning_effort': 'low'})
    assert resolved['settings'] == {'model': 'workflow-test-model', 'model_reasoning_effort': 'low'}
    (project / '.codex/config.toml').write_text('profile="unsupported"\n')
    with pytest.raises(ValueError, match='unsupported profile'):
        w.resolve_reviewer(project)


def test_explicit_workflow_models_need_no_toml_model_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(w.Path, 'exists', lambda path: False)
    configured = {stage: {'model': stage + '-fixture', 'model_reasoning_effort': 'high'} for stage in w.REVIEWS}
    resolved = w.resolve_reviewers(tmp_path, {'reviewers': configured})
    assert {stage: value['settings'] for stage, value in resolved.items()} == configured
    del configured['claim_review']['model']
    with pytest.raises(ValueError, match='explicitly configured'):
        w.resolve_reviewers(tmp_path, {'reviewers': configured})


@pytest.mark.parametrize('configured', [None, [], {'parsing': {}}, {'parsing_review': None},
    {'chunk_review': {'reasoning_effort': 'high'}}, {'claim_review': {'model': ''}},
    {'claim_review': {'model': False}}])
def test_invalid_workflow_reviewer_settings_are_rejected(tmp_path, configured):
    with pytest.raises(ValueError, match='reviewer'):
        w.resolve_reviewers(tmp_path, {'reviewers': configured})


def test_branch_invalidation_leaves_independent_summaries():
    assert 'summaries' not in w.descendants('generate')
    assert {'classify', 'claim_review', 'graph', 'index', 'retrieval', 'publish'} <= w.descendants('generate')
    assert set(w.STAGES) == w.descendants('ingest')


def prepare_independent(controller, monkeypatch, result_overrides=None, inspected=True, stage='chunk_review'):
    prompt_dir = controller.root / 'src/knowledge_foundry/prompts'
    prompt_dir.mkdir(parents=True)
    (prompt_dir / {'parsing_review': 'agent_prompt_parsing_review', 'chunk_review': 'agent_prompt_chunking_review', 'claim_review': 'agent_prompt_claim_review'}[stage]).write_text('Fixture prompt: read-only source inspection')
    out = controller.directory / stage / 'attempt-1'
    out.mkdir(parents=True)
    review = {'build_id': 'fixture-build', 'review_policy_sha256': 'policy',
              'reviewer': {'kind': None, 'name': None, 'model': None},
              'reviews': [{'chunk_id': 'x', 'status': 'pending', 'notes': 'inspection pending'}]}
    if stage != 'chunk_review':
        review['reviews'][0]['sample_id'] = review['reviews'][0].pop('chunk_id')
    w.write(out / 'review.json', review)
    w.write(out / 'report.json', {'documents': []})
    controller.state['identity'] = w.input_identity(controller.root, controller.config_path)
    controller.limits, controller.started = limits(), w.time.monotonic()
    monkeypatch.setattr(w, 'resolve_reviewer', lambda root, overrides=None: {'settings': {'model': 'configured-test-model', 'model_reasoning_effort': 'high'} | (overrides or {}), 'configuration_sha256': {}})
    commands = []
    def agent(command, directory, timeout, stdin=None, **kwargs):
        commands.append(command)
        judged = copy.deepcopy(review)
        judged['reviewer'] = {'kind': 'agent', 'name': 'Fixture session', 'model': command[command.index('--model') + 1]}
        judged['reviews'][0].update(status='pass', notes='Inspected fixture original; not pilot approval')
        judged.update(result_overrides or {})
        w.write(directory / 'response.json', {'review_json': json.dumps(judged),
                'inspected_sample_ids': ['x'] if inspected else [], 'requested_pages': []})
        (directory / 'stdout.log').write_text(json.dumps({'type': 'thread.started', 'thread_id': 'isolated-fixture-thread'}) + '\n' +
                  json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 100, 'output_tokens': 10}}) + '\n')
        return 0
    monkeypatch.setattr(w, 'execute', agent)
    return out, commands


@pytest.mark.parametrize('stage', ['parsing_review', 'chunk_review', 'claim_review'])
def test_independent_session_pins_config_records_usage_and_rejects_reused_thread(controller, monkeypatch, stage):
    out, commands = prepare_independent(controller, monkeypatch, stage=stage)
    controller.independent_review(stage, out)
    command = commands[0]
    assert command[command.index('--model') + 1] == 'configured-test-model'
    assert command[command.index('--sandbox') + 1] == 'read-only'
    assert '--ephemeral' in command
    assert 'model_reasoning_effort="high"' in command
    review = w.read(out / 'review.json')
    measurement = w.read(review['workflow_session']['measurement'])
    assert measurement['usage'][0]['input_tokens'] == 100 and measurement['cost_usd'] is None
    with pytest.raises(ValueError, match='fresh identifiable session'):
        controller.independent_review(stage, out)


@pytest.mark.parametrize('stage', ['parsing_review', 'chunk_review', 'claim_review'])
def test_stage_model_overrides_are_pinned_recorded_and_changes_rejected(controller, monkeypatch, stage):
    settings = {'model': stage + '-fixture-model', 'model_reasoning_effort': 'medium',
                'model_provider': 'fixture-provider', 'service_tier': 'default'}
    controller.config['reviewers'] = {stage: settings}
    w.write(controller.config_path, controller.config)
    out, commands = prepare_independent(controller, monkeypatch, stage=stage)
    controller.independent_review(stage, out)
    command = commands[0]
    assert command[command.index('--model') + 1] == settings['model']
    for key in ('model_reasoning_effort', 'model_provider', 'service_tier'):
        assert key + '=' + json.dumps(settings[key]) in command
    assert controller.state['reviewer'][stage]['settings'] == settings
    assert all(value['settings']['model'] == 'configured-test-model'
               for name, value in controller.state['reviewer'].items() if name != stage)
    review = w.read(out / 'review.json')
    assert w.read(review['workflow_session']['measurement'])['resolved']['settings'] == settings
    assert w.read(controller.directory / 'report.json')['reviewer'][stage]['settings'] == settings
    settings['model'] = 'changed-fixture-model'
    assert any('reviewer settings changed' in issue for issue in controller.stale())
    with pytest.raises(ValueError, match='reviewer configuration changed'):
        controller.independent_review(stage, out)
    assert len(commands) == 1


@pytest.mark.parametrize('overrides,inspected,error', [
    ({}, False, 'uninspected judgment'),
    ({'build_id': 'stale'}, True, 'stale or changed review identity'),
    ({'reviewer': {'kind': 'agent', 'model': 'substitute'}}, True, 'actual reviewer attribution'),
    ({'reviews': []}, True, 'sample selection'),
])
def test_reviewer_cannot_substitute_model_build_samples_or_uninspected_pass(controller, monkeypatch, overrides, inspected, error):
    out, _ = prepare_independent(controller, monkeypatch, overrides, inspected)
    before = (out / 'review.json').read_bytes()
    with pytest.raises(ValueError, match=error):
        controller.independent_review('chunk_review', out)
    assert (out / 'review.json').read_bytes() == before
    assert (out / 'session-1/before.json').is_file()


def test_review_session_budget_stops_before_dispatch(controller, monkeypatch):
    out, commands = prepare_independent(controller, monkeypatch)
    controller.limits.max_review_sessions = 0
    with pytest.raises(ValueError, match='review-session budget exhausted'):
        controller.independent_review('chunk_review', out)
    assert not commands


def test_publication_rejects_development_and_stale_acceptance(controller):
    controller.config['test_only'] = False
    with pytest.raises(ValueError, match='development retrieval cases'):
        controller.publication_ready()
    controller.config['retrieval_acceptance'] = 'acceptance.json'
    w.write(controller.root / 'acceptance.json', {'status': 'reviewed', 'reviewer': 'fixture',
            'manifest_sha256': 'stale', 'cases_sha256': 'stale'})
    with pytest.raises(ValueError, match='stale source-checked'):
        controller.publication_ready()


def test_partial_and_missing_vectors_do_not_pass(controller, tmp_path):
    w.write(tmp_path / 'build/report.json', {'status': 'partial', 'errors': []})
    assert controller.validate_stage('generate', tmp_path, 0) == 'failed'
    w.write(tmp_path / 'stdout.log', {'snapshot_id': 's', 'vector_coverage': {'claim': {'vectors': 1, 'records': 2}}})
    assert controller.validate_stage('index', tmp_path, 0) == 'failed'
    w.write(tmp_path / 'report.json', {'rows': [{'hit': True}], 'metrics': []})
    assert controller.validate_stage('retrieval', tmp_path, 0) == 'failed'


def test_generation_and_classification_resume_existing_checkpoints(controller):
    for stage in ('structure', 'generate'):
        out = controller.directory / stage / 'attempt-1'
        (out / 'build').mkdir(parents=True)
        controller.state['stages'][stage] = {'directory': str(out), 'status': 'complete'}
    generated = controller.directory / 'generate/attempt-2'
    classified = controller.directory / 'classify/attempt-1'
    (generated / 'build').mkdir(parents=True)
    (classified / 'build').mkdir(parents=True)
    assert '--resume' in controller.command('generate', generated)
    assert '--resume' in controller.command('classify', classified)


def test_finalized_interrupted_exports_are_hash_checked_before_reuse(controller, tmp_path):
    w.write(tmp_path / 'build/report.json', {'status': 'complete', 'errors': []})
    w.write(tmp_path / 'build/build-manifest.json', {'outputs': [{'path': 'report.json', 'sha256': w.digest(tmp_path / 'build/report.json')}]})
    assert controller.finalized_checkpoint('generate', tmp_path)
    (tmp_path / 'build/report.json').write_text('{}')
    with pytest.raises(ValueError, match='integrity mismatch'):
        controller.finalized_checkpoint('generate', tmp_path)


def test_combined_target_is_enforced_while_cross_language_lexical_baseline_is_diagnostic(controller, commands, monkeypatch):
    monkeypatch.setattr(w.Workflow, 'independent_review', fixture_judgment)
    controller.run(limits())
    out = controller.output('retrieval')
    report = w.read(out / 'report.json')
    for metric in report['metrics']:
        if metric['method'] == 'keyword':
            metric['hit_rate'] = 0
    w.write(out / 'report.json', report)
    assert controller.validate_stage('retrieval', out, 0) == 'pass'
    next(m for m in report['metrics'] if m['method'] == 'combined')['hit_rate'] = .5
    w.write(out / 'report.json', report)
    assert controller.validate_stage('retrieval', out, 0) == 'failed'
    report['metrics'].pop()
    w.write(out / 'report.json', report)
    assert controller.validate_stage('retrieval', out, 0) == 'failed'


def test_existing_findings_cannot_be_discarded_by_a_new_reviewer(controller, monkeypatch):
    out, _ = prepare_independent(controller, monkeypatch)
    original = w.read(out / 'review.json')
    original['reviews'][0].update(status='fail', severity='major', notes='Prior source-located defect must remain')
    w.write(out / 'review.json', original)
    with pytest.raises(ValueError, match='earlier finding discarded'):
        controller.independent_review('chunk_review', out)
    assert w.read(out / 'review.json') == original


def test_repeated_failed_attempts_stop_at_retry_budget(controller, commands, monkeypatch):
    def fail(self, stage, out):
        if stage != 'chunk_review':
            return fixture_judgment(self, stage, out)
        review = w.read(out / 'review.json')
        review['reviews'][0].update(status='fail', severity='critical', notes='Fixture unresolved defect')
        w.write(out / 'review.json', review)
    monkeypatch.setattr(w.Workflow, 'independent_review', fail)
    for number in range(3):
        with pytest.raises(ValueError, match='gate failed'):
            controller.run(limits())
        controller.repair('chunk', 'Fixture correction attempt documented; rebuild required')
    with pytest.raises(ValueError, match='retry budget exhausted'):
        controller.run(limits())
    assert len([e for e in controller.state['history'] if e.get('stage') == 'chunk_review']) == 3


def test_publication_requires_parsing_review_even_when_other_reviews_have_sessions(controller, commands, monkeypatch):
    monkeypatch.setattr(w.Workflow, 'independent_review', fixture_judgment)
    controller.run(limits())
    controller.config.update(test_only=False, retrieval_acceptance='acceptance.json')
    w.write(controller.root / 'acceptance.json', {'status': 'reviewed', 'reviewer': 'Fixture only',
            'manifest_sha256': w.digest(controller.manifest_path),
            'cases_sha256': w.digest(controller.root / 'cases.json')})
    controller.state['review_thread_ids'] = ['fixture-thread']
    for stage in ('chunk_review', 'claim_review'):
        path = controller.output(stage) / 'review.json'
        review = w.read(path)
        review['workflow_session'] = {'thread_id': 'fixture-thread', 'measurement': 'fixture-only'}
        w.write(path, review)
    controller.state['stages'].pop('parsing_review')
    with pytest.raises(ValueError, match='independent reviews are incomplete'):
        controller.publication_ready()


def continuing_reviews(controller, monkeypatch, stage='parsing_review'):
    """Mock fresh agents; synthetic inspection never approves a real corpus."""
    out, _ = prepare_independent(controller, monkeypatch, stage=stage)
    key = 'chunk_id' if stage == 'chunk_review' else 'sample_id'
    review = w.read(out / 'review.json')
    review['snapshot_sha256'] = 'fixture-snapshot'
    review['reviews'].append({key: 'y', 'status': 'pending', 'notes': 'Fixture page awaiting inspection'})
    w.write(out / 'review.json', review)
    prompts = []

    def dispatch(inspected, mutate=None):
        def execute(command, directory, timeout, stdin=None, **kwargs):
            prompts.append(stdin)
            updated = w.read(out / 'review.json')
            updated['reviewer'] = {'kind': 'agent', 'name': 'Fixture reviewer ' + directory.name,
                                  'model': 'configured-test-model'}
            for row in updated['reviews']:
                if row[key] in inspected:
                    row.update(status='pass', notes=row['notes'] + '; inspected fixture original')
            if mutate:
                mutate(updated)
            w.write(directory / 'response.json', {'review_json': json.dumps(updated),
                    'inspected_sample_ids': inspected, 'requested_pages': []})
            (directory / 'stdout.log').write_text(json.dumps({'type': 'thread.started',
                'thread_id': 'fixture-' + directory.name}) + '\n' + json.dumps({
                    'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5}}) + '\n')
            return 0
        monkeypatch.setattr(w, 'execute', execute)
        controller.independent_review(stage, out)
    return out, dispatch, prompts


@pytest.mark.parametrize('stage', ['parsing_review', 'chunk_review', 'claim_review'])
def test_review_continuation_carries_verified_exact_judgments_with_original_attribution(controller, monkeypatch, stage):
    out, dispatch, prompts = continuing_reviews(controller, monkeypatch, stage)
    dispatch(['x'])
    first = w.read(out / 'review.json')['reviews'][0]
    dispatch(['y'])
    result = w.read(out / 'review.json')
    assert result['reviews'][0] == first
    session = result['workflow_session']
    assert session['inspected_sample_ids'] == ['y']
    assert session['carried_inspections']['x']['thread_id'] == 'fixture-session-1'
    assert session['carried_inspections']['x']['measurement'] == str(out / 'session-1/measurement.json')
    assert 'verified_prior_inspections' in prompts[1]
    assert all(row['status'] == 'pass' for row in result['reviews'])
    # A third continuation keeps both original inspection sessions, not just the latest one.
    dispatch([])
    carried = w.read(out / 'review.json')['workflow_session']['carried_inspections']
    assert {sid: record['thread_id'] for sid, record in carried.items()} == {
        'x': 'fixture-session-1', 'y': 'fixture-session-2'}


@pytest.mark.parametrize('invalid', ['missing_history', 'failed_session', 'different_settings',
                                   'stale_identity', 'unregistered_thread', 'missing_response',
                                   'uninspected_response', 'edited_judgment', 'edited_archive'])
def test_continuation_rejects_unverified_carryover(controller, monkeypatch, invalid):
    out, dispatch, _ = continuing_reviews(controller, monkeypatch)
    dispatch(['x'])
    path = out / 'session-1/after.json'
    if invalid == 'missing_history':
        path.unlink()
    elif invalid in {'failed_session', 'different_settings', 'unregistered_thread'}:
        if invalid == 'unregistered_thread':
            controller.state['review_thread_ids'] = []
        else:
            measurement = w.read(out / 'session-1/measurement.json')
            if invalid == 'failed_session':
                measurement['returncode'] = 1
            else:
                measurement['resolved']['settings']['model'] = 'substitute'
            w.write(out / 'session-1/measurement.json', measurement)
    elif invalid == 'stale_identity':
        archived = w.read(path)
        archived['snapshot_sha256'] = 'stale'
        w.write(path, archived)
    elif invalid in {'missing_response', 'uninspected_response'}:
        response = out / 'session-1/response.json'
        if invalid == 'missing_response':
            response.unlink()
        else:
            record = w.read(response)
            record['inspected_sample_ids'] = []
            w.write(response, record)
    else:
        review = w.read(out / 'review.json')
        review['reviews'][0]['notes'] += '; changed without inspection'
        w.write(out / 'review.json', review)
        if invalid == 'edited_archive':
            archived = w.read(path)
            archived['reviews'][0] = review['reviews'][0]
            w.write(path, archived)
    original = (out / 'review.json').read_bytes()
    with pytest.raises(ValueError, match='uninspected judgment rejected: x'):
        dispatch(['y'])
    assert (out / 'review.json').read_bytes() == original


def test_revised_prior_judgment_requires_fresh_inspection_and_preserves_findings(controller, monkeypatch):
    out, dispatch, _ = continuing_reviews(controller, monkeypatch)
    dispatch(['x'])
    def revision(review):
        review['reviews'][0]['notes'] += '; revision needs fresh inspection'
    with pytest.raises(ValueError, match='uninspected judgment rejected: x'):
        dispatch(['y'], revision)
    dispatch(['x', 'y'], revision)
    assert w.read(out / 'review.json')['workflow_session']['inspected_sample_ids'] == ['x', 'y']


def test_continuation_preserves_failed_findings_and_honors_remaining_session_budget(controller, monkeypatch):
    out, dispatch, _ = continuing_reviews(controller, monkeypatch)
    def defect(review):
        review['reviews'][0].update(status='fail', severity='critical',
                                   notes='Fixture source-confirmed defect; must remain')
    dispatch(['x'], defect)
    failed = w.read(out / 'review.json')['reviews'][0]
    dispatch(['y'])
    assert w.read(out / 'review.json')['reviews'][0] == failed
    assert controller.state['review_sessions_started'] == 2
    controller.limits.max_review_sessions = 2
    with pytest.raises(ValueError, match='review-session budget exhausted'):
        dispatch([])
    assert not (out / 'session-3').exists()


def test_parsing_review_locations_outside_config_are_explicitly_hash_bound(controller):
    selection = controller.root / 'tests/fixtures/review-pages.json'
    w.write(selection, [{'source_id': 'src:fixture', 'page': 1}])
    config = w.read(controller.config_path)
    config['parsing_review_required_pages'] = 'tests/fixtures/review-pages.json'
    w.write(controller.config_path, config)
    controller = w.Workflow('fixture-run', root=controller.root)
    assert str(selection) in controller.state['identity']
    assert not controller.stale()
    controller.state['stages']['parse'] = {'directory': str(controller.directory / 'parse/attempt-1'),
                                          'status': 'ready'}
    command = controller.review_command('parsing_review', controller.directory / 'parsing_review/attempt-1')
    assert command[command.index('--required-pages') + 1] == str(selection)
    w.write(selection, [{'source_id': 'src:fixture', 'page': 2}])
    assert any('inputs/configuration/code changed' in issue for issue in controller.stale())
    with pytest.raises(ValueError, match='conservatively require --repair-from ingest'):
        controller.repair('parsing_review', 'Changing selection requires affected outputs to invalidate')


def test_viewer_assets_remain_hash_bound_outside_scripts(controller):
    asset = controller.root / 'apps/kpg_viewer/core.js'
    asset.parent.mkdir(parents=True)
    asset.write_text('// fixture viewer asset')
    controller = w.Workflow('fixture-run', root=controller.root)
    assert str(asset) in controller.state['identity']
    asset.write_text('// changed fixture asset')
    assert any('inputs/configuration/code changed' in issue for issue in controller.stale())
