"""Resumable controller for the existing source-to-snapshot commands.

No model judgments live here. Independent read-only Codex sessions inspect sources;
the existing evaluators validate and score their saved judgments.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[2]
STAGES = ('ingest', 'parse', 'parsing_evaluation', 'parsing_review', 'chunk', 'chunk_review', 'accept', 'structure',
          'summaries', 'generate', 'classify', 'claim_review', 'graph', 'index', 'retrieval', 'publish')
DEPS = {'ingest': (), 'parse': ('ingest',), 'parsing_evaluation': ('parse',),
        'parsing_review': ('parsing_evaluation',), 'chunk': ('parsing_review',), 'chunk_review': ('chunk',), 'accept': ('parsing_review', 'chunk_review'), 'structure': ('accept',),
        'summaries': ('structure',), 'generate': ('structure',), 'classify': ('generate',),
        'claim_review': ('classify',), 'graph': ('claim_review', 'summaries'), 'index': ('graph',),
        'retrieval': ('index',), 'publish': ('retrieval',)}
PAID = {'structure', 'summaries', 'generate', 'classify', 'index', 'retrieval'}
REVIEWS = {'parsing_review', 'chunk_review', 'claim_review'}
REVIEW_PROMPTS = {
    'parsing_review': 'agent_prompt_document_structuring_review',
    'chunk_review': 'agent_prompt_document_structuring_review',
    'claim_review': 'agent_prompt_claim_review',
}
RESUMABLE = {'generate', 'classify'}
BUNDLE_STAGES = {'parse', 'chunk', 'structure', 'summaries', 'generate', 'classify'}
STAGE_PATHS = {'ingest': 'ingestion', 'parse': 'document-structuring/parsing', 'parsing_evaluation': 'document-structuring/parsing/automatic-review',
    'parsing_review': 'document-structuring/parsing-review', 'chunk': 'document-structuring/chunks', 'chunk_review': 'document-structuring/chunk-review', 'accept': 'document-structuring/acceptance',
    'structure': 'semantic-clustering', 'summaries': 'summary-generation',
    'generate': 'claim-generation', 'classify': 'claim-classification', 'claim_review': 'claim-review',
    'graph': 'provenance-graph', 'index': 'indexing', 'retrieval': 'retrieval-evaluation', 'publish': 'publication'}
STAGE_NAMES = {path: stage for stage, path in STAGE_PATHS.items()}
STAGE_NAMES['parsing-audit'] = 'parsing_evaluation'
ARCHIVE_GROUPS = {'parsing_evaluation': ('parse', 'automatic-review'), 'parsing_review': ('parse', 'review'),
                  'chunk_review': ('chunk', 'review'), 'claim_review': ('classify', 'review')}


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprints(directory):
    return {str(p.relative_to(directory)): digest(p) for p in sorted(Path(directory).rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


@contextmanager
def run_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another controller holds this run lock') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def resolve_reviewer(root=ROOT, overrides=None):
    """Resolve TOML defaults and workflow overrides for each fresh CLI session.

    Do not infer a model from generation configs or invent a fallback. Profiles
    require explicit resolution outside this controller; unrecognized layering
    is rejected rather than guessed.
    """
    keys = ('model', 'model_reasoning_effort', 'model_provider', 'service_tier')
    if overrides is None:
        overrides = {}
    if (not isinstance(overrides, dict) or set(overrides) - set(keys)
            or any(not isinstance(value, str) or not value.strip() for value in overrides.values())):
        raise ValueError('reviewer settings must use supported keys with nonempty string values')
    home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
    paths = [Path('/etc/codex/config.toml'), home / 'config.toml']
    paths += [p / '.codex/config.toml' for p in [*reversed(root.parents), root]]
    settings, identities = {}, {}
    for path in dict.fromkeys(paths):
        if not path.exists():
            continue
        config = tomllib.loads(path.read_text())
        if config.get('profile') or config.get('model_catalog_json'):
            raise ValueError('reviewer configuration requires unsupported profile/catalog resolution')
        identities[str(path)] = digest(path)
        settings.update({k: config[k] for k in keys if k in config})
    settings.update(overrides)
    if not settings.get('model') or not settings.get('model_reasoning_effort'):
        raise ValueError('reviewer model and reasoning effort must be explicitly configured; no fallback')
    return {'settings': settings, 'configuration_sha256': identities,
            'resolution': 'explicit system/user/ancestor project TOML, then workflow stage settings; pinned CLI settings; no profile override'}


def resolve_reviewers(root, config):
    overrides = config.get('reviewers', {})
    if not isinstance(overrides, dict) or set(overrides) - REVIEWS:
        raise ValueError('reviewers must map parsing_review, chunk_review or claim_review to settings')
    if any(not isinstance(settings, dict) for settings in overrides.values()):
        raise ValueError('reviewer stage settings must be objects')
    return {stage: resolve_reviewer(root, overrides.get(stage)) for stage in sorted(REVIEWS)}


@contextmanager
def reviewer_environment(run_id):
    """Give fresh read-only reviewers writable, disposable CLI runtime storage.

    Keep the configured user settings and authentication, but never write runtime
    locks/databases or credential refreshes into the controlling user's home.
    Credential copies are private and removed even when the review fails.
    """
    source = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
    with tempfile.TemporaryDirectory(prefix=f'kf-{run_id}-reviewer-') as directory:
        runtime = Path(directory)
        for name in ('config.toml', 'auth.json', 'AGENTS.md'):
            original = source / name
            if original.is_file():
                target = runtime / name
                shutil.copyfile(original, target)
                target.chmod(0o600)
        yield {**os.environ, 'CODEX_HOME': str(runtime)}


def input_identity(root, config_path):
    workflow_config = read(config_path)
    files = [config_path, root / 'requirements.txt', root / workflow_config['retrieval_cases']]
    if workflow_config.get('parsing_review_required_pages'):
        files.append(root / workflow_config['parsing_review_required_pages'])
    for directory in ('config', 'src', 'scripts', 'schemas', 'apps/kpg_viewer'):
        files += [p for p in (root / directory).rglob('*') if p.is_file()
                  and '__pycache__' not in p.parts and p.suffix != '.pyc']
    pipeline_path = root / read(config_path)['pipeline']
    pipeline = read(pipeline_path)
    source_root = (pipeline_path.parent / pipeline['root']).resolve()
    manifest = source_root / pipeline['manifest_file']
    files.append(manifest)
    entries = {e['manifest_id']: e for e in read(manifest)['sources']}
    files += [source_root / entries[mid]['local_path'] for mid in pipeline['manifest_ids']]
    return {str(p.resolve()): digest(p) for p in sorted(set(files))}


def gate_status(report, review=False):
    if review:
        return 'pass' if all(report.get(k) == 'pass' for k in ('automatic_gate', 'review_gate', 'gate')) else (
            'failed' if report.get('automatic_gate') == 'fail' or report.get('review_gate') == 'fail'
            or report.get('gate') == 'fail' else 'pending')
    return 'pass' if report.get('gate', {}).get('status') == 'pass' else 'failed'


def descendants(stage):
    affected = {stage}
    for candidate in STAGES:
        if affected.intersection(DEPS[candidate]):
            affected.add(candidate)
    return affected


def execute(command, directory, timeout, *, stdin=None, env=None):
    """Keep exact argv, stdout/stderr, including on timeout or interruption."""
    write(directory / 'command.json', {'argv': list(map(str, command)), 'started_at': now()})
    with (directory / 'stdout.log').open('w') as out, (directory / 'stderr.log').open('w') as err:
        process = subprocess.Popen(list(map(str, command)), cwd=ROOT, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                                   stdout=out, stderr=err, text=True, start_new_session=True, env=env)
        try:
            process.communicate(stdin, timeout=timeout)
        except BaseException:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
    return process.returncode


class Workflow:
    def __init__(self, run_id, config_path=None, root=ROOT, mode=None):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', run_id):
            raise ValueError('run ID must be a safe directory name')
        self.root = root.resolve()
        self.config_path = (config_path or root / 'config/workflow.json').resolve()
        self.config = read(self.config_path)
        self.pipeline_path = self.root / self.config['pipeline']
        pipeline = read(self.pipeline_path)
        self.source_root = (self.pipeline_path.parent / pipeline['root']).resolve()
        self.manifest_path = self.source_root / pipeline['manifest_file']
        self.directory = self.root / 'artifacts' / run_id
        if self.directory.is_symlink() or not self.directory.resolve().is_relative_to(self.root):
            raise ValueError('run directory must stay inside the repository and cannot be a symlink')
        self.path = self.directory / 'state.json'
        self.state = read(self.path) if self.path.exists() else {
            'schema_version': 1, 'run_id': run_id, 'created_at': now(), 'status': 'new',
            'layout_version': 4, 'execution_mode': mode or 'automated',
            'workflow_config': read(self.config_path), 'pipeline_config': pipeline,
            'review_policies': read(self.root / 'config/review-policy.json') if (self.root / 'config/review-policy.json').exists() else {},
            'claim_settings': read(self.root / self.config['claims']) if self.config.get('claims') else {},
            'identity': input_identity(root, self.config_path), 'stages': {}, 'history': [],
            'paid_stages_started': 0, 'review_sessions_started': 0, 'elapsed_seconds': 0}
        if self.path.exists() and self.state.get('layout_version') != 4:
            raise ValueError('obsolete workflow layout; start a fresh run with reusable ingested artifacts')
        self.mode = self.state.get('execution_mode', 'automated')
        if mode and mode != self.mode:
            raise ValueError('run execution mode differs; use a distinct manual/automated run ID')
        self.native_input_dir = None

    def stage_path(self, stage):
        return self.directory / STAGE_PATHS[stage]

    def bundle_directory(self, stage, directory=None):
        directory = Path(directory) if directory is not None else self.output(stage)
        return directory

    def restore_execution_logs(self, stage):
        entry = self.state['stages'][stage]
        if not entry.get('pending_logs'):
            return
        logs, out = Path(entry['pending_logs']), Path(entry['directory'])
        if not logs.resolve().is_relative_to((self.directory / '.execution').resolve()):
            raise ValueError('execution log directory escapes this run')
        if logs.exists():
            if out.is_symlink() or not out.resolve().is_relative_to(self.directory.resolve()):
                raise ValueError('stage output escapes this run')
            target = out / 'execution'
            target.mkdir(parents=True, exist_ok=True)
            for name in ('command.json', 'stdout.log', 'stderr.log'):
                if (logs / name).exists():
                    (logs / name).replace(target / name)
            logs.rmdir()
            if not any(logs.parent.iterdir()):
                logs.parent.rmdir()
        entry.pop('pending_logs')

    def execute_stage(self, stage, command, out):
        if stage not in BUNDLE_STAGES:
            return execute(command, out, self.remaining())
        # Builders require an absent output directory. Persist logs separately
        # until they finish, including failures; hard-kill recovery uses this pointer.
        logs_root = self.directory / '.execution'
        if logs_root.is_symlink() or not logs_root.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError('execution logs must stay inside this run')
        logs_root.mkdir(parents=True, exist_ok=True)
        logs = Path(tempfile.mkdtemp(prefix=stage + '-', dir=logs_root))
        self.state['stages'][stage]['pending_logs'] = str(logs)
        self.save()
        try:
            return execute(command, logs, self.remaining())
        finally:
            self.restore_execution_logs(stage)

    def stage_fingerprints(self, stage, directory):
        saved = fingerprints(directory)
        # The audit has its own checkpoint; adding it must not mutate parsing's identity.
        if stage == 'chunk':
            saved = {key: value for key, value in saved.items() if key not in {
                'acceptance.json', 'parsing-review.json', 'chunking-review.json'}}
        if self.mode == 'manual' and stage == 'parse':
            saved = {key: value for key, value in saved.items() if not key.startswith('automatic-review/')}
        return saved

    def save(self):
        self.state['updated_at'] = now()
        write(self.path, self.state)
        self.write_report()

    def write_report(self):
        from knowledge_foundry.workflow_reporting import build_report
        elapsed = self.state['elapsed_seconds']
        if getattr(self, '_run_active', False):
            elapsed += time.monotonic() - self.started
        path = self.directory / 'report.json'
        write(path, build_report(self.state, STAGES, self.path, self.config_path, elapsed))
        return path

    def output(self, stage):
        return Path(self.state['stages'][stage]['directory'])

    def changed_stages(self):
        previous, current = self.state['identity'], input_identity(self.root, self.config_path)
        changed = {path for path in previous.keys() | current.keys() if previous.get(path) != current.get(path)}
        stages = set()
        pipeline = str(self.pipeline_path.resolve())
        for path in changed:
            name = Path(path).name
            if path == pipeline:
                before = self.state.get('pipeline_config', {})
                after = read(self.pipeline_path)
                for key in before.keys() | after.keys():
                    if before.get(key) != after.get(key):
                        stages.add('parse' if key in {'parsing', 'structuring'} else
                                   'chunk' if key == 'chunking' else 'chunk_review' if key == 'evaluation' else 'ingest')
            elif name == 'review-policy.json':
                before = self.state.get('review_policies', {}).get('policies', {})
                after = read(path).get('policies', {})
                stages.update({'parsing': 'parsing_review', 'chunking': 'chunk_review',
                               'claim_generation_and_classification': 'claim_review'}[key]
                              for key in before.keys() | after.keys() if before.get(key) != after.get(key))
            elif path == str(self.config_path):
                before, after = self.state.get('workflow_config', {}), read(self.config_path)
                for key in before.keys() | after.keys():
                    if before.get(key) == after.get(key):
                        continue
                    if key == 'reviewers':
                        stages.update(k for k in REVIEWS if before.get(key, {}).get(k) != after.get(key, {}).get(k))
                    else:
                        stages.add({'summaries': 'summaries', 'claims': 'structure', 'structural': 'structure',
                                    'taxonomy': 'classify', 'retrieval': 'index', 'retrieval_cases': 'retrieval',
                                    'retrieval_acceptance': 'retrieval', 'sample_per_document': 'parsing_review',
                                    'parsing_review_required_pages': 'parsing_review'}.get(key, 'ingest'))
            elif name == 'claim-generation-and-classification.json':
                before = self.state.get('claim_settings', {})
                after = read(path)
                if {**before.get('steps', {}), 'topic_classification': None} == {**after.get('steps', {}), 'topic_classification': None} and before.get('structural_build_config') == after.get('structural_build_config'):
                    stages.add('classify')
                else:
                    stages.add('structure')
            elif '/data/' in path or name in {'ingestion.py', 'corpus.py'}:
                stages.add('ingest')
            elif name in {'parsing.py', 'parse.py', 'parsers.py', 'structure_providers.py', 'provider_exports.py',
                          'pdf_source.py', 'pdf_tables.py', 'pdf_structure.py'}:
                stages.add('parse')
            elif name in {'chunking.py', 'chunk.py', 'document_structure.py', 'structural_chunks.py', 'structural_links.py'}:
                stages.add('chunk')
            elif name == 'agent_prompt_document_structuring_review':
                stages.update({'parsing_review', 'chunk_review'})
            elif name == 'agent_prompt_claim_review':
                stages.add('claim_review')
            elif name in {'parsing_audit.py', 'document_structuring_review.py', 'deterministic_document_structuring_review.py'}:
                stages.add('parsing_evaluation')
            elif 'parsing_review' in name or 'parsing-review' in name or name.startswith('parsing-expanded') or path == str((self.root / self.config.get('parsing_review_required_pages', '__absent__')).resolve()):
                stages.add('parsing_review')
            elif 'chunking_review' in name or name == 'chunking_audit.py':
                stages.add('chunk_review')
            elif name in {'document_structuring.py', 'accept_structure.py'}:
                stages.add('accept')
            elif 'summar' in name:
                stages.add('summaries')
            elif name == 'topics-v1.json':
                stages.add('classify')
            elif path == str((self.root / self.config['retrieval_cases']).resolve()):
                stages.add('retrieval')
            elif '/retrieval/' in path or '/storage/' in path or name.startswith('retrieval'):
                stages.add('index')
            elif 'claim' in name or 'compilation' in name or name in {'generation_context.py', 'openai_tools.py'}:
                stages.add('structure')
            elif name in {'embeddings.py', 'semantic_clusters.py', 'semantic-clustering.json', 'build_semantic_clusters.py'}:
                stages.add('structure')
            elif '/apps/' in path or name == 'render_knowledge_graph.py':
                stages.add('graph')
            else:
                # Controller/dependency/contract changes restart structuring,
                # preserving verified source bytes unless acquisition changed.
                stages.add('parse')
        if self.state.get('reviewer'):
            resolved = resolve_reviewers(self.root, self.config)
            stages.update(k for k in REVIEWS if resolved[k] != self.state['reviewer'].get(k))
        return stages

    def stale(self):
        issues = []
        if input_identity(self.root, self.config_path) != self.state['identity']:
            issues.append('inputs/configuration/code changed: repair from ' + ', '.join(sorted(self.changed_stages())) + ' with a repair note')
        if self.state.get('reviewer'):
            if resolve_reviewers(self.root, self.config) != self.state['reviewer']:
                issues.append('reviewer settings changed: repair the affected review stage')
        for name, entry in self.state['stages'].items():
            if entry['status'] == 'complete' and self.stage_fingerprints(name, Path(entry['directory'])) != entry['fingerprints']:
                issues.append('saved artifacts changed: ' + name)
        return issues

    def repair(self, stage, note):
        if not note or len(note.strip()) < 12:
            raise ValueError('repair requires a concrete defect/correction note')
        required = self.changed_stages()
        if not required <= descendants(stage):
            raise ValueError('repair must include changed stages: ' + ', '.join(sorted(required)))
        if stage in REVIEWS and stage in self.state['stages']:
            entry = self.state['stages'][stage]
            report_path, review_path = Path(entry['directory']) / 'report.json', Path(entry['directory']) / 'review.json'
            report = read(report_path) if report_path.exists() else {}
            review = read(review_path) if review_path.exists() else {}
            if (entry.get('status') == 'failed' or gate_status(report, review=True) == 'failed'
                    or any(row.get('status') == 'fail' for row in review.get('reviews', []))
                    or review.get('additional_findings')):
                raise ValueError('failed review requires rebuilding affected outputs, not just resetting review')
        affected = descendants(stage)
        entries = dict(self.state['stages'])
        if self.mode == 'manual':
            # External review evaluation may have saved findings before stale
            # identity prevented registration. Archive those bytes as evidence,
            # without inventing a registered review or resetting its budgets.
            for name, (group, _) in ARCHIVE_GROUPS.items():
                directory = self.directory / STAGE_PATHS[name]
                if (name in affected and name not in entries and group in entries and
                        (directory.exists() or directory.is_symlink())):
                    entries[name] = {'status': 'unregistered', 'directory': str(directory),
                                     'started_at': entries[group]['started_at']}
        for name in affected & self.state['stages'].keys():
            self.restore_execution_logs(name)
        for name in affected & entries.keys():
            directory = Path(entries[name]['directory'])
            if directory.is_symlink() or not directory.resolve().is_relative_to(self.directory.resolve()):
                raise ValueError('stage directory escapes this run; refusing to move evidence')
        # Archive children first, including the audit nested inside parsing.
        for name in reversed(STAGES):
            if name not in entries:
                continue
            if name in affected:
                entry = entries[name]
                if self.mode == 'manual':
                    original = Path(entry['directory'])
                    group, part = ARCHIVE_GROUPS.get(name, (name, 'outputs'))
                    started = entries.get(group, entry)['started_at']
                    stamp = datetime.fromisoformat(started).astimezone(timezone.utc).strftime('%Y-%m-%dT%H-%M-%S.%fZ')
                    archived = self.directory / 'history' / STAGE_PATHS[group] / stamp / part
                    if not archived.resolve().is_relative_to(self.directory.resolve()):
                        raise ValueError('history directory escapes this run; refusing to move evidence')
                    if original.exists():
                        if archived.exists():
                            raise ValueError('archive destination already exists; refusing to overwrite history')
                        archived.parent.mkdir(parents=True, exist_ok=True)
                        original.rename(archived)
                    entry = {**entry, 'directory': str(archived), 'original_directory': str(original)}
                    if entry.get('bundle_directory'):
                        entry['bundle_directory'] = str(archived / Path(entry['bundle_directory']).relative_to(original))
                    write(archived.parent / (part + '-archive.json'), {'stage': name, 'original_directory': str(original),
                          'archived_directory': str(archived), 'started_at': entry['started_at'], 'reason': note})
                self.state['history'].append({'stage': name, **entry, 'invalidated_at': now(), 'repair_note': note})
                self.state['stages'].pop(name, None)
        self.config = read(self.config_path)
        self.pipeline_path = self.root / self.config['pipeline']
        pipeline = read(self.pipeline_path)
        self.source_root = (self.pipeline_path.parent / pipeline['root']).resolve()
        self.manifest_path = self.source_root / pipeline['manifest_file']
        self.state['identity'] = input_identity(self.root, self.config_path)
        self.state['workflow_config'] = read(self.config_path)
        self.state['pipeline_config'] = read(self.pipeline_path)
        self.state['review_policies'] = read(self.root / 'config/review-policy.json') if (self.root / 'config/review-policy.json').exists() else {}
        self.state['claim_settings'] = read(self.root / self.config['claims']) if self.config.get('claims') else {}
        if stage in {'ingest', 'parse', 'parsing_review', 'chunk', 'chunk_review', 'claim_review'} and self.state.get('reviewer'):
            self.state['history'].append({'reviewer': self.state.pop('reviewer'), 'repair_note': note})
        self.state['status'] = 'repaired'
        self.save()

    def prerequisite_sources(self):
        from knowledge_foundry.corpus import readiness
        inventory = None
        if self.state['stages'].get('ingest', {}).get('status') == 'complete':
            from knowledge_foundry.ingestion import load_ingested
            inventory = load_ingested(self.output('ingest'))
        result = readiness(self.pipeline_path, inventory=inventory)
        self.state['corpus_readiness'] = result
        if not result['ready']:
            raise ValueError('corpus not ready: ' + '; '.join(result['problems']))

    def command(self, name, out):
        c = self.config
        path = lambda key: str(self.root / c[key])
        dep = lambda stage: str(self.bundle_directory(stage))
        bundle = self.bundle_directory(name, out)
        args = {}
        if name == 'ingest':
            args[name] = ['ingest.py', '--config', path('pipeline'), '--output-dir', str(out)]
        if name == 'parse':
            args[name] = ['parse.py', '--config', path('pipeline'), '--output-dir', str(bundle),
                          '--run-id', self.state['run_id'], '--ingested-dir', str(self.output('ingest'))]
            if self.native_input_dir:
                args[name] += ['--native-input-dir', str(self.native_input_dir)]
        if name == 'parsing_evaluation':
            args[name] = ['deterministic_document_structuring_review.py', 'prepare',
                          '--checkpoint', 'parsing', '--audit-only', '--parsed-dir', dep('parse'),
                          '--source-root', str(self.source_root), '--output', str(out / 'report.json')]
        if name == 'chunk':
            args[name] = ['chunk.py', '--config', path('pipeline'), '--input-dir', dep('parse'), '--output-dir', str(bundle)]
        if name == 'accept':
            args[name] = ['accept_structure.py', '--input-dir', dep('chunk'),
                          '--parsing-review', str(self.output('parsing_review') / 'report.json'),
                          '--chunk-review', str(self.output('chunk_review') / 'report.json')]
        if name == 'structure':
            args[name] = ['build_semantic_clusters.py', '--chunked-dir', dep('chunk'),
                          '--config', path('structural'),
                          '--output-dir', str(bundle), '--cache-dir', str(self.root / 'artifacts/embeddings-and-token-counts')]
        if name in {'generate', 'summaries'}:
            args[name] = [('generate_claims.py' if name == 'generate' else 'generate_summaries.py'),
                          '--context-dir', dep('structure'), '--config', path('claims' if name == 'generate' else 'summaries'),
                          '--output-dir', str(bundle)]
            if name == 'summaries':
                args[name] += ['--cache-dir', str(self.root / 'artifacts/summary-cache')]
            elif (bundle / 'generation-checkpoint.json').exists() and not (bundle / 'build-manifest.json').exists():
                args[name] += ['--resume']
            elif name == 'generate':
                prior = [e for e in self.state['history'] if e.get('stage') == 'generate' and
                         e.get('input_identity_sha256') == hashlib.sha256(json.dumps(self.state['identity'], sort_keys=True).encode()).hexdigest()]
                if prior:
                    previous = Path(prior[-1].get('bundle_directory', self.bundle_directory('generate', prior[-1]['directory'])))
                    if (previous / 'report.json').exists() and read(previous / 'report.json').get('status') == 'partial':
                        args[name] += ['--retry-failed-from', str(previous)]
        if name == 'classify':
            args[name] = ['classify_claims.py', '--candidates-dir', dep('generate'), '--config', path('claims'),
                          '--taxonomy', path('taxonomy'), '--output-dir', str(bundle)]
            if (bundle / 'classification-checkpoint.json').exists() and not (bundle / 'build-manifest.json').exists():
                args[name] += ['--resume']
        if name == 'graph':
            args[name] = ['render_knowledge_graph.py', '--bundle', dep('classify') + '/artifact-bundle.json',
                          '--claim-generation-dir', dep('generate'), '--summary-dir', dep('summaries'),
                          '--output', str(out / 'graph.html')]
        if name == 'index':
            args[name] = ['index_artifacts.py', 'import', '--corpus', c['corpus'], '--config', path('retrieval'),
                          '--preparation-dir', dep('structure'), '--claims-dir', dep('classify'),
                          '--summaries-dir', dep('summaries'), '--embed-missing']
        if name == 'retrieval':
            snapshot = read(self.output('index') / 'stdout.log')['snapshot_id']
            args[name] = ['evaluate_retrieval.py', '--corpus', c['corpus'], '--config', path('retrieval'),
                          '--snapshot-id', snapshot, '--unpublished', '--cases', path('retrieval_cases'), '--modes', 'passage', 'claim', 'summary',
                          '--no-rerank', '--check-excerpts', '--output', str(out / 'report.json')]
        if name == 'publish':
            snapshot = read(self.output('index') / 'stdout.log')['snapshot_id']
            args[name] = ['index_artifacts.py', 'publish', '--corpus', c['corpus'], '--snapshot-id', snapshot]
        return [sys.executable, str(self.root / 'scripts' / args[name][0]), *args[name][1:]]

    def review_command(self, name, out):
        args = ['--manifest', str(self.manifest_path), '--review-policy', str(self.root / 'config/review-policy.json'),
                '--sample-per-document', str(self.config['sample_per_document']), '--output', str(out / 'report.json')]
        if name == 'parsing_review':
            args += ['--checkpoint', 'parsing', '--parsed-dir', str(self.bundle_directory('parse')),
                     '--parsing-review-dir', str(out), '--source-root', str(self.source_root)]
            if self.config.get('parsing_review_required_pages'):
                args += ['--required-pages', str(self.root / self.config['parsing_review_required_pages'])]
        elif name == 'chunk_review':
            args += ['--checkpoint', 'chunking', '--chunked-dir', str(self.bundle_directory('chunk')),
                     '--parsed-dir', str(self.bundle_directory('parse')), '--chunk-review-dir', str(out)]
        else:
            args += ['--candidates-dir', str(self.bundle_directory('generate')), '--classified-dir', str(self.bundle_directory('classify')),
                     '--source-root', str(self.source_root), '--taxonomy', str(self.root / self.config['taxonomy'])]
            script = 'deterministic_claim_review.py'
        if name in {'parsing_review', 'chunk_review'}:
            script = 'deterministic_document_structuring_review.py'
            args = ['finalize' if (out / 'review.json').exists() else 'prepare', *args]
        else:
            args = ['finalize' if (out / 'review.json').exists() else 'prepare', *args]
            args += ['--review-file' if (out / 'review.json').exists() else '--initialize-review', str(out / 'review.json')]
        return [sys.executable, str(self.root / 'scripts' / script), *args]

    def render_review_pages(self, out):
        sources = {e['source_id']: self.source_root / e['local_path'] for e in read(self.manifest_path)['sources']}
        pages = set()
        def walk(value, source=None):
            if isinstance(value, dict):
                source = sources.get(value.get('source_id'), source)
                if value.get('source_file'):
                    source = self.source_root / value['source_file']
                loc = value.get('location', {})
                if source and loc.get('format') == 'pdf' and type(loc.get('page')) is int:
                    pages.add((source, loc['page']))
                for item in value.values():
                    walk(item, source)
            elif isinstance(value, list):
                for item in value:
                    walk(item, source)
        walk(read(out / 'report.json'))
        if (out / 'extra-pages.json').exists():
            for request in read(out / 'extra-pages.json'):
                source = sources[request['source_id']]
                if source.suffix.lower() != '.pdf' or type(request['page']) is not int or request['page'] < 1:
                    raise ValueError('invalid additional PDF page request')
                pages.add((source, request['page']))
        rendered = out / 'pages'
        rendered.mkdir(exist_ok=True)
        index = []
        for source, page in sorted(pages):
            target = rendered / (digest(source)[:16] + '-p' + str(page))
            if not target.with_suffix('.png').exists():
                subprocess.run(['pdftoppm', '-f', str(page), '-l', str(page), '-singlefile', '-scale-to', '1800',
                                '-png', str(source), str(target)], check=True, capture_output=True,
                               timeout=self.remaining())
            index.append({'source': str(source), 'page': page, 'image': str(target.with_suffix('.png'))})
        write(out / 'page-index.json', index)

    def prior_review_inspections(self, out, original, key, resolved):
        """Recover exact judgments inspected by successful earlier sessions.

        A supplied pass/fail or an unchanged row alone is not inspection evidence.
        Retain attribution to the session that actually inspected that sample.
        """
        current = {row[key]: row for row in original['reviews']}
        verified = {}
        for path in sorted(out.glob('session-*/after.json')):
            try:
                saved = read(path)
                session = saved['workflow_session']
                measurement_path = path.parent / 'measurement.json'
                measurement = read(measurement_path)
                response = read(path.parent / 'response.json')
                proposed = {row[key]: row for row in json.loads(response['review_json'])['reviews']}
                if (any(saved.get(field) != original.get(field) for field in
                        ('build_id', 'snapshot_sha256', 'review_policy_sha256')) or
                        session['thread_id'] not in self.state.get('review_thread_ids', []) or
                        Path(session['measurement']).resolve() != measurement_path.resolve() or
                        measurement.get('returncode') != 0 or measurement.get('resolved') != resolved or
                        measurement.get('thread_ids') != [session['thread_id']] or not measurement.get('usage')):
                    continue
                for row in saved['reviews']:
                    sid = row[key]
                    if (row['status'] in {'pass', 'fail'} and current.get(sid) == row and proposed.get(sid) == row and
                            sid in response['inspected_sample_ids']):
                        verified[sid] = {'thread_id': session['thread_id'], 'measurement': str(measurement_path)}
            except (OSError, ValueError, KeyError, TypeError):
                continue  # Missing/unusable history requires inspection again.
        return verified

    def independent_review(self, name, out):
        if self.state['review_sessions_started'] >= self.limits.max_review_sessions:
            raise ValueError('review-session budget exhausted; resolve missing evidence/non-convergence')
        configuration = resolve_reviewers(self.root, self.config)
        prior = self.state.get('reviewer')
        if prior and prior != configuration:
            raise ValueError('reviewer configuration changed; repair the affected review checkpoint')
        self.state['reviewer'] = configuration
        resolved = configuration[name]
        self.render_review_pages(out)
        number = self.state['review_sessions_started'] + 1
        session = out / f'session-{number}'
        session.mkdir()
        original = read(out / 'review.json')
        write(session / 'before.json', original)
        id_field = 'chunk_id' if name == 'chunk_review' else 'sample_id'
        prior_inspections = self.prior_review_inspections(out, original, id_field, resolved)
        prompt_file = self.root / 'src/knowledge_foundry/prompts' / REVIEW_PROMPTS[name]
        prompt = prompt_file.read_text() + '\n\nController binding (overrides example paths, continuation scope and write mechanism):\n' + json.dumps({
            'run_id': self.state['run_id'], 'review_checkpoint': name,
            'RUN_ID': self.state['run_id'], 'MODEL': resolved['settings']['model'],
            'MODEL_REASONING_EFFORT': resolved['settings']['model_reasoning_effort'],
            'stage_paths': {k: str(self.bundle_directory(k)) if k in BUNDLE_STAGES else v['directory'] for k, v in self.state['stages'].items()},
            'evaluation_report': str(out / 'report.json'), 'review_file': str(out / 'review.json'),
            'manifest': str(self.manifest_path), 'source_root': str(self.source_root),
            'policy': str(self.root / 'config/review-policy.json'), 'taxonomy': str(self.root / self.config['taxonomy']),
            'sample_per_document': self.config['sample_per_document'], 'pdf_page_index': str(out / 'page-index.json'),
            'model_settings': resolved['settings'], 'launch_mode': self.mode + '_workflow',
            'verified_prior_inspections': prior_inspections}, indent=2) + (
            '\nYou are a fresh independent source reviewer in a read-only session. Initialization is already complete. '
            'Inspect every to_review sample without a verified prior judgment, and any revised judgments, against originals; '
            'use view_image on rendered PDF pages. Prioritization determines inspection order, not review scope. '
            'Exact unchanged judgments listed in verified_prior_inspections may carry forward without reinspection. '
            'Other assessed judgments require source inspection in this session. Never claim earlier inspections as your own. '
            'Leave a sample pending only when a concrete blocker prevents inspection; record the specific blocker and '
            'attempted inspection in its notes, and continue inspecting all other samples. Request inaccessible additional '
            'source pages with exact page numbers. Return only after every sample has a completed judgment or a documented '
            'inspection blocker. Do not execute evaluation commands '
            'that write files: the controller runs final evaluation. Return the full updated review as review_json, '
            'and requested_pages for any inaccessible additional PDF pages, and inspected_sample_ids only for samples actually inspected against saved originals. Preserve every '
            'existing judgment/finding and its rationale; add evidence and notes on any revision. Set reviewer.kind=agent '
            'and reviewer.model and reviewer.model_reasoning_effort to model_settings.model and '
            'model_settings.model_reasoning_effort, and reviewer.launch_mode to the controller launch_mode. '
            'These override manual prompt variables. Do not claim another model or effort. Never use fixtures as approval.')
        (session / 'prompt.txt').write_text(prompt)
        schema = {'type': 'object', 'properties': {'review_json': {'type': 'string'},
                  'inspected_sample_ids': {'type': 'array', 'items': {'type': 'string'}},
                  'requested_pages': {'type': 'array', 'items': {'type': 'object', 'properties': {'source_id': {'type': 'string'}, 'page': {'type': 'integer'}}, 'required': ['source_id', 'page'], 'additionalProperties': False}}},
                  'required': ['review_json', 'inspected_sample_ids', 'requested_pages'], 'additionalProperties': False}
        write(session / 'response-schema.json', schema)
        settings = resolved['settings']
        command = ['codex', 'exec', '--ephemeral', '--json', '--sandbox', 'read-only', '--cd', str(self.root),
                   '--model', settings['model'], '-c', 'model_reasoning_effort=' + json.dumps(settings['model_reasoning_effort']),
                   '--output-schema', str(session / 'response-schema.json'), '--output-last-message', str(session / 'response.json')]
        for key in ('model_provider', 'service_tier'):
            if key in settings:
                command += ['-c', key + '=' + json.dumps(settings[key])]
        command += ['-']
        protected = input_identity(self.root, self.config_path)
        protected_outputs = {k: fingerprints(Path(v['directory'])) for k, v in self.state['stages'].items() if v['status'] == 'complete'}
        self.state.setdefault('review_measurements', []).append(str(session / 'measurement.json'))
        self.state['review_sessions_started'] = number
        self.save()
        review_started, review_clock, rc = now(), time.monotonic(), None
        try:
            with reviewer_environment(self.state['run_id']) as env:
                rc = execute(command, session, self.remaining(), stdin=prompt, env=env)
        finally:
            events = []
            if (session / 'stdout.log').exists():
                for line in (session / 'stdout.log').read_text().splitlines():
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
            threads = [e['thread_id'] for e in events if e.get('type') == 'thread.started']
            usage = [e.get('usage') for e in events if e.get('type') == 'turn.completed']
            review_finished, duration = now(), time.monotonic() - review_clock
            write(session / 'measurement.json', {'resolved': resolved, 'thread_ids': threads, 'usage': usage or None,
                  'cost_usd': None, 'started_at': review_started, 'finished_at': review_finished,
                  'duration_seconds': duration, 'returncode': rc})
        used = self.state.setdefault('review_thread_ids', [])
        if rc or len(threads) != 1 or threads[0] in used or not usage:
            raise ValueError('independent review did not complete in a fresh identifiable session; inspect session logs')
        used += threads
        if input_identity(self.root, self.config_path) != protected:
            raise ValueError('reviewer changed protected inputs')
        if any(fingerprints(self.output(k)) != hashes for k, hashes in protected_outputs.items()):
            raise ValueError('reviewer changed frozen upstream artifacts')
        result = read(session / 'response.json')
        updated = json.loads(result['review_json'])
        if result.get('requested_pages'):
            write(out / 'extra-pages.json', result['requested_pages'])
        before = {r[id_field]: r for r in original['reviews']}
        after = {r[id_field]: r for r in updated['reviews']}
        if len(after) != len(updated['reviews']) or set(before) != set(after):
            raise ValueError('reviewer changed sample selection')
        write(session / 'proposed-review.json', updated)
        for field in ('build_id', 'snapshot_sha256', 'review_policy_sha256'):
            if original.get(field) != updated.get(field):
                raise ValueError('stale or changed review identity: ' + field)
        if updated.get('reviewer', {}).get('kind') != 'agent' or updated['reviewer'].get('model') != settings['model']:
            raise ValueError('actual reviewer attribution is absent or differs from resolved model')
        attribution = {'model_reasoning_effort': settings['model_reasoning_effort'],
                       'launch_mode': self.mode + '_workflow'}
        for field, value in attribution.items():
            if updated['reviewer'].get(field) not in (None, value):
                raise ValueError('actual reviewer attribution differs from controller ' + field)
        updated['reviewer'].update(attribution)
        if not set(result['inspected_sample_ids']) <= set(before):
            raise ValueError('inspection references an unknown sample')
        for sid, row in after.items():
            carried = sid in prior_inspections and row == before[sid]
            assessed = row['status'] in {'pass', 'fail'} or before[sid]['status'] in {'pass', 'fail'}
            if assessed and not carried and sid not in result['inspected_sample_ids']:
                raise ValueError('uninspected judgment rejected: ' + sid)
            if before[sid]['status'] in {'pass', 'fail'} and before[sid] != row:
                if before[sid].get('notes', '') not in row.get('notes', ''):
                    raise ValueError('earlier finding discarded: ' + sid)
        for finding in original.get('additional_findings', []):
            if finding not in updated.get('additional_findings', []):
                raise ValueError('additional finding discarded')
        updated['workflow_session'] = {'thread_id': threads[0], 'resolved_model': settings, 'measurement': str(session / 'measurement.json'),
            'inspected_sample_ids': result['inspected_sample_ids'],
            'carried_inspections': {sid: evidence for sid, evidence in prior_inspections.items() if after[sid] == before[sid]}}
        token_totals = {}
        for record in usage:
            if isinstance(record, dict):
                for key, value in record.items():
                    if type(value) is int and value >= 0:
                        token_totals[key] = token_totals.get(key, 0) + value
        if original.get('telemetry'):
            updated['prior_review_telemetry'] = [*original.get('prior_review_telemetry', []), original['telemetry']]
        updated['telemetry'] = {'started_at': review_started, 'finished_at': review_finished,
                                'duration_seconds': duration, 'requests': None, 'usage': token_totals or None,
                                'estimated_cost_usd': None}
        write(session / 'after.json', updated)
        write(out / 'review.json', updated)
        self.save()

    def finalized_checkpoint(self, name, out):
        """Recover the small window after final export but before state was saved."""
        bundle = self.bundle_directory(name, out)
        manifest_path = bundle / 'build-manifest.json'
        if name not in RESUMABLE or not manifest_path.exists():
            return False
        manifest = read(manifest_path)
        outputs = manifest.get('outputs', [])
        if not outputs or len({item['path'] for item in outputs}) != len(outputs):
            raise ValueError('invalid finalized checkpoint output manifest')
        for item in outputs:
            path = (bundle / item['path']).resolve()
            if not path.is_relative_to(bundle.resolve()) or digest(path) != item['sha256']:
                raise ValueError('finalized checkpoint integrity mismatch')
        return self.validate_stage(name, out, 0) == 'pass'

    def record_parsing_audit(self, review_out, rc):
        """Retain the audit performed by review initialization without executing it twice."""
        report = read(review_out / 'report.json')
        audit = report.get('automatic_audit')
        if rc not in (0, 1) or not isinstance(audit, dict) or not isinstance(audit.get('gate'), dict):
            raise ValueError('parsing review did not provide a valid automatic audit')
        passed = gate_status(audit) == 'pass'
        if report.get('automatic_gate') != ('pass' if passed else 'fail'):
            raise ValueError('parsing review and embedded automatic audit gates disagree')
        prior = self.state['stages'].get('parsing_evaluation')
        if prior:
            if prior['status'] != 'complete' or read(Path(prior['directory']) / 'report.json') != audit:
                raise ValueError('automatic audit changed or previously failed; rebuild parsing and request fresh review')
            self.state['stages']['parsing_review']['upstream']['parsing_evaluation'] = {
                'directory': prior['directory'], 'fingerprints': prior['fingerprints']}
            return
        attempt = 1 + sum(e.get('stage') == 'parsing_evaluation' for e in self.state['history'])
        if attempt > self.limits.max_stage_attempts:
            raise ValueError('stage retry budget exhausted for automatic parsing audit')
        out = self.stage_path('parsing_evaluation')
        if self.mode != 'manual':
            out = out / f'attempt-{attempt}'
        if out.is_symlink() or not out.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError('automatic audit output must stay inside this run')
        if out.exists() and any(out.iterdir()):
            raise ValueError('untracked automatic audit output exists; refusing to overwrite evidence')
        write(out / 'report.json', audit)
        entry = {'status': 'complete' if passed else 'failed', 'directory': str(out), 'attempt': attempt,
                 'executions': 1, 'started_at': self.state['stages']['parsing_review']['started_at'], 'finished_at': now(),
                 'derived_from': str(review_out / 'report.json'), 'returncode': rc,
                 'input_identity_sha256': self.state['stages']['parsing_review']['input_identity_sha256'],
                 'upstream': {'parse': {'directory': self.state['stages']['parse']['directory'],
                                        'fingerprints': self.state['stages']['parse']['fingerprints']}}}
        if passed:
            entry['fingerprints'] = fingerprints(out)
        self.state['stages']['parsing_evaluation'] = entry
        self.state['stages']['parsing_review']['upstream']['parsing_evaluation'] = {
            'directory': str(out), 'fingerprints': entry.get('fingerprints')}
        self.save()

    def remaining(self):
        return max(0.001, self.limits.max_seconds - self.state['elapsed_seconds'] - (time.monotonic() - self.started))

    def validate_stage(self, name, out, rc):
        if name in REVIEWS:
            status = gate_status(read(out / 'report.json'), review=True)
            return status if rc in (0, 1) else 'failed'
        if rc:
            return 'failed'
        if name == 'parsing_evaluation':
            return gate_status(read(out / 'report.json'))
        if name in {'parse', 'ingest'}:
            report = read(self.bundle_directory(name, out) / 'report.json' if name == 'parse' else out / 'report.json')
            return 'pass' if report['outcomes'] and all(r['status'] == ('parsed' if name == 'parse' else 'ingested') for r in report['outcomes']) else 'failed'
        if name in {'structure', 'summaries', 'generate', 'classify'}:
            report = read(self.bundle_directory(name, out) / 'report.json')
            return 'pass' if report['status'] == 'complete' and not report.get('errors') else 'failed'
        if name == 'accept':
            from knowledge_foundry.document_structuring import load_validated_structure
            value = load_validated_structure(self.bundle_directory('chunk'))
            write(out / 'report.json', value['acceptance'])
            return 'pass'
        if name == 'index':
            report = read(out / 'stdout.log')
            coverage = report['vector_coverage']
            return 'pass' if report.get('snapshot_id') and coverage and all(v['vectors'] == v['records'] for v in coverage.values()) else 'failed'
        if name == 'retrieval':
            report = read(out / 'report.json')
            cases = read(self.root / self.config['retrieval_cases'])
            expected = {(mode, method) for mode in ('passage', 'claim', 'summary')
                        for method in ('keyword', 'vector', 'combined')}
            metrics = {(m['mode'], m['method']): m for m in report['metrics']}
            if not cases or len(metrics) != len(report['metrics']) or set(metrics) != expected:
                return 'failed'
            if any('error' in r for r in report['rows']):
                return 'failed'
            ids = {case['id'] for case in cases}
            if len(ids) != len(cases):
                return 'failed'
            for pair, metric in metrics.items():
                rows = [r for r in report['rows'] if (r['mode'], r['method']) == pair]
                if len(rows) != len(cases) or {r['id'] for r in rows} != ids or metric['completed'] != len(cases):
                    return 'failed'
                if metric['cases'] != len(cases):
                    return 'failed'
                if pair[1] == 'combined' and (metric['hit_rate'] is None or
                        metric['hit_rate'] < self.config['minimum_retrieval_hit_rate']):
                    return 'failed'
            return 'pass'
        if name == 'graph':
            return 'pass' if (out / 'graph.html').is_file() and (out / 'graph.html').stat().st_size else 'failed'
        return 'pass'

    def publication_ready(self):
        acceptance = self.config.get('retrieval_acceptance')
        if self.config.get('test_only'):
            raise ValueError('publication blocked: fixture/test-only workflow cannot approve a real pilot')
        for key in ('structural', 'claims', 'summaries', 'retrieval'):
            if '"fixture"' in (self.root / self.config[key]).read_text():
                raise ValueError('publication blocked: model fixtures cannot approve a real pilot')
        if not acceptance:
            raise ValueError('publication blocked: development retrieval cases are not reviewed acceptance data')
        record = read(self.root / acceptance)
        if (record.get('status') != 'reviewed' or not record.get('reviewer') or
            record.get('manifest_sha256') != digest(self.manifest_path) or
            record.get('cases_sha256') != digest(self.root / self.config['retrieval_cases'])):
            raise ValueError('publication blocked: missing/stale source-checked retrieval acceptance attestation')
        for name in REVIEWS:
            entry = self.state['stages'].get(name)
            if not entry or entry.get('status') != 'complete':
                raise ValueError('publication blocked: independent reviews are incomplete')
            review = read(Path(entry['directory']) / 'review.json')
            session = review.get('workflow_session', {})
            if session.get('thread_id') not in self.state.get('review_thread_ids', []) or not session.get('measurement'):
                raise ValueError('publication blocked: review has no independent session evidence')
        if self.stale() or any(self.state['stages'].get(n, {}).get('status') != 'complete' for n in STAGES[:-1]):
            raise ValueError('publication blocked: required checks are incomplete or stale')

    def run(self, limits, *, publish=False, stop_after=None, only_stage=None, agent_review=False):
        if only_stage and self.mode != 'manual':
            raise ValueError('single-stage execution requires a manual run')
        if self.mode == 'manual' and not only_stage and not publish:
            raise ValueError('manual runs require step --stage; automatic resume cannot execute them')
        if only_stage == 'publish':
            publish = True
        launch_review = self.mode != 'manual' or agent_review
        self.limits, self.started = limits, time.monotonic()
        self._run_active = True
        self.state.setdefault('budget_history', []).append({
            'started_at': now(), 'publish': publish,
            **{key: getattr(limits, key) for key in ('allow_paid', 'max_seconds', 'max_paid_stages',
                                                     'max_review_sessions', 'max_stage_attempts')}})
        try:
            if self.stale():
                raise ValueError('; '.join(self.stale()))
            self.prerequisite_sources()
            if publish:
                self.publication_ready()
            for name in ((only_stage,) if only_stage else ('publish',) if publish else STAGES[:-1]):
                if name == 'parsing_evaluation' and not only_stage and stop_after != name:
                    continue  # The review tool supplies this checkpoint from its embedded audit.
                entry = self.state['stages'].get(name)
                if entry and entry.get('pending_logs'):
                    self.restore_execution_logs(name)
                    self.save()
                if entry and entry['status'] == 'complete':
                    if name == stop_after:
                        break
                    continue
                if self.remaining() <= 0.01:
                    raise ValueError('wall-time budget exhausted')
                prerequisites = ('parse',) if name == 'parsing_review' else DEPS[name]
                if any(self.state['stages'].get(d, {}).get('status') != 'complete' for d in prerequisites):
                    raise ValueError('missing prerequisite for ' + name)
                if entry and entry['status'] == 'failed':
                    raise ValueError('resolve defect then resume with --repair-from and --repair-note; findings retained')
                if name in PAID or (name in REVIEWS and launch_review):
                    if not limits.allow_paid:
                        raise ValueError('paid work disabled; resume with --allow-paid and explicit budgets')
                    if name in PAID and self.state['paid_stages_started'] >= limits.max_paid_stages:
                        raise ValueError('paid-stage budget exhausted')
                if entry and entry['status'] == 'running':
                    entry['status'] = 'interrupted'  # prior controller died while holding the now-released lock
                missing_checkpoint = (name in RESUMABLE and not any(
                    (self.bundle_directory(name, entry['directory']) / filename).exists()
                    for filename in ('build-manifest.json', 'generation-checkpoint.json', 'classification-checkpoint.json'))) if entry else False
                if entry and entry['status'] == 'interrupted' and (name not in REVIEWS | RESUMABLE or missing_checkpoint):
                    self.repair(name, 'Interrupted build preserved; rebuild in a fresh output directory')
                    entry = None
                prior = [e for e in self.state['history'] if e.get('stage') == name]
                if not entry:
                    attempt = len(prior) + 1
                    if attempt > limits.max_stage_attempts:
                        raise ValueError('stage retry budget exhausted; repeated lack of progress requires escalation')
                    out = self.stage_path(name)
                    if self.mode != 'manual':
                        out = out / f'attempt-{attempt}'
                    if out.exists() and any(out.iterdir()):
                        raise ValueError('untracked output directory exists; preserve it and use a fresh run')
                    if out.is_symlink() or not out.resolve().is_relative_to(self.directory.resolve()):
                        raise ValueError('stage output must stay inside this run')
                    if name not in BUNDLE_STAGES:
                        out.mkdir(parents=True, exist_ok=True)
                    entry = {'status': 'ready', 'directory': str(out), 'attempt': attempt, 'executions': 0, 'started_at': now(),
                             'upstream': {d: {'directory': self.state['stages'][d]['directory'],
                                              'fingerprints': self.state['stages'][d]['fingerprints']} for d in prerequisites},
                             'input_identity_sha256': hashlib.sha256(json.dumps(self.state['identity'], sort_keys=True).encode()).hexdigest()}
                    if name in BUNDLE_STAGES:
                        entry['bundle_directory'] = str(self.bundle_directory(name, out))
                    self.state['stages'][name] = entry
                out = Path(entry['directory'])
                if entry['executions'] >= limits.max_stage_attempts:
                    raise ValueError('interruption/resume attempt budget exhausted')
                entry.update(status='running', executions=entry['executions'] + 1)
                self.state['status'] = 'running'
                if name in PAID:
                    self.state['paid_stages_started'] += 1
                self.save()
                execution_started = time.monotonic()
                try:
                    if name in REVIEWS:
                        rc = execute(self.review_command(name, out), out, self.remaining())
                        status = self.validate_stage(name, out, rc)
                        if name == 'parsing_review':
                            self.record_parsing_audit(out, rc)
                        if status == 'pending' and launch_review:
                            entry['status'] = 'pending'
                            self.save()
                            self.independent_review(name, out)
                            rc = execute(self.review_command(name, out), out, self.remaining())
                            status = self.validate_stage(name, out, rc)
                            if name == 'parsing_review':
                                self.record_parsing_audit(out, rc)
                    else:
                        if self.finalized_checkpoint(name, out):
                            rc = 0
                            entry['recovered_finalized_output'] = True
                        else:
                            rc = self.execute_stage(name, self.command(name, out), out)
                        status = self.validate_stage(name, out, rc)
                    entry.update(status='complete' if status == 'pass' else status, finished_at=now(), returncode=rc)
                    report_path = (self.bundle_directory(name, out) if name in BUNDLE_STAGES else out) / 'report.json'
                    if report_path.exists():
                        report = read(report_path)
                        entry['measurements'] = {k: report[k] for k in ('telemetry', 'embeddings', 'usage_records', 'duration_seconds', 'generation', 'compilation_telemetry') if k in report}
                    if status == 'pass':
                        entry['fingerprints'] = self.stage_fingerprints(name, out)
                    else:
                        task = {'stage': name, 'status': status, 'report': str(out / 'report.json'),
                            'instruction': 'Follow repair_prompt when supplied. Pending inspection requires a separate review task; '
                            'report missing or inaccessible evidence. For confirmed defects, diagnose, correct, run focused regression '
                            'tests, report results and a rebuild handoff, then stop. Workflow invalidation, corpus rebuilding and '
                            'source review are separate tasks; do not execute them or approve outputs here. Preserve originals, '
                            'frozen artifacts, findings, judgments, sample selection, policy and thresholds. Tests do not grant '
                            'real-corpus approval. Escalate ambiguity/inaccessible evidence/non-convergence.'}
                        if name in {'parse', 'parsing_evaluation', 'parsing_review', 'chunk', 'chunk_review', 'accept',
                                    'generate', 'classify', 'claim_review'}:
                            task['repair_prompt'] = str(self.root / 'src/knowledge_foundry/prompts/agent_prompt_repair_failed_review_findings')
                        write(out / 'repair-task.json', task)
                        self.save()
                        raise ValueError(name + ' gate ' + status + '; see repair-task.json')
                    self.save()
                except BaseException as exc:
                    if entry['status'] == 'running':
                        entry.update(status='interrupted' if isinstance(exc, (KeyboardInterrupt, subprocess.TimeoutExpired)) else 'failed', error=str(exc))
                    self.save()
                    raise
                finally:
                    entry['execution_seconds'] = entry.get('execution_seconds', 0) + time.monotonic() - execution_started
                    self.save()
                if name == stop_after:
                    break
            self.state['status'] = 'published' if publish else ('stopped_after_' + (only_stage or stop_after) if only_stage or stop_after else 'awaiting_explicit_publication')
        except BaseException as exc:
            self.state.update(status='blocked', last_error=str(exc))
            raise
        finally:
            self.state['elapsed_seconds'] += time.monotonic() - self.started
            self._run_active = False
            self.save()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'step', 'status', 'resume', 'publish', 'report'])
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/workflow.json')
    parser.add_argument('--allow-paid', action='store_true')
    parser.add_argument('--max-seconds', type=float, default=3600)
    parser.add_argument('--max-paid-stages', type=int, default=6)
    parser.add_argument('--max-review-sessions', type=int, default=4)
    parser.add_argument('--max-stage-attempts', type=int, default=3)
    stage_choices = sorted(set(STAGES[:-1]) | (set(STAGE_NAMES) - {'publication'}))
    parser.add_argument('--stop-after', choices=stage_choices)
    parser.add_argument('--repair-from', choices=stage_choices)
    parser.add_argument('--repair-note')
    parser.add_argument('--stage', choices=sorted(STAGE_NAMES), help='execute only this stage in a manual run')
    parser.add_argument('--rebuild', action='store_true', help='archive a manual stage and its dependents before rebuilding; requires --repair-note')
    parser.add_argument('--agent-review', action='store_true', help='launch the configured independent reviewer for this manual review stage')
    parser.add_argument('--native-input-dir', type=Path,
                        help='reuse source-bound native PDF conversion during a manual parsing repair')
    args = parser.parse_args(argv)
    args.stop_after = STAGE_NAMES.get(args.stop_after, args.stop_after)
    args.repair_from = STAGE_NAMES.get(args.repair_from, args.repair_from)
    if (args.command == 'step') != bool(args.stage) or args.rebuild and args.command != 'step':
        parser.error('step requires --stage; --stage/--rebuild are only valid for step')
    if args.agent_review and (args.command != 'step' or STAGE_NAMES[args.stage] not in REVIEWS):
        parser.error('--agent-review requires a manual review stage')
    if args.native_input_dir and (args.command != 'step' or STAGE_NAMES[args.stage] != 'parse'):
        parser.error('--native-input-dir requires a manual parsing step')
    if not math.isfinite(args.max_seconds) or min(args.max_seconds, args.max_stage_attempts) <= 0 or min(args.max_paid_stages, args.max_review_sessions) < 0:
        parser.error('budgets must be finite and nonnegative; time/stage attempts must be positive')
    try:
        def open_run():
            return Workflow(args.run_id, args.config, **({'mode': 'manual'} if args.command == 'step' else {}))
        workflow = open_run()
        if args.command == 'status':
            if not workflow.path.exists():
                raise ValueError('unknown run ID')
            # Atomic state replacement makes read-only status safe during execution.
            print(json.dumps({'state': workflow.state, 'stale': workflow.stale()}, indent=2))
            return 0
        with run_lock(workflow.directory):
            # Reload state under the lock, including after waiting to acquire it.
            workflow = open_run()
            workflow.native_input_dir = args.native_input_dir
            if args.command == 'run' and workflow.path.exists():
                raise ValueError('run already exists; use status or resume')
            if args.command not in {'run', 'step'} and not workflow.path.exists():
                raise ValueError('unknown run ID')
            if args.command == 'report':
                print(json.dumps({'report_path': str(workflow.write_report())}))
                return 0
            if args.repair_from:
                workflow.repair(args.repair_from, args.repair_note)
            if args.rebuild:
                workflow.repair(STAGE_NAMES[args.stage], args.repair_note)
            workflow.run(args, publish=args.command == 'publish' or args.stage == 'publication', stop_after=args.stop_after,
                         only_stage=STAGE_NAMES[args.stage] if args.stage else None, agent_review=args.agent_review)
            print(json.dumps({'status': workflow.state['status'], 'state_path': str(workflow.path),
                              'report_path': str(workflow.directory / 'report.json')}))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0
