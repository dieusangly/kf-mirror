"""Legacy provenance remains readable in the single Assistant without adopting it."""
import copy
import json

import pytest
from streamlit.testing.v1 import AppTest

from knowledge_foundry.assistant.bindings import KnowledgeBinding
from knowledge_foundry.retrieval.config import ROOT
from rag_fixtures import assistant_poc_binding


@pytest.mark.parametrize('legacy_mode', ['document', 'reasoning'])
def test_legacy_histories_reopen_read_only_with_original_provenance(store, published, tmp_path, monkeypatch, legacy_mode):
    cfg, sid = published
    binding = assistant_poc_binding(store, sid)
    token = 'legacy-poc-private-token-' * 2
    session = store.new_session(sid, token, legacy_mode)
    old = {'status':'extractive','text':'Original retained answer','citations':[],
           'original_provenance':{'snapshot':sid,'mode':legacy_mode}}
    store.save_turn(session, token, sid, 'Original question', old, assistant_mode=legacy_mode)
    saved = copy.deepcopy(store.history(session, token, sid, legacy_mode))
    path = tmp_path / 'assistant-poc-retrieval.json'; path.write_text(json.dumps(cfg))
    monkeypatch.setenv('KF_DATABASE_URL', store.dsn)
    monkeypatch.setenv('KF_RETRIEVAL_CONFIG', str(path))
    monkeypatch.setenv('KF_ASSISTANT_POC_ENABLED','1')
    monkeypatch.setattr(KnowledgeBinding, 'load', classmethod(lambda cls, path=None: binding))
    app = AppTest.from_file(str(ROOT / 'apps/chat/app.py'), default_timeout=30)
    app.query_params['chat_token'] = token
    app.run()
    next(s for s in app.selectbox if s.label == 'Open previous history').select(session).run()
    assert not app.exception and len(app.chat_message) == 2 and not app.chat_input
    assert any('read-only' in i.value for i in app.info)
    assert store.assistant_conversations(token) == []
    assert store.history(session, token, sid, legacy_mode) == saved
    next(b for b in app.button if b.label == 'New conversation').click().run()
    assert not app.exception and app.chat_input and not app.chat_message
    assert store.history(session, token, sid, legacy_mode) == saved


def test_local_changes_do_not_enable_unapproved_application_writes(store, published, monkeypatch):
    _, sid = published
    binding = assistant_poc_binding(store, sid)
    monkeypatch.setenv('KF_DATABASE_URL', store.dsn)
    monkeypatch.delenv('KF_ASSISTANT_POC_ENABLED', raising=False)
    monkeypatch.setattr(KnowledgeBinding, 'load', classmethod(lambda cls, path=None: binding))
    def forbidden(*args, **kwargs): pytest.fail('disabled POC must not create application conversations')
    monkeypatch.setattr(store.__class__, 'new_assistant_conversation', forbidden)
    app = AppTest.from_file(str(ROOT / 'apps/chat/app.py'), default_timeout=30).run()
    assert not app.exception and app.title[0].value == 'Assistant' and not app.chat_input
    assert any('activation approval' in w.value for w in app.warning)


def test_missing_bound_snapshot_never_selects_another_corpus(store, published, monkeypatch):
    monkeypatch.setenv('KF_DATABASE_URL', store.dsn)
    # The actual POC IDs are absent from this synthetic schema.
    app = AppTest.from_file(str(ROOT / 'apps/chat/app.py'), default_timeout=30).run()
    assert not app.exception and not app.chat_input
    assert any('No alternative snapshot' in e.value for e in app.error)
