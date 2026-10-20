"""Real PostgreSQL/pgvector tests, isolated in a unique disposable schema."""
import copy
import json
import secrets

import pytest

from knowledge_foundry.retrieval.artifacts import profile
from knowledge_foundry.retrieval.service import DocumentAssistant
from rag_fixtures import config, projection


def test_import_idempotency_migration_and_publication(store, tmp_path):
    c = config(tmp_path)
    records, evidence, identity = projection()
    first = store.import_snapshot('animals', identity, records, evidence, profile(c['embedding']))
    with pytest.raises(ValueError, match='published'):
        store.snapshot('animals')
    second = store.import_snapshot('animals', identity, records, evidence, profile(c['embedding']))
    assert first['created'] and not second['created']
    assert first['snapshot_id'] == second['snapshot_id']
    store.migrate()
    store.publish('animals', first['snapshot_id'])
    with store.connection() as conn:
        assert conn.execute('SELECT count(*) n FROM kf_records').fetchone()['n'] == len(records)
        assert conn.execute('SELECT count(*) n FROM kf_vectors').fetchone()['n'] == len(records)


def test_failed_import_rolls_back_without_changing_active_snapshot(store, published):
    c, sid = published
    records, evidence, identity = projection()
    broken = copy.deepcopy(records)
    broken[0]['vector'] = [1, 0]
    with pytest.raises(ValueError, match='dimensions'):
        store.import_snapshot('animals', {**identity, 'revision': 2}, broken, evidence, profile(c['embedding']))
    assert store.snapshot('animals')['id'] == sid
    assert len(store.snapshots()) == 1


def test_snapshot_and_corpus_isolation_and_retained_history(store, published):
    c, old = published
    records, evidence, identity = projection()
    newer = store.import_snapshot('animals', {**identity, 'revision': 2}, records, evidence, profile(c['embedding']))['snapshot_id']
    store.publish('animals', newer)
    assert store.snapshot('animals')['id'] == newer
    assert store.snapshot('animals', old)['id'] == old
    with pytest.raises(ValueError):
        store.snapshot('other', old)
    with pytest.raises(ValueError):
        store.publish('other', old)


def test_pgvector_keyword_combined_and_filters(store, published):
    c, sid = published
    snapshot = store.snapshot('animals', sid)
    rows = {}
    for method in ['keyword', 'vector', 'combined']:
        rows[method] = store.search(snapshot, 'exercise', 'passage', method, c['search'], vector=[1., 0., 0.])
        assert rows[method][0]['text'].startswith('Dogs must')
        assert store.search(snapshot, 'rest', 'passage', method, c['search'], vector=[1., 0., 0.], source_ids=['missing']) == []
    assert store.search(snapshot, 'rest', 'passage', 'keyword', c['search'], document_ids=['missing']) == []
    assert store.evidence(sid, rows['combined'][0]['evidence_ids'])


def test_conversations_feedback_followups_and_retrieval_are_isolated(store, published):
    c, sid = published
    token, other = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    session = store.new_session(sid, token)
    assistant = DocumentAssistant(store, c)
    answer = assistant.ask('animals', 'Dogs rest exercise', token=token, session_id=session)
    assert answer['citations'][0]['reference']['element_id']
    from knowledge_foundry.retrieval.evaluation import audit_answer
    audit = audit_answer(answer, store)
    assert audit['citation_resolution_rate'] == 1
    assert audit['exact_quote_support_rate'] == 1
    assert store.history(session, token)[1]['payload']['status'] == 'extractive'
    store.feedback(session, token, answer['message_id'], 1, 'supported')
    with pytest.raises(ValueError):
        store.feedback(session, other, answer['message_id'], -1)
    with pytest.raises(ValueError):
        store.history(session, other)
    assert store.sessions(sid, other) == []
    followup = assistant.ask('animals', 'What about exceptions?', token=token, session_id=session)
    assert 'Dogs rest exercise' in followup['retrieval']['query']
    assert 'What about exceptions?' in followup['retrieval']['query']
    assert followup['retrieval']['passage_ids']
    assert len(store.history(session, token)) == 4
    new = store.new_session(sid, token)
    assert not store.history(new, token)


