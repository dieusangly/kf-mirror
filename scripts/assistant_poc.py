"""Prepare read-only POC scopes/backups; capped paid verification requires new approval."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from knowledge_foundry.assistant.bindings import KnowledgeBinding
from knowledge_foundry.assistant.service import Assistant
from knowledge_foundry.retrieval.artifacts import file_hash
from knowledge_foundry.retrieval.config import load_config
from knowledge_foundry.storage.postgres import PostgresStore
from knowledge_foundry.structural_chunks import stable_hash

OUT = ROOT / 'artifacts/20261028-docling-aml-cft-01/20261112-unified-assistant-poc'
ISOLATED_DSN = 'postgresql://127.0.0.1:55484/kf_unified_assistant_poc'
ISOLATED_ADMIN = 'postgresql://127.0.0.1:55484/postgres'
MIGRATION = ROOT / 'src/knowledge_foundry/storage/migrations/004_assistant_poc_conversations.sql'


def write(name, value, *, fresh=False):
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / name).open('x' if fresh else 'w') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, default=str)
        f.write('\n')


def now():
    return datetime.now(ZoneInfo('Europe/Paris')).isoformat()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def isolated(dsn=ISOLATED_DSN):
    with psycopg.connect(dsn) as conn:
        require(conn.execute('SHOW data_directory').fetchone()[0] == '/tmp/kf-reasoning-poc-postgres',
                'refusing a non-disposable physical database')
        require(conn.execute('SELECT current_database()').fetchone()[0] == 'kf_unified_assistant_poc',
                'refusing a database outside this POC scope')
    return PostgresStore(dsn)


def state(store):
    """Consistent read-only row digests, including unchanged legacy histories."""
    result = {}
    with store.connection() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        conn.execute("SET LOCAL TIME ZONE 'UTC'")
        tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() "
                              "AND tablename LIKE 'kf_%%' ORDER BY tablename").fetchall()
        for table in tables:
            name = table['tablename']
            # Database canonical JSON text avoids transferring private history.
            digest = hashlib.sha256()
            rows = conn.execute(sql.SQL("SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS digest FROM {} t")
                                .format(sql.Identifier(name))).fetchall()
            for row_digest in sorted(r['digest'] for r in rows):
                digest.update((row_digest + '\n').encode())
            result[name] = {'count': len(rows), 'sha256': digest.hexdigest()}
    return result


def code_bindings():
    from knowledge_foundry.reasoning.catalogue import resolve_checker
    checker = Path(resolve_checker())
    files = [* (ROOT / 'src/knowledge_foundry').rglob('*.py'),
             *(ROOT / 'src/knowledge_foundry/prompts').rglob('*.txt'), MIGRATION,
             *(ROOT / 'src/knowledge_foundry/storage/migrations').glob('*.sql'),
             ROOT / 'apps/chat/app.py', Path(__file__), ROOT / 'config/assistant-poc.json',
             ROOT / 'config/retrieval.json', ROOT / 'config/reasoning-assistant.json',
             ROOT / 'requirements.txt', checker]
    effective_reasoning = Path(os.environ.get('KF_REASONING_CONFIG', ROOT / 'config/reasoning-assistant.json')).resolve()
    files.append(effective_reasoning)
    if checker.with_name('manifest.json').is_file():
        files.append(checker.with_name('manifest.json'))
    return {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p): file_hash(p)
            for p in sorted(set(files))}


def frozen_bindings():
    root = ROOT / 'artifacts/20261028-docling-aml-cft-01'
    return {str(p.relative_to(ROOT)): file_hash(p) for directory in
            ('reasoning-release-01', 'reasoning-poc-01', 'formalization-v2')
            for p in sorted((root / directory).rglob('*')) if p.is_file()}


def cases(binding):
    original = json.loads((ROOT / binding.value['accepted']['poc_reference']).with_name('poc-examples.json').read_text())['cases']
    decree = original[0]['question']
    rows = [
        ('poc-cosi-fr', 'context', "C'est quoi une COSI ?"),
        ('poc-followup-fr', 'context', "Et qu'est-ce qui la distingue d'une déclaration de soupçon ?"),
        ('poc-followup-en', 'context', 'Explain that distinction in English, preserving the qualifications.'),
        ('poc-accepted-fact', 'accepted', 'Vérifiez dans les connaissances acceptées : ' + decree),
        ('poc-multipart', 'mixed', "Expliquez ce qu'est une COSI, puis vérifiez dans les connaissances acceptées : " + decree),
        ('poc-missing-mapping', 'definition', 'Que signifie SICAV dans les lignes directrices ACPR sur la connaissance de la clientèle ?'),
        ('poc-missing-evidence', 'missing', 'What is the exact unpublished 2031 budget of the fictional organisation Zyxqv?'),
        ('poc-filtered', 'filtered', "C'est quoi une COSI ?"),
        ('poc-missing-premise', 'premise', original[7]['question']),
        ('poc-inventory', 'inventory', 'Documents in corpus'),
    ]
    return [{'id': cid, 'conversation': conv, 'question': question,
             'source_ids': ['src:bcbs-aml-cft-risk-management-2020'] if cid == 'poc-filtered' else None,
             'document_ids': None, 'intent': 'document_inventory' if cid == 'poc-inventory' else None}
            for cid, conv, question in rows]


def prepare():
    require(not (OUT / 'poc-backup-restore.json').exists(), 'a prepared backup already exists; verify it instead of overwriting')
    require((OUT / 'poc-prior-app.py').is_file(), 'preserve the prior app source before preparing activation')
    binding = KnowledgeBinding.load()
    source = PostgresStore()
    prior = state(source)
    write('poc-application-before.json', prior)
    require('kf_assistant_conversations' not in prior, 'application already has POC storage; revise the concrete scope')
    compatibility = binding.validate(source)
    frozen = frozen_bindings()
    OUT.mkdir(parents=True, exist_ok=True)
    backup = OUT / 'poc-before.dump'
    if not backup.exists():
        dumped = subprocess.run(['pg_dump', '--dbname', source.dsn, '--format=custom', '--file', str(backup)],
                                check=False, capture_output=True)
        require(dumped.returncode == 0, 'pg_dump failed; no application writes occurred')
    # Verify an actual isolated restore, not only an archive listing.
    with psycopg.connect(ISOLATED_ADMIN, autocommit=True) as conn:
        require(conn.execute('SHOW data_directory').fetchone()[0] == '/tmp/kf-reasoning-poc-postgres', 'wrong restore cluster')
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname='kf_unified_assistant_poc'").fetchone()
        if not exists:
            conn.execute('CREATE DATABASE kf_unified_assistant_poc')
    if not exists:
        subprocess.run(['pg_restore', '--no-owner', '--no-acl', '--exit-on-error', '--dbname', ISOLATED_DSN,
                        str(backup)], check=True, capture_output=True)
    restored = isolated()
    restored_state = state(restored)
    after = state(source)
    require(prior == after == restored_state, 'application changed during backup or restored rows differ')
    write('poc-backup-restore.json', {'checked_at': now(), 'backup': backup.name,
        'backup_sha256': file_hash(backup), 'application_writes': False,
        'restore_target': ISOLATED_DSN, 'prior_state': prior, 'restored_state': restored_state,
        'all_tables_equal': True, 'binding_compatibility': compatibility}, fresh=True)
    write('poc-preservation-before.json', frozen, fresh=True)
    restored.migrate()  # Only the physically verified disposable target.
    write('poc-application-write-scope.json', {'status': 'awaiting_new_scoped_approval',
        'prepared_at': now(), 'prior_state': prior, 'backup_restore': 'poc-backup-restore.json',
        'migration': str(MIGRATION.relative_to(ROOT)), 'migration_sha256': file_hash(MIGRATION),
        'writes': ['apply only migration 004 and its kf_migrations receipt',
                   'create empty Assistant conversations only on explicit user action',
                   'save one user/final assistant pair per submission in the additive tables'],
        'bindings': binding.value, 'snapshot_imports': 0, 'publications': 0,
        'legacy_mutations': 0, 'production_release_activation': False,
        'process_change': 'after approval: restart only the selected app with KF_ASSISTANT_POC_ENABLED=1',
        'rollback': 'stop the new process; unset KF_ASSISTANT_POC_ENABLED and restore prior source/environment; '
                    'retain additive tables and all histories; no corpus pointer or dataset change',
        'prior_environment': {k: os.environ.get(k) for k in ('KF_ASSISTANT_POC_ENABLED', 'KF_ASSISTANT_POC_CONFIG')}
    }, fresh=True)
    prepare_live(binding)
    print(json.dumps({'backup_restore': 'verified_equal', 'application_writes': False,
                      'compatibility': compatibility, 'output': str(OUT)}))


def prepare_live(binding=None):
    require(not (OUT / 'poc-live-started.json').exists(), 'a paid batch has started; preserve its original scope')
    binding = binding or KnowledgeBinding.load()
    batch = {'format': 'assistant-live-poc-v1', 'bindings': binding.value, 'cases': cases(binding),
        'code_config_bindings': code_bindings(), 'processing': 'Standard/default',
        'limits': {'max_provider_requests': 100, 'max_input_tokens_per_request': 12000,
                   'max_output_tokens_per_request': 1600, 'max_total_input_tokens': 1200000,
                   'max_total_output_tokens': 160000, 'spending_cap_usd': 1.0,
                   'reserved_usd_per_request': 0.01, 'automatic_retries': 0},
        'execution': 'Normal Assistant.ask, actual provider transports, isolated restored database; no application writes',
        'models': {'planner_audits_interpretation': 'gpt-6-luna / medium',
                   'document_answer_reasoning_explanation': 'gpt-6-luna / low'},
        'verification': 'Record every outcome once. Independently inspect actual passages, citations and proof kind. '
                        'No deduction is claimed without a passed certificate and supported accepted premises.'}
    write('poc-live-batch.json', {'scope': batch, 'scope_sha256': stable_hash(batch),
                                'status': 'awaiting_new_paid_approval'})
    path = OUT / 'poc-application-write-scope.json'
    if path.is_file():
        scope = json.loads(path.read_text())
        scope.pop('scope_sha256', None)
        scope['code_config_bindings'] = batch['code_config_bindings']
        scope['bindings'] = binding.value
        scope['prior_app_source'] = {'file': 'poc-prior-app.py', 'sha256': file_hash(OUT / 'poc-prior-app.py')}
        scope['process_change'] = 'after separate approval: replace only the selected local app on port 8501; '
        scope['process_change'] += 'set KF_ASSISTANT_POC_ENABLED=1 and unset OPENAI_API_KEY (no paid interactive calls)'
        scope['writes'] = ['apply only migration 004 (two additive tables and two indexes) and its migration receipt',
                           'execute only the bounded provider-off application smoke below; preserve all old rows']
        scope['application_smoke'] = {'provider_requests': 0, 'usd_cap': 0,
            'fresh_private_owner': True, 'new_conversations': 2, 'new_user_assistant_pairs': 3,
            'cases': [
                {'id':'poc-application-cosi','conversation':1,'question':"C'est quoi une COSI ?",'intent':None},
                {'id':'poc-application-followup','conversation':1,'question':'Et la déclaration de soupçon ?', 'intent':None},
                {'id':'poc-application-inventory','conversation':2,'question':'Documents in corpus','intent':'document_inventory'}],
            'filters': {'source_ids':None,'document_ids':None}, 'provider':'extractive',
            'post_smoke_history_policy':'retain the six new messages and two conversations; no legacy adoption'}
        scope['scope_sha256'] = stable_hash(scope)
        write(path.name, scope)


class LiveLedger:
    def __init__(self, scope):
        self.scope, self.records = scope, []

    def stream(self, payload, config):
        from knowledge_foundry.retrieval.answering import openai_stream, request_tokens
        limits = self.scope['limits']
        tokens, output = request_tokens(payload, config), payload['max_output_tokens']
        require(payload['model'] == 'gpt-6-luna' and not payload.get('store') and payload.get('stream'), 'unapproved provider configuration')
        require(payload.get('reasoning', {}).get('effort') in {'low', 'medium'}, 'unapproved reasoning effort')
        require(tokens <= limits['max_input_tokens_per_request'] and output <= limits['max_output_tokens_per_request'], 'request token ceiling')
        require(len(self.records) < limits['max_provider_requests'] and
            (len(self.records) + 1) * limits['reserved_usd_per_request'] <= limits['spending_cap_usd'] and
            sum(r['reserved_input_tokens'] for r in self.records) + tokens <= limits['max_total_input_tokens'] and
            sum(r['reserved_output_tokens'] for r in self.records) + output <= limits['max_total_output_tokens'], 'live batch ceiling')
        # This reservation exceeds the configured worst case including cache writes.
        pricing = config['answer']['pricing']
        worst = (tokens * (pricing['input_usd_per_million'] + pricing['cache_write_usd_per_million']) +
                 output * pricing['output_usd_per_million']) / 1000000
        require(worst <= limits['reserved_usd_per_request'], 'price reservation no longer covers this request')
        row = {'case': self.case_id, 'reserved_input_tokens': tokens, 'reserved_output_tokens': output,
               'reserved_usd': limits['reserved_usd_per_request'], 'status': 'started', 'usage': None}
        self.records.append(row)
        started = time.monotonic()
        write('poc-live-requests.json', self.records)
        try:
            for event in openai_stream(payload, config):
                if event['type'] == 'usage':
                    row.update(usage=event['usage'], model=event.get('model', payload['model']), status='completed')
                yield event
        finally:
            if row['status'] == 'started':
                row['status'] = 'failed_or_incomplete'
            row['latency_seconds'] = round(time.monotonic() - started, 3)
            write('poc-live-requests.json', self.records)


def live(authorization):
    proposal = json.loads((OUT / 'poc-live-batch.json').read_text())
    scope = proposal['scope']
    approval = json.loads(Path(authorization).read_text())
    require(approval.get('approved') is True and approval.get('scope_sha256') == proposal['scope_sha256'] and
            approval.get('approved_spending_cap_usd') == scope['limits']['spending_cap_usd'], 'new exact paid approval required')
    require(scope['code_config_bindings'] == code_bindings(), 'code/config changed after approval scope')
    require(not (OUT / 'poc-live-started.json').exists(), 'batch already started; automatic replay is forbidden')
    store, binding = isolated(), KnowledgeBinding(scope['bindings'])
    binding.validate(store)
    write('poc-live-started.json', {'started_at': now(), 'scope_sha256': proposal['scope_sha256']}, fresh=True)
    ledger, conversations, outcomes = LiveLedger(scope), {}, []
    token = str(uuid4()) + str(uuid4())
    cfg = load_config()
    assistant = Assistant(store, cfg, binding, route_stream=ledger.stream, audit_stream=ledger.stream,
        document_stream=ledger.stream, interpretation_stream=ledger.stream, reasoning_stream=ledger.stream,
        reasoning_audit_stream=ledger.stream, ranking_stream=ledger.stream, output_dir=OUT / 'poc-live-proofs')
    for case in scope['cases']:
        ledger.case_id = case['id']
        try:
            require(len(ledger.records) < scope['limits']['max_provider_requests'], 'batch_exhausted')
            cid = conversations.setdefault(case['conversation'], None)
            if cid is None:
                cid = store.new_assistant_conversation(token, binding.value)
                conversations[case['conversation']] = cid
            answer = assistant.ask(case['question'], token=token, conversation_id=cid,
                source_ids=case['source_ids'], document_ids=case['document_ids'], intent=case['intent'])
            write(case['id'] + '-live.json', answer, fresh=True)
            outcomes.append({'id': case['id'], 'status': answer['status'],
                             'provider_requests': sum(r['case'] == case['id'] for r in ledger.records),
                             'output_sha256': file_hash(OUT / (case['id'] + '-live.json'))})
        except Exception as exc:
            outcomes.append({'id': case['id'], 'status': 'not_executed_budget' if str(exc) == 'batch_exhausted' else 'failed',
                             'error_type': type(exc).__name__})
        write('poc-live-outcomes.json', {'results': outcomes, 'retries': 0,
            'unexecuted_ids': [c['id'] for c in scope['cases'] if c['id'] not in
                {o['id'] for o in outcomes if o['status'] != 'not_executed_budget'}]})
    print(json.dumps({'outcomes': outcomes, 'requests': len(ledger.records), 'automatic_retries': 0}))


def verify():
    before = json.loads((OUT / 'poc-backup-restore.json').read_text())
    preserved = json.loads((OUT / 'poc-preservation-before.json').read_text())
    current = state(PostgresStore())
    result = {'checked_at': now(), 'application_tables_unchanged': current == before['prior_state'],
              'frozen_artifacts_unchanged': preserved == frozen_bindings(),
              'backup_sha256_matches': file_hash(OUT / before['backup']) == before['backup_sha256'],
              'application_writes': False}
    write('poc-preservation-after.json', result)
    require(all(result[k] for k in ('application_tables_unchanged','frozen_artifacts_unchanged','backup_sha256_matches')), 'preservation check failed')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'prepare-live', 'verify', 'live'])
    parser.add_argument('--authorization')
    args = parser.parse_args()
    if args.command == 'prepare': prepare()
    elif args.command == 'prepare-live': prepare_live()
    elif args.command == 'verify': verify()
    else:
        require(bool(args.authorization), 'live execution requires a scoped authorization file')
        live(args.authorization)
