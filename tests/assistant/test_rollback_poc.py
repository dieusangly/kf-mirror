"""Provider-off rollback smoke on the restored disposable DB, never the application."""
import json
import os

import pytest
from streamlit.testing.v1 import AppTest

from knowledge_foundry.retrieval.artifacts import file_hash


def test_prior_app_reopens_original_inventory_and_retains_additive_history(monkeypatch):
    from scripts.assistant_poc import ISOLATED_DSN, OUT, isolated
    if os.environ.get('KF_ASSISTANT_POC_TEST_DATABASE_URL') != ISOLATED_DSN:
        pytest.skip('requires the physically checked isolated restore')
    store = isolated()
    path = OUT / 'poc-prior-app.py'
    with store.connection() as conn:
        before = {name:conn.execute('SELECT count(*) AS n FROM ' + name).fetchone()['n']
                  for name in ('kf_assistant_conversations','kf_assistant_messages')}
    monkeypatch.setenv('KF_DATABASE_URL', ISOLATED_DSN)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('KF_RETRIEVAL_CONFIG', raising=False)
    def forbidden(*args, **kwargs): pytest.fail('rollback smoke cannot call providers')
    monkeypatch.setattr('knowledge_foundry.retrieval.answering.openai_stream', forbidden)
    monkeypatch.setattr('knowledge_foundry.retrieval.query.openai_stream', forbidden)
    monkeypatch.setattr('knowledge_foundry.embeddings.EmbeddingAdapter._request', forbidden)
    app = AppTest.from_file(str(path), default_timeout=30).run()
    assert not app.exception
    next(b for b in app.button if b.label == 'Documents in corpus').click().run()
    assert not app.exception and len(app.dataframe[0].value) == 13
    with store.connection() as conn:
        after = {name:conn.execute('SELECT count(*) AS n FROM ' + name).fetchone()['n']
                 for name in before}
    assert before == after
    (OUT/'poc-rollback-check.json').write_text(json.dumps({
        'status':'passed','prior_app_sha256':file_hash(path),'document_inventory_count':13,
        'additive_history_retained':True,'provider_requests':0,'application_database_writes':False,
        'execution':'Prior source executed directly only against the physically checked isolated restore; no application rollback executed.'},indent=2)+'\n')
