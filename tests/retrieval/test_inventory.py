import json
import secrets

import pytest

from knowledge_foundry.retrieval.service import DocumentAssistant, inventory


@pytest.mark.parametrize('question', ['Quels sont les documents dans ce corpurs ?',
                                     'List this corpus sources', 'Welche Dokumente enthält der Korpus?'])
def test_inventory_uses_published_metadata_not_search_or_generated_facts(store, published, question, monkeypatch):
    config, sid = published
    snapshot = store.snapshot('animals', sid)
    def forbidden(*args, **kwargs):
        raise AssertionError('inventory must not search or generate an answer')
    monkeypatch.setattr(store, 'search', forbidden)
    def route(payload, config):
        yield {'type': 'delta', 'text': json.dumps({'query': question, 'uses_history': False, 'intent': 'document_inventory'})}
    assistant = DocumentAssistant(store, config, query_stream=route, answer_stream=forbidden)
    token = secrets.token_urlsafe(32)
    session = store.new_session(sid, token)
    answer = assistant.ask('animals', question, token=token, session_id=session)
    assert answer['status'] == 'inventory' and answer['citations'] == []
    assert answer['documents'] == inventory(snapshot)
    assert len(answer['documents']) == len(snapshot['identity']['documents'])
    assert store.history(session, token, sid)[1]['payload']['documents'] == answer['documents']
    assert assistant.ask('animals', question, token=token, session_id=session, intent='document_inventory',
                         source_ids=['absent'])['documents'] == []
    with pytest.raises(ValueError):
        assistant.ask('animals', question, token=secrets.token_urlsafe(32), session_id=session, intent='document_inventory')


def test_inventory_filters_by_source_edition_and_document_without_fallback():
    snapshot = {'identity': {'sources': [{'id': 's', 'version': v, 'title': 'Title ' + v, 'uri': 'https://example.org'}
                                        for v in ['old', 'new']],
                             'documents': [{'id': v, 'source_id': 's', 'source_version': v} for v in ['old', 'new']]}}
    assert len(inventory(snapshot)) == 2
    assert inventory(snapshot, document_ids=['new'])[0]['edition'] == 'new'
    assert inventory(snapshot, source_ids=['missing']) == []
