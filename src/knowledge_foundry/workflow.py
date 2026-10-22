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
import signal
import subprocess
import sys
import time
import tomllib

ROOT = Path(__file__).resolve().parents[2]
STAGES = ('ingest', 'parse', 'parsing_evaluation', 'chunk', 'chunk_review', 'structure',
          'summaries', 'generate', 'classify', 'claim_review', 'graph', 'index', 'retrieval', 'publish')
DEPS = {'ingest': (), 'parse': ('ingest',), 'parsing_evaluation': ('parse',),
        'chunk': ('parsing_evaluation',), 'chunk_review': ('chunk',), 'structure': ('chunk_review',),
        'summaries': ('structure',), 'generate': ('structure',), 'classify': ('generate',),
        'claim_review': ('classify',), 'graph': ('claim_review', 'summaries'), 'index': ('graph',),
        'retrieval': ('index',), 'publish': ('retrieval',)}
PAID = {'structure', 'summaries', 'generate', 'classify', 'index', 'retrieval'}
REVIEWS = {'chunk_review', 'claim_review'}
RESUMABLE = {'generate', 'classify'}


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


def resolve_reviewer(root=ROOT):
    """Resolve explicit TOML settings; pin these in each fresh CLI session.

    Do not infer a model from generation configs or invent a fallback. Profiles
    require explicit resolution outside this controller; unrecognized layering
    is rejected rather than guessed.
    """
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
        settings.update({k: config[k] for k in ('model', 'model_reasoning_effort', 'model_provider', 'service_tier') if k in config})
    if not settings.get('model') or not settings.get('model_reasoning_effort'):
        raise ValueError('reviewer model and reasoning effort must be explicitly configured; no fallback')
    return {'settings': settings, 'configuration_sha256': identities,
            'resolution': 'explicit system/user/ancestor project TOML; pinned CLI settings; no profile override'}


