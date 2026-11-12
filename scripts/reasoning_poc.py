"""Prepare or execute the fixed reasoning POC batch; paid runs need exact approval.

Application import/publication is separately documented in the POC runbook.
Only the disposable local cluster below is writable through this runner.
"""
import argparse
from datetime import datetime
from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path
import sys
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT), str(ROOT / 'tests')]
from knowledge_foundry.reasoning.adapter import require
from knowledge_foundry.reasoning.assistant import ReasoningAssistant, phase_config, settings
from knowledge_foundry.reasoning.catalogue import accepted_projection, load_dataset, resolve_checker
from knowledge_foundry.reasoning.poc import POC_LABEL, current_bindings, load_poc
from knowledge_foundry.reasoning.release import release_evidence
from knowledge_foundry.retrieval.artifacts import file_hash
from knowledge_foundry.retrieval.config import load_config
from knowledge_foundry.storage.postgres import PostgresStore
from knowledge_foundry.structural_chunks import stable_hash

POC = ROOT / 'artifacts/20261028-docling-aml-cft-01/reasoning-poc-01'
CHECKS = POC / 'poc-checks'
TEST_DSN = 'postgresql://127.0.0.1:55484/kf_reasoning_poc'
read = lambda p: json.loads(p.read_text())
now = lambda: datetime.now(ZoneInfo('Europe/Paris')).isoformat()


def write(path, value, *, fresh=False):
    with path.open('x' if fresh else 'w') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')


def isolated_store():
    """Verify the physical target before migrations, imports, sessions or publication."""
    import psycopg
    with psycopg.connect(TEST_DSN) as conn:
        require(conn.execute('SHOW data_directory').fetchone()[0] == '/tmp/kf-reasoning-poc-postgres',
                'only the dedicated disposable POC cluster is allowed')
    store = PostgresStore(TEST_DSN)
    store.migrate()
    return store


def projection():
    poc = load_poc(POC / 'poc-config.json', 'reasoning-poc-01')
    dataset = load_dataset(poc['dataset_path'])
    records, evidence, identity = accepted_projection(dataset)
    require(len(records) == 2164 and len(evidence) == 3506, 'frozen POC population mismatch')
    imported_identity = {**identity, 'corpus': poc['corpus'], 'language': 'simple', 'embedding_profile': None,
                         'records_sha256': stable_hash(records), 'evidence_sha256': stable_hash(evidence),
                         'record_count': len(records), 'evidence_count': len(evidence)}
    return poc, dataset, records, evidence, identity, 'snapshot:' + stable_hash(imported_identity)


def prepare():
    poc, data, records, evidence, identity, sid = projection()
    cfg = load_config()
    opts = settings(cfg)
    models = {}
    for phase in ('interpretation', 'interpretation_audit', 'explanation', 'explanation_audit'):
        base = 'explanation' if phase == 'explanation' else 'interpretation'
        effective = phase_config(cfg, opts, base)
        models[phase] = {k: effective['answer'][k] for k in ('provider', 'model', 'reasoning_effort')}
        models[phase]['effective_max_output_tokens'] = effective['budget']['output_tokens']
    cases = read(POC / poc['examples'])['cases']
    # Oracle plans remain in the separate inventory; never send them in a live request.
    scope = {'case_ids': [c['id'] for c in cases], 'cases': [
        {k: c[k] for k in ('id', 'question', 'source_ids', 'document_ids', 'expected_meaning',
                            'answer_category', 'expected_status', 'expected_proof')} for c in cases],
        'application_models': models, 'code_config_bindings': current_bindings(),
        'input_bindings': {'examples_sha256': file_hash(POC / poc['examples']),
                           'poc_config_sha256': file_hash(POC / 'poc-config.json'),
                           'integration_sha256': file_hash(data['directory'] / 'integration.json'),
                           'dataset_sha256': data['digest'], 'release_identity': data['release_identity']},
        'snapshot_id': sid,
        'limits': {'max_provider_requests': 36, 'max_requests_per_case': 4,
                   'max_input_tokens_per_request': 12000, 'max_output_tokens_per_request': 1600,
                   'max_total_input_tokens': 432000, 'max_total_output_tokens': 57600,
                   'request_reservation_usd': 0.0055, 'spending_cap_usd': 0.25,
                   'automatic_retries': 0},
        'processing': 'Standard/default', 'application_database_writes': False,
        'execution': 'Normal ReasoningAssistant.ask in the dedicated disposable PostgreSQL cluster; one fresh conversation per question.'}
    require(all(v['model'] == 'gpt-6-luna' and v['effective_max_output_tokens'] == 1600 for v in models.values()),
            'model configuration differs from proposed run; do not substitute models')
    proposal = {'status': 'awaiting_new_authorization', 'prepared_at': now(), 'scope': scope,
                'scope_sha256': stable_hash(scope), 'session_f_authorization_reused': False,
                'pricing': {'verified_on': '2026-11-12', 'official_source': 'https://developers.openai.com/api/docs/pricing',
                            'model_source': 'https://developers.openai.com/api/docs/models/gpt-6-luna',
                            'input_per_million': 0.1, 'cached_input_per_million': 0.01,
                            'cache_write_30m_per_million': 0.125, 'cache_write_12h_per_million': 0.25,
                            'output_per_million': 0.5,
                            'max_reserved_usd': 0.198,
                            'reservation_note': 'Conservative additional input plus 12h-write allowance and 10% uplift: (12000*(.1+.25)+1600*.5)*1.1/1e6 = .0055 per request. Billing is unverified.'}}
    write(CHECKS / 'poc-live-batch.json', proposal)
    print(json.dumps({k: proposal[k] for k in ('status', 'scope_sha256')}, indent=2))


