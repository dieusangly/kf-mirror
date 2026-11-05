"""Additive mode histories and reviewed import, in the isolated test schema only."""
import pytest

from knowledge_foundry.reasoning.catalogue import accepted_projection, load_dataset
from knowledge_foundry.storage.postgres import PostgresStore


def test_old_conversations_backfill_as_document_history(store, published):
    _, snapshot = published
    token = 'private-history-token-' * 3
    session = store.new_session(snapshot, token)
    answer = {'status': 'extractive', 'text': 'Existing document answer', 'citations': []}
    store.save_turn(session, token, snapshot, 'Existing question', answer)
    # SYNTHETIC migration fixture: reproduce the pre-003 schema and old rows.
    with store.connection() as conn:
        conn.execute('ALTER TABLE kf_sessions DROP COLUMN assistant_mode')
        conn.execute("DELETE FROM kf_migrations WHERE version='003_assistant_history.sql'")
    store.migrate()
    store.migrate()
    reopened = PostgresStore(store.dsn)
    assert reopened.sessions(snapshot, token)[0]['id'] == session
    assert reopened.history(session, token, snapshot, 'document')[-1]['content'] == answer['text']
    assert reopened.sessions(snapshot, token, 'reasoning') == []
    reasoning = reopened.new_session(snapshot, token, 'reasoning')
    assert [s['id'] for s in reopened.sessions(snapshot, token, 'reasoning')] == [reasoning]
    assert [s['id'] for s in reopened.sessions(snapshot, token, 'document')] == [session]
    with pytest.raises(ValueError, match='unavailable'):
        reopened.history(session, token, snapshot, 'reasoning')
    with pytest.raises(ValueError, match='unavailable'):
        reopened.save_turn(reasoning, token, snapshot, 'wrong mode', answer, assistant_mode='document')
    reopened.save_turn(reasoning, token, snapshot, 'Reasoning question', answer, assistant_mode='reasoning')
    assert len(PostgresStore(store.dsn).history(reasoning, token, snapshot, 'reasoning')) == 2
    assert len(reopened.history(session, token, snapshot, 'document')) == 2
    with pytest.raises(ValueError):
        reopened.sessions(snapshot, token, 'unknown')


def test_only_accepted_span_subset_is_imported_idempotently_without_publication(store):
    dataset = load_dataset()
    records, evidence, identity = accepted_projection(dataset)
    first = store.import_snapshot('reviewed-pilot', identity, records, evidence, None)
    again = store.import_snapshot('reviewed-pilot', identity, records, evidence, None)
    assert first['created'] and not again['created']
    assert first['snapshot_id'] == again['snapshot_id']
    assert first['records'] == 3 and first['evidence'] == 7
    with pytest.raises(ValueError, match='published'):
        store.snapshot('reviewed-pilot')
    snapshot = store.evaluation_snapshot('reviewed-pilot', first['snapshot_id'])
    assert snapshot['identity']['formalization_review'] == dataset['formalization']
    assert snapshot['identity']['grounding_review'] == dataset['grounding']
    assert len(snapshot['identity']['sources']) == 3
    assert set(store.evidence(first['snapshot_id'], [e['id'] for e in evidence])) == {e['id'] for e in evidence}