def input_identity(root, config_path):
    workflow_config = read(config_path)
    files = [config_path, root / 'requirements.txt', root / workflow_config['retrieval_cases']]
    for directory in ('config', 'src', 'scripts', 'schemas'):
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
    def __init__(self, run_id, config_path=None, root=ROOT):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', run_id):
            raise ValueError('run ID must be a safe directory name')
        self.root = root.resolve()
        self.config_path = (config_path or root / 'config/workflow.json').resolve()
        self.config = read(self.config_path)
        self.pipeline_path = self.root / self.config['pipeline']
        pipeline = read(self.pipeline_path)
        self.source_root = (self.pipeline_path.parent / pipeline['root']).resolve()
        self.manifest_path = self.source_root / pipeline['manifest_file']
        self.directory = root / 'artifacts/workflow' / run_id
        self.path = self.directory / 'state.json'
        self.state = read(self.path) if self.path.exists() else {
            'schema_version': 1, 'run_id': run_id, 'created_at': now(), 'status': 'new',
            'identity': input_identity(root, self.config_path), 'stages': {}, 'history': [],
            'paid_stages_started': 0, 'review_sessions_started': 0, 'elapsed_seconds': 0}

    def save(self):
        self.state['updated_at'] = now()
        write(self.path, self.state)

    def output(self, stage):
        return Path(self.state['stages'][stage]['directory'])

    def stale(self):
        issues = []
        if input_identity(self.root, self.config_path) != self.state['identity']:
            issues.append('inputs/configuration/code changed: invalidate from ingest with a repair note')
        if self.state.get('reviewer'):
            if resolve_reviewer(self.root) != self.state['reviewer']:
                issues.append('reviewer settings changed: invalidate from ingest or use a new run')
        for name, entry in self.state['stages'].items():
            if entry['status'] == 'complete' and fingerprints(Path(entry['directory'])) != entry['fingerprints']:
                issues.append('saved artifacts changed: ' + name)
        return issues

    def repair(self, stage, note):
        if not note or len(note.strip()) < 12:
            raise ValueError('repair requires a concrete defect/correction note')
        if input_identity(self.root, self.config_path) != self.state['identity'] and stage != 'ingest':
            raise ValueError('code/config/input changes conservatively require --repair-from ingest')
        if stage in REVIEWS and self.state['stages'].get(stage, {}).get('status') == 'failed':
            raise ValueError('failed review requires rebuilding affected outputs, not just resetting review')
        affected = descendants(stage)
        for name in list(self.state['stages']):
            if name in affected:
                self.state['history'].append({'stage': name, **self.state['stages'].pop(name),
                                              'invalidated_at': now(), 'repair_note': note})
        self.state['identity'] = input_identity(self.root, self.config_path)
        if stage == 'ingest' and self.state.get('reviewer'):
            self.state['history'].append({'reviewer': self.state.pop('reviewer'), 'repair_note': note})
        self.state['status'] = 'repaired'
        self.save()

    def prerequisite_sources(self):
        from knowledge_foundry.corpus import readiness
        result = readiness(self.pipeline_path)
        self.state['corpus_readiness'] = result
        if not result['ready']:
            raise ValueError('corpus not ready: ' + '; '.join(result['problems']))

    def command(self, name, out):
        c = self.config
        path = lambda key: str(self.root / c[key])
        dep = lambda stage: str(self.output(stage))
        args = {}
        if name == 'ingest':
            args[name] = ['ingest.py', '--config', path('pipeline'), '--output', str(out / 'report.json')]
        if name == 'parse':
            args[name] = ['parse.py', '--config', path('pipeline'), '--output-dir', str(out / 'build')]
        if name == 'parsing_evaluation':
            args[name] = ['evaluate_parsing.py', '--input-dir', dep('parse') + '/build',
                          '--source-root', str(self.source_root), '--output', str(out / 'report.json')]
        if name == 'chunk':
            args[name] = ['chunk.py', '--config', path('pipeline'), '--input-dir', dep('parse') + '/build', '--output-dir', str(out / 'build')]
        if name == 'structure':
            args[name] = ['build_structural_chunks_and_semantic_clusters.py', '--chunked-dir', dep('chunk') + '/build',
                          '--parsed-dir', dep('parse') + '/build', '--config', path('structural'),
                          '--output-dir', str(out / 'build'), '--cache-dir', str(self.root / 'artifacts/embeddings-and-token-counts')]
        if name in {'generate', 'summaries'}:
            args[name] = [('generate_claims.py' if name == 'generate' else 'generate_summaries.py'),
                          '--context-dir', dep('structure') + '/build', '--config', path('claims' if name == 'generate' else 'summaries'),
                          '--output-dir', str(out / 'build')]
            if name == 'summaries':
                args[name] += ['--cache-dir', str(self.root / 'artifacts/summary-cache')]
            elif (out / 'build').exists() and not (out / 'build/build-manifest.json').exists():
                args[name] += ['--resume']
            elif name == 'generate':
                prior = [e for e in self.state['history'] if e.get('stage') == 'generate' and
                         e.get('input_identity_sha256') == hashlib.sha256(json.dumps(self.state['identity'], sort_keys=True).encode()).hexdigest()]
                if prior:
                    previous = Path(prior[-1]['directory']) / 'build'
                    if (previous / 'report.json').exists() and read(previous / 'report.json').get('status') == 'partial':
                        args[name] += ['--retry-failed-from', str(previous)]
        if name == 'classify':
            args[name] = ['classify_claims.py', '--candidates-dir', dep('generate') + '/build', '--config', path('claims'),
                          '--taxonomy', path('taxonomy'), '--output-dir', str(out / 'build')]
            if (out / 'build').exists() and not (out / 'build/build-manifest.json').exists():
                args[name] += ['--resume']
        if name == 'graph':
            args[name] = ['render_knowledge_graph.py', '--bundle', dep('classify') + '/build/artifact-bundle.json',
                          '--claim-generation-dir', dep('generate') + '/build', '--summary-dir', dep('summaries') + '/build',
                          '--output', str(out / 'graph.html')]
        if name == 'index':
            args[name] = ['index_artifacts.py', 'import', '--corpus', c['corpus'], '--config', path('retrieval'),
                          '--preparation-dir', dep('structure') + '/build', '--claims-dir', dep('classify') + '/build',
                          '--summaries-dir', dep('summaries') + '/build', '--embed-missing']
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
        if name == 'chunk_review':
            args += ['--chunked-dir', str(self.output('chunk') / 'build'), '--parsed-dir', str(self.output('parse') / 'build')]
            script = 'evaluate_chunking.py'
        else:
            args += ['--candidates-dir', str(self.output('generate') / 'build'), '--classified-dir', str(self.output('classify') / 'build'),
                     '--source-root', str(self.source_root), '--taxonomy', str(self.root / self.config['taxonomy'])]
            script = 'evaluate_claims.py'
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

    def independent_review(self, name, out):
        if self.state['review_sessions_started'] >= self.limits.max_review_sessions:
            raise ValueError('review-session budget exhausted; resolve missing evidence/non-convergence')
        resolved = resolve_reviewer(self.root)
        prior = self.state.get('reviewer')
        if prior and prior != resolved:
            raise ValueError('reviewer configuration changed; start a new run or invalidate from ingest')
        self.state['reviewer'] = resolved
        self.render_review_pages(out)
        number = self.state['review_sessions_started'] + 1
        session = out / f'session-{number}'
        session.mkdir()
        original = read(out / 'review.json')
        write(session / 'before.json', original)
        prompt_file = self.root / 'src/knowledge_foundry/prompts' / (
            'agent_prompt_chunking_reviewer' if name == 'chunk_review' else 'agent_prompt_claim_reviewer')
        prompt = prompt_file.read_text() + '\n\nController binding (overrides example paths and write mechanism only):\n' + json.dumps({
            'run_id': self.state['run_id'], 'stage_paths': {k: v['directory'] + '/build' for k, v in self.state['stages'].items()},
            'evaluation_report': str(out / 'report.json'), 'review_file': str(out / 'review.json'),
            'manifest': str(self.manifest_path), 'source_root': str(self.source_root),
            'policy': str(self.root / 'config/review-policy.json'), 'taxonomy': str(self.root / self.config['taxonomy']),
            'sample_per_document': self.config['sample_per_document'], 'pdf_page_index': str(out / 'page-index.json'),
            'model_settings': resolved['settings']}, indent=2) + (
            '\nYou are a fresh independent source reviewer in a read-only session. Initialization is already complete. '
            'Inspect all sampled originals and use view_image on the rendered PDF pages. Additional source pages may be '
            'requested as pending with exact page numbers if layout is inaccessible. Do not execute evaluation commands '
            'that write files: the controller runs final evaluation. Return the full updated review as review_json, '
            'and requested_pages for any inaccessible additional PDF pages, and inspected_sample_ids only for samples actually inspected against saved originals. Preserve every '
            'existing judgment/finding and its rationale; add evidence and notes on any revision. Set reviewer.kind=agent '
            'and reviewer.model to the actual configured model; do not claim another model. Never use fixtures as approval.')
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
            rc = execute(command, session, self.remaining(), stdin=prompt)
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
        key = 'chunk_id' if name == 'chunk_review' else 'sample_id'
        before = {r[key]: r for r in original['reviews']}
        after = {r[key]: r for r in updated['reviews']}
        if len(after) != len(updated['reviews']) or set(before) != set(after):
            raise ValueError('reviewer changed sample selection')
        write(session / 'proposed-review.json', updated)
        for field in ('build_id', 'snapshot_sha256', 'review_policy_sha256'):
            if original.get(field) != updated.get(field):
                raise ValueError('stale or changed review identity: ' + field)
        if updated.get('reviewer', {}).get('kind') != 'agent' or updated['reviewer'].get('model') != settings['model']:
            raise ValueError('actual reviewer attribution is absent or differs from resolved model')
        for sid, row in after.items():
            if row['status'] in {'pass', 'fail'} and sid not in result['inspected_sample_ids']:
                raise ValueError('uninspected judgment rejected: ' + sid)
            if before[sid]['status'] in {'pass', 'fail'} and before[sid] != row:
                if before[sid].get('notes', '') not in row.get('notes', ''):
                    raise ValueError('earlier finding discarded: ' + sid)
        for finding in original.get('additional_findings', []):
            if finding not in updated.get('additional_findings', []):
                raise ValueError('additional finding discarded')
        updated['workflow_session'] = {'thread_id': threads[0], 'resolved_model': settings, 'measurement': str(session / 'measurement.json')}
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
        manifest_path = out / 'build/build-manifest.json'
        if name not in RESUMABLE or not manifest_path.exists():
            return False
        manifest = read(manifest_path)
        outputs = manifest.get('outputs', [])
        if not outputs or len({item['path'] for item in outputs}) != len(outputs):
            raise ValueError('invalid finalized checkpoint output manifest')
        for item in outputs:
            path = (out / 'build' / item['path']).resolve()
            if not path.is_relative_to((out / 'build').resolve()) or digest(path) != item['sha256']:
                raise ValueError('finalized checkpoint integrity mismatch')
        return self.validate_stage(name, out, 0) == 'pass'

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
            report = read(out / 'build/report.json' if name == 'parse' else out / 'report.json')
            return 'pass' if report['outcomes'] and all(r['status'] == ('parsed' if name == 'parse' else 'ingested') for r in report['outcomes']) else 'failed'
        if name in {'structure', 'summaries', 'generate', 'classify'}:
            report = read(out / 'build/report.json')
            return 'pass' if report['status'] == 'complete' and not report.get('errors') else 'failed'
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

    def run(self, limits, *, publish=False, stop_after=None):
        self.limits, self.started = limits, time.monotonic()
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
            for name in (('publish',) if publish else STAGES[:-1]):
                entry = self.state['stages'].get(name)
                if entry and entry['status'] == 'complete':
                    if name == stop_after:
                        break
                    continue
                if self.remaining() <= 0.01:
                    raise ValueError('wall-time budget exhausted')
                if any(self.state['stages'].get(d, {}).get('status') != 'complete' for d in DEPS[name]):
                    raise ValueError('missing prerequisite for ' + name)
                if entry and entry['status'] == 'failed':
                    raise ValueError('resolve defect then resume with --repair-from and --repair-note; findings retained')
                if name in PAID | REVIEWS:
                    if not limits.allow_paid:
                        raise ValueError('paid work disabled; resume with --allow-paid and explicit budgets')
                    if name in PAID and self.state['paid_stages_started'] >= limits.max_paid_stages:
                        raise ValueError('paid-stage budget exhausted')
                if entry and entry['status'] == 'running':
                    entry['status'] = 'interrupted'  # prior controller died while holding the now-released lock
                if entry and entry['status'] == 'interrupted' and name not in REVIEWS | RESUMABLE:
                    self.repair(name, 'Interrupted build preserved; rebuild in a fresh output directory')
                    entry = None
                prior = [e for e in self.state['history'] if e.get('stage') == name]
                if not entry:
                    attempt = len(prior) + 1
                    if attempt > limits.max_stage_attempts:
                        raise ValueError('stage retry budget exhausted; repeated lack of progress requires escalation')
                    out = self.directory / name / f'attempt-{attempt}'
                    out.mkdir(parents=True)
                    entry = {'status': 'ready', 'directory': str(out), 'attempt': attempt, 'executions': 0, 'started_at': now(),
                             'input_identity_sha256': hashlib.sha256(json.dumps(self.state['identity'], sort_keys=True).encode()).hexdigest()}
                    self.state['stages'][name] = entry
                out = Path(entry['directory'])
                if entry['executions'] >= limits.max_stage_attempts:
                    raise ValueError('interruption/resume attempt budget exhausted')
                entry.update(status='running', executions=entry['executions'] + 1)
                self.state['status'] = 'running'
                if name in PAID:
                    self.state['paid_stages_started'] += 1
                self.save()
                try:
                    if name in REVIEWS:
                        rc = execute(self.review_command(name, out), out, self.remaining())
                        status = self.validate_stage(name, out, rc)
                        if status == 'pending':
                            entry['status'] = 'pending'
                            self.save()
                            self.independent_review(name, out)
                            rc = execute(self.review_command(name, out), out, self.remaining())
                            status = self.validate_stage(name, out, rc)
                    else:
                        if self.finalized_checkpoint(name, out):
                            rc = 0
                            entry['recovered_finalized_output'] = True
                        else:
                            rc = execute(self.command(name, out), out, self.remaining())
                        status = self.validate_stage(name, out, rc)
                    entry.update(status='complete' if status == 'pass' else status, finished_at=now(), returncode=rc)
                    report_path = out / ('build/report.json' if name in {'parse', 'structure', 'summaries', 'generate', 'classify'} else 'report.json')
                    if report_path.exists():
                        report = read(report_path)
                        entry['measurements'] = {k: report[k] for k in ('telemetry', 'embeddings', 'usage_records', 'duration_seconds', 'generation', 'compilation_telemetry') if k in report}
                    if status == 'pass':
                        entry['fingerprints'] = fingerprints(out)
                    else:
                        write(out / 'repair-task.json', {'stage': name, 'status': status, 'report': str(out / 'report.json'),
                            'instruction': 'Inspect originals and findings. Pending: complete inspection or resolve prerequisite. '
                            'Failed: correct implementation/configuration defect, preserve findings, invalidate from affected builder '
                            '(ingest for code/config changes), rebuild and request fresh independent review. Never lower gates, '
                            'alter originals or mark uninspected samples passed. Escalate ambiguity/inaccessible evidence/non-convergence.'})
                        self.save()
                        raise ValueError(name + ' gate ' + status + '; see repair-task.json')
                    self.save()
                except BaseException as exc:
                    if entry['status'] == 'running':
                        entry.update(status='interrupted' if isinstance(exc, (KeyboardInterrupt, subprocess.TimeoutExpired)) else 'failed', error=str(exc))
                    self.save()
                    raise
                if name == stop_after:
                    break
            self.state['status'] = 'published' if publish else ('stopped_after_' + stop_after if stop_after else 'awaiting_explicit_publication')
        except BaseException as exc:
            self.state.update(status='blocked', last_error=str(exc))
            raise
        finally:
            self.state['elapsed_seconds'] += time.monotonic() - self.started
            self.save()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'status', 'resume', 'publish'])
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/workflow.json')
    parser.add_argument('--allow-paid', action='store_true')
    parser.add_argument('--max-seconds', type=float, default=3600)
    parser.add_argument('--max-paid-stages', type=int, default=6)
    parser.add_argument('--max-review-sessions', type=int, default=4)
    parser.add_argument('--max-stage-attempts', type=int, default=3)
    parser.add_argument('--stop-after', choices=STAGES[:-1])
    parser.add_argument('--repair-from', choices=STAGES[:-1])
    parser.add_argument('--repair-note')
    args = parser.parse_args(argv)
    if not math.isfinite(args.max_seconds) or min(args.max_seconds, args.max_stage_attempts) <= 0 or min(args.max_paid_stages, args.max_review_sessions) < 0:
        parser.error('budgets must be finite and nonnegative; time/stage attempts must be positive')
    try:
        workflow = Workflow(args.run_id, args.config)
        if args.command == 'status':
            if not workflow.path.exists():
                raise ValueError('unknown run ID')
            # Atomic state replacement makes read-only status safe during execution.
            print(json.dumps({'state': workflow.state, 'stale': workflow.stale()}, indent=2))
            return 0
        with run_lock(workflow.directory):
            # Reload state under the lock, including after waiting to acquire it.
            workflow = Workflow(args.run_id, args.config)
            if args.command == 'run' and workflow.path.exists():
                raise ValueError('run already exists; use status or resume')
            if args.command != 'run' and not workflow.path.exists():
                raise ValueError('unknown run ID')
            if args.repair_from:
                workflow.repair(args.repair_from, args.repair_note)
            workflow.run(args, publish=args.command == 'publish', stop_after=args.stop_after)
            print(json.dumps({'status': workflow.state['status'], 'state_path': str(workflow.path)}))
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0