def legacy_recorder():
    """Reuse the observed application transport, with POC-only paths and gates."""
    path = POC.with_name('reasoning-release-01') / 'checks/final-live-runtime.py'
    spec = importlib.util.spec_from_file_location('poc_historical_transport', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PER_REQUEST = Decimal('.0055')
    class PocRecorder(module.Recorder):
        def __init__(self, scope, authorization):
            from knowledge_foundry.retrieval import answering
            self.matrix, self.authorization = scope, authorization
            self.original_stream, self.original_urlopen = answering.openai_stream, answering.urlopen
            self.requests, self.current, self.stop_reason = [], None, None
            self.code = scope['code_config_bindings']
            require(not (CHECKS / 'poc-live-requests.json').exists(), 'paid run already exists; never replay automatically')

        def bindings(self):
            require(self.code == current_bindings(), 'runtime/configuration changed')
            require(file_hash(POC / 'poc-examples.json') == self.matrix['input_bindings']['examples_sha256'], 'inventory changed')
            require(file_hash(POC / 'poc-config.json') == self.matrix['input_bindings']['poc_config_sha256'], 'POC config changed')

        def stream(self, payload, config):
            require(len(self.requests) < self.matrix['limits']['max_provider_requests'], 'request ceiling reached')
            yield from super().stream(payload, config)

        def checkpoint(self):
            # Reprice raw cache-write usage; the historical initial estimate omits it.
            for request in self.requests:
                raw = request.get('raw_usage')
                if isinstance(raw, dict) and all(type(raw.get(k)) is int and raw[k] >= 0 for k in ('input_tokens', 'output_tokens')):
                    i, o = raw['input_tokens'], raw['output_tokens']
                    details = raw.get('input_tokens_details', {})
                    cached, written = details.get('cached_tokens', 0), details.get('cache_write_tokens', 0)
                    require(type(cached) is int and type(written) is int and 0 <= cached + written <= i, 'invalid provider usage')
                    def price(rate):
                        return float((Decimal(i-cached-written)*Decimal('.1') + Decimal(cached)*Decimal('.01') +
                                      Decimal(written)*Decimal(rate) + Decimal(o)*Decimal('.5')) / Decimal(1000000))
                    request['usage_estimate_30m_usd'] = price('.125')
                    request['usage_estimate_12h_usd'] = price('.25')
                    request['conservative_usage_estimate_usd'] = float((Decimal(i)*Decimal('.35')+Decimal(o)*Decimal('.5'))*Decimal('1.1')/Decimal(1000000))
            reserved = sum(Decimal(str(r['reserved_maximum_usd'])) for r in self.requests)
            require(reserved <= Decimal(str(self.authorization['approved_spending_cap_usd'])), 'spending cap exceeded')
            write(CHECKS / 'poc-live-requests.json', {'updated_at': now(), 'request_count': len(self.requests),
                'committed_maximum_reservations_usd': float(reserved), 'cap_usd': self.authorization['approved_spending_cap_usd'],
                'known_usage_estimate_30m_usd': sum(r.get('usage_estimate_30m_usd', 0) for r in self.requests),
                'known_usage_estimate_12h_usd': sum(r.get('usage_estimate_12h_usd', 0) for r in self.requests),
                'conservative_usage_or_reservation_usd': sum(r.get('conservative_usage_estimate_usd', r['reserved_maximum_usd']) for r in self.requests),
                'unknown_usage_requests': sum(r.get('raw_usage') is None for r in self.requests),
                'billing_verified': False, 'automatic_retries': 0, 'stop_reason': self.stop_reason, 'requests': self.requests})
    return PocRecorder


def assess(case, answer, evidence, mapping_evidence):
    """Mechanical safety checks; prose fidelity always needs separate source review."""
    retrieval = answer['retrieval']; proof = retrieval['reasoning']
    diagnostics = retrieval['semantic_checks']
    checks = {'expected_status': answer['status'] == case['expected_status'],
              'language': answer.get('language') == case['evaluation_oracle']['language']}
    if case['expected_status'] == 'reasoned':
        cites = proof.get('citations', [])
        mids = [m['id'] for m in proof.get('mappings', [])]
        required = set().union(*(set(mapping_evidence.get(mid, [])) for mid in mids))
        checks.update(proof_kind=proof.get('status') == case['expected_proof'],
            certificate=proof.get('certificate', {}).get('status') == 'accepted',
            complete_proof_citations=bool(required) and required == {e['id'] for e in cites},
            exact_citations=bool(cites) and all(c['id'] in evidence and all(c[k] == evidence[c['id']][k] for k in
                ('reference', 'text', 'source', 'document', 'location', 'element_kind')) for c in cites),
            displayed_citations=bool(answer['citations']) and {c['id'] for c in answer['citations']} ==
                {c['id'] for c in cites if c['role'] == 'source_passage'},
            interpretation_audit=bool(diagnostics.get('interpretation_audit')) and all(diagnostics['interpretation_audit'].values()),
            explanation_audit=bool(diagnostics.get('explanation_audit')) and all(diagnostics['explanation_audit'].values()))
    else:
        checks.update(no_claim=not answer['citations'] and not proof.get('conclusions'),
                      no_explanation=not any(p['phase'] == 'explanation' for p in answer['telemetry']['phases']))
    return {'checks': checks, 'mechanical_pass': all(checks.values()), 'source_review': 'pending'}


def execute(mode, authorization_path=None):
    poc, data, records, spans, identity, sid = projection()
    cases = read(POC / poc['examples'])['cases']
    scope = read(CHECKS / 'poc-live-batch.json')['scope']
    require(scope['code_config_bindings'] == current_bindings(), 'prepare current code/configuration bindings first')
    require(scope['input_bindings']['dataset_sha256'] == data['digest'] and scope['snapshot_id'] == sid, 'dataset binding changed')
    recorder = None
    if mode == 'live':
        require(authorization_path is not None, 'new explicit paid-run authorization required')
        auth = read(authorization_path)
        require(auth['approved_scope_sha256'] == stable_hash(scope) and auth['approved_spending_cap_usd'] == .25,
                'authorization does not cover this exact POC batch and USD .25 cap')
        require(auth['approved'] is True and auth['case_ids'] == scope['case_ids'], 'all nine cases require approval')
        require(bool(os.environ.get('OPENAI_API_KEY')), 'provider credentials unavailable')
        recorder = legacy_recorder()(scope, auth)
    from rag_fixtures import json_stream, excerpt_explanation, faithful_audit
    store = isolated_store()
    imported = store.import_snapshot(poc['corpus'], identity, records, spans, None, 'simple')
    require(imported['snapshot_id'] == sid, 'import snapshot differs from prepared identity')
    store.publish(poc['corpus'], sid)  # Dedicated disposable cluster only.
    evidence, mapping_evidence = release_evidence(data)
    proof_dir = Path('/tmp') / ('poc-' + mode + '-proofs-' + uuid4().hex)
    report_path = CHECKS / ('poc-' + mode + '-summary.json')
    require(not report_path.exists(), 'never overwrite prior batch evidence')
    report = {'mode': mode, 'started_at': now(), 'status': 'running', 'snapshot_id': sid,
              'dataset_sha256': data['digest'], 'release_identity': data['release_identity'],
              'code_config_bindings': current_bindings(), 'examples_sha256': file_hash(POC / poc['examples']),
              'accepted_mappings': 2164, 'disposable_imported_mappings': imported['records'],
              'application_imported_mappings': 0, 'application_active_mappings': 0,
              'model_configuration': scope['application_models'], 'application_database_writes': False,
              'results': [], 'remaining_case_ids': [c['id'] for c in cases], 'provider_requests': 0,
              'language_quality': 'unverified fixtures' if mode == 'offline' else 'pending source review'}
    write(report_path, report, fresh=True)
    for case in cases:
        if recorder and recorder.stop_reason:
            report['stop_reason'] = recorder.stop_reason
            break
        started = monotonic()
        session_token = uuid4().hex; session = store.new_session(sid, session_token, 'reasoning')
        if recorder:
            recorder.current = case['id']
            streams = dict.fromkeys(('interpretation_stream', 'answer_stream', 'audit_stream'), recorder.stream)
        else:
            streams = {'interpretation_stream': json_stream(case['evaluation_oracle']),
                       'answer_stream': excerpt_explanation, 'audit_stream': faithful_audit}
        assistant = ReasoningAssistant(store, load_config(), dataset_dir=poc['dataset_path'],
            output_dir=proof_dir, checker=resolve_checker(), experience_label=POC_LABEL, **streams)
        row = {'id': case['id'], 'question': case['question'], 'source_ids': case['source_ids'],
               'document_ids': case['document_ids'], 'expected_meaning': case['expected_meaning'],
               'answer_category': case['answer_category'], 'started_at': now(),
               'verification_result': 'pending_source_review' if mode == 'live' else 'offline_fixture_only', 'ui_verified': False}
        try:
            answer = assistant.ask(poc['corpus'], case['question'], token=session_token, session_id=session,
                                   snapshot_id=sid, source_ids=case['source_ids'], document_ids=case['document_ids'])
            saved = store.history(session, session_token, sid, 'reasoning')[-1]['payload']
            row.update(answer=answer, actual_displayed_answer=answer['text'], actual_citations=answer['citations'],
                asserted_versus_inferred=answer['retrieval']['reasoning'].get('status'),
                rule_premise_proof_references={k: answer['retrieval']['reasoning'].get(k) for k in
                                               ('rules', 'premises', 'proof', 'certificate')},
                assessment=assess(case, answer, evidence, mapping_evidence),
                persisted_answer_matches=all(saved[k] == v for k, v in answer.items() if k != 'message_id'),
                snapshot_id=sid, dataset_sha256=data['digest'])
        except Exception as exc:
            row.update(verification_result='harness_failure', error_type=type(exc).__name__)
        row['whole_case_seconds'] = round(monotonic()-started, 3)
        filename = 'poc-' + mode + '-case-' + case['id'] + '.json'
        write(CHECKS / filename, row, fresh=True)
        report['results'].append({'id': case['id'], 'evidence': 'poc-checks/' + filename,
            'evidence_sha256': file_hash(CHECKS / filename), 'mechanical_pass': row.get('assessment', {}).get('mechanical_pass', False),
            'verification_result': row['verification_result'], 'ui_verified': False,
            'whole_case_seconds': row['whole_case_seconds']})
        report.update(remaining_case_ids=report['remaining_case_ids'][1:],
                      provider_requests=len(recorder.requests) if recorder else 0)
        write(report_path, report)
        print(case['id'], row.get('answer', {}).get('status'), row['whole_case_seconds'], flush=True)
    archive = CHECKS / ('poc-' + mode + '-proofs.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as zipped:
        if proof_dir.exists():
            for path in sorted(proof_dir.rglob('*')):
                if path.is_file():
                    # Fixed upstream checker protocol names retained inside the POC archive.
                    zipped.write(path, str(path.relative_to(proof_dir)))
    report.update(status='complete_pending_source_review' if not report['remaining_case_ids'] else 'incomplete',
                  completed_at=now(), proof_archive=str(archive.relative_to(POC)), proof_archive_sha256=file_hash(archive))
    write(report_path, report)


def finalize_review(path):
    report = read(CHECKS / 'poc-live-summary.json')
    review = read(path)
    require(not report['remaining_case_ids'], 'all fixed questions must have recorded outcomes')
    require(report['code_config_bindings'] == current_bindings(), 'source review is stale')
    verdicts = {r['id']: r for r in review['results']}
    require(set(verdicts) == {r['id'] for r in report['results']}, 'review all outcomes, including failures')
    for row in report['results']:
        verdict = verdicts[row['id']]
        require(verdict['evidence_sha256'] == row['evidence_sha256'] == file_hash(POC / row['evidence']), 'review output binding mismatch')
        require(verdict.get('basis') and verdict.get('reviewer'), 'explicit separate source review required')
        passed = row['mechanical_pass'] and verdict['verdict'] == 'pass'
        row.update(verification_result='pass' if passed else 'fail', ui_verified=passed, source_review=verdict)
    report['status'] = 'reviewed'
    report['source_review_file'] = str(path.resolve())
    write(CHECKS / 'poc-verification.json', report, fresh=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'offline', 'live', 'review'])
    parser.add_argument('--authorization', type=Path)
    parser.add_argument('--source-review', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare': prepare()
    elif args.command == 'review': finalize_review(args.source_review)
    else: execute(args.command, args.authorization)


if __name__ == '__main__': main()
