"""Unreviewed release copies in disposable PostgreSQL cannot gain runtime admission."""
import json
import shutil
from pathlib import Path

import pytest

from knowledge_foundry.reasoning.assistant import ReasoningAssistant
from knowledge_foundry.reasoning.catalogue import resolve_checker
from knowledge_foundry.reasoning.release import inspect_release, release_projection
from knowledge_foundry.retrieval.config import ROOT, load_config
from scripts.setup_reasoning_assistant import ReleaseEvaluationAssistant, ReleaseEvaluationStore
from rag_fixtures import json_stream, faithful_audit, excerpt_explanation

RELEASE = ROOT / 'artifacts/20261028-docling-aml-cft-01/reasoning-release-01'


@pytest.fixture(scope='module')
def candidate(tmp_path_factory):
    if not (RELEASE / 'integration.json').exists(): pytest.skip('local frozen release required')
    # Alter only a disposable copy; the actual release has completed reviews.
    directory = tmp_path_factory.mktemp('unreviewed-storage-release') / 'release'
    shutil.copytree(RELEASE, directory,
                    ignore=shutil.ignore_patterns('proofs', 'postgres', '__pycache__'))
    contract_path = directory / 'integration.json'
    contract = json.loads(contract_path.read_text())
    contract['reviews'] = {}
    contract_path.write_text(json.dumps(contract))
    return inspect_release(directory)


@pytest.fixture(autouse=True)
def no_paid(monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail('no paid calls in isolated release tests')
    for target in ['knowledge_foundry.reasoning.assistant.openai_stream',
                   'knowledge_foundry.retrieval.answering.openai_stream',
                   'knowledge_foundry.retrieval.query.openai_stream',
                   'knowledge_foundry.embeddings.EmbeddingAdapter._request']:
        monkeypatch.setattr(target, forbidden)


def test_all_required_evidence_imported_exactly_and_idempotently_without_publication(store, candidate):
    records, evidence, identity = release_projection(candidate, evaluation=True)
    first = store.import_snapshot('release-evaluation', identity, records, evidence, None)
    again = store.import_snapshot('release-evaluation', identity, records, evidence, None)
    assert first['created'] and not again['created'] and first['snapshot_id'] == again['snapshot_id']
    assert first['records'] == 2164 and first['evidence'] == 3506
    with pytest.raises(ValueError, match='published'): store.snapshot('release-evaluation')
    snapshot = store.evaluation_snapshot('release-evaluation', first['snapshot_id'])
    assert snapshot['identity']['release_identity'] == candidate['release_identity']
    assert snapshot['identity']['chunk_build_id'].startswith('release-evidence:')
    assert len(snapshot['identity']['documents']) == 9
    assert len(snapshot['identity']['unprojected_lineage_document_ids']) == 4
    assert {d['id'] for d in snapshot['identity']['documents']} == {e['document']['id'] for e in evidence}
    fetched = store.evidence(first['snapshot_id'], [e['id'] for e in evidence])
    assert fetched == {e['id']: e for e in evidence}
    assert not store.snapshots('different-corpus')
    other = store.import_snapshot('another-release', {**identity, 'test_marker': True}, records, evidence, None)
    with pytest.raises(ValueError): store.evaluation_snapshot('release-evaluation', other['snapshot_id'])


def test_assistant_proofs_resolve_isolated_ready_snapshot_and_reject_filters(store, candidate, tmp_path):
    records, evidence, identity = release_projection(candidate, evaluation=True)
    sid = store.import_snapshot('release-evaluation', identity, records, evidence, None)['snapshot_id']
    checker = resolve_checker()
    if not Path(checker).is_file(): pytest.skip('pinned checker required')
    row = next(r for r in candidate['mapping']['mappings'] if r['facts'])
    claim = next(c for c in candidate['bundle']['claims'] if c['id'] == row['claim_id'])
    question = claim['text']
    plan = {'status': 'ready', 'language': 'en', 'message': '', 'operation': 'check',
            'jurisdiction': row['jurisdiction'], 'as_of': row['valid_from'], 'patterns': [row['facts'][0]], 'select': []}
    evaluation_store = ReleaseEvaluationStore(candidate, database=store, corpus='release-evaluation', snapshot_id=sid)
    assistant = ReleaseEvaluationAssistant(candidate, load_config(), store=evaluation_store, checker=checker,
        output_dir=tmp_path / 'proofs', interpretation_stream=json_stream(plan),
        answer_stream=excerpt_explanation, audit_stream=faithful_audit)
    answer = assistant.ask('release-evaluation', question, token='local', session_id='local', snapshot_id=sid)
    assert answer['status'] == 'reasoned'
    assert answer['knowledge_status'] == 'runtime_review_pending_evaluation'
    assert answer['retrieval']['reasoning']['certificate']['status'] == 'accepted'
    assert answer['retrieval']['reasoning']['evaluation_only']
    # Corrupt only the isolated SQL evidence payload, then exercise the cached
    # engine and complete proof-resolution path against that actual database.
    key = answer['retrieval']['reasoning']['citations'][0]['id']
    original = store.evidence(sid, [key])[key]
    with store.connection() as conn:
        conn.execute("UPDATE kf_evidence SET payload=jsonb_set(payload,'{document,source_version}','\"forged\"'::jsonb) WHERE snapshot_id=%s AND id=%s", (sid, key))
    changed = assistant.ask('release-evaluation', question, token='local', session_id='local', snapshot_id=sid)
    assert changed['status'] == 'insufficient' and not changed['citations']
    assert changed['retrieval']['reasoning']['reason_code'] == 'missing_citations'
    from psycopg.types.json import Jsonb
    with store.connection() as conn:
        conn.execute('UPDATE kf_evidence SET payload=%s WHERE snapshot_id=%s AND id=%s', (Jsonb(original), sid, key))
    filtered = assistant.ask('release-evaluation', question, token='local', session_id='local', snapshot_id=sid,
                             source_ids=['src:deliberately-absent'])
    assert filtered['status'] == 'insufficient' and not filtered['citations']
    with pytest.raises(ValueError):
        evaluation_store.snapshot('another-corpus', sid)
    # Even with complete evidence imported, production cannot bypass the review gate.
    production = ReasoningAssistant(evaluation_store, load_config(), dataset_dir=candidate['directory'])
    denied = production.ask('release-evaluation', question, token='local', session_id='local', snapshot_id=sid)
    assert denied['status'] == 'unavailable'
    assert denied['retrieval']['reasoning']['reason'] == 'review'