def test_claim_and_summary_navigation_resolve_to_original_passages(store, published):
    c, sid = published
    assistant = DocumentAssistant(store, c)
    for mode, query in [('claim', 'Canine recovery proposition'), ('summary', 'Overview animal recovery')]:
        result = assistant.retrieve('animals', query, snapshot_id=sid, method='keyword', mode=mode)
        assert result['navigation_ids'][0].startswith(mode + ':')
        assert result['passage_ids'][0].startswith('structural:')
        assert result['evidence'][0]['text'].startswith('Dogs must')
        assert all('proposition' not in e['text'] for e in json.loads(result['payload']['input'])['evidence'])


def test_embedding_query_profile_mismatch_and_cached_fixture_query(store, published, tmp_path):
    from knowledge_foundry.structural_chunks import stable_hash
    c, sid = published
    fixture = {'embedding_vectors': {stable_hash('Dogs rest exercise'): [1., 0., 0.]}}
    assistant = DocumentAssistant(store, c, embedding_fixture=fixture)
    result = assistant.retrieve('animals', 'Dogs rest exercise', method='vector')
    assert result['passage_ids']
    again = assistant.retrieve('animals', 'Dogs rest exercise', method='combined')
    assert again['embedding_usage']['cache_hits'] == 1
    c['embedding']['model'] = 'different'
    with pytest.raises(ValueError, match='differ from snapshot'):
        assistant.retrieve('animals', 'rest', method='vector')


def test_artifact_importer_is_repeatable_and_never_publishes(store, tmp_path):
    from knowledge_foundry.retrieval.config import ROOT
    from knowledge_foundry.retrieval.indexing import index_artifacts
    c = config(tmp_path)
    kwargs = {'artifact_path': ROOT / 'examples/artifact-bundle-pdf.json'}
    first = index_artifacts(store, 'example', c, **kwargs)
    second = index_artifacts(store, 'example', c, **kwargs)
    assert first['snapshot_id'] == second['snapshot_id']
    assert not second['created']
    with pytest.raises(ValueError, match='published'):
        store.snapshot('example')
    assert first['embedding_usage']['requests'] == 0


def test_incomplete_lineage_and_incomplete_snapshot_cannot_publish(store, published):
    c, sid = published
    records, evidence, identity = projection()
    broken = copy.deepcopy(records)
    broken[0]['evidence_ids'] = ['missing']
    with pytest.raises(ValueError, match='unresolved evidence'):
        store.import_snapshot('animals', identity, broken, evidence, profile(c['embedding']))
    new = store.import_snapshot('animals', {**identity, 'revision': 2}, records, evidence, profile(c['embedding']))['snapshot_id']
    with store.connection() as conn:
        conn.execute('DELETE FROM kf_evidence WHERE snapshot_id=%s', (new,))
    with pytest.raises(ValueError, match='incomplete'):
        store.publish('animals', new)
    assert store.snapshot('animals')['id'] == sid


def test_no_keyword_match_and_high_vector_threshold_are_insufficient(store, published):
    c, sid = published
    assistant = DocumentAssistant(store, c)
    token = secrets.token_urlsafe(32)
    session = store.new_session(sid, token)
    answer = assistant.ask('animals', 'zxqvneverfound', token=token, session_id=session)
    assert answer['status'] == 'insufficient'
    assert not answer['citations']
    snapshot = store.snapshot('animals', sid)
    c['search']['minimum_vector_similarity'] = .99
    assert store.search(snapshot, 'question', 'passage', 'vector', c['search'], vector=[0, 0, 1]) == []


def test_auxiliary_resolution_rejects_shared_evidence_outside_original_chunks(store, tmp_path):
    c = config(tmp_path)
    records, evidence, identity = projection()
    target = next(r for r in records if r['text'].startswith('Dogs must'))
    misleading = copy.deepcopy(target)
    misleading.update(id='structural:misleading', text='Canine recovery proposition')
    misleading['metadata']['original']['original_chunk_ids'] = ['unrelated-chunk']
    records.append(misleading)
    sid = store.import_snapshot('animals', identity, records, evidence, profile(c['embedding']), 'english')['snapshot_id']
    store.publish('animals', sid)
    result = DocumentAssistant(store, c).retrieve('animals', 'Canine recovery proposition', mode='claim')
    assert result['passage_ids'] == [target['id']]
