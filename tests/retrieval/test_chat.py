"""Rendered Streamlit interactions backed by the isolated real database."""
import json

from streamlit.testing.v1 import AppTest

from knowledge_foundry.retrieval.config import ROOT


def test_chat_history_reset_feedback_and_source_panel(store, published, tmp_path, monkeypatch):
    config, _ = published
    path = tmp_path / 'retrieval.json'
    path.write_text(json.dumps(config))
    monkeypatch.setenv('KF_DATABASE_URL', store.dsn)
    monkeypatch.setenv('KF_RETRIEVAL_CONFIG', str(path))
    app = AppTest.from_file(str(ROOT / 'apps/chat/app.py'), default_timeout=20).run()
    assert not app.exception
    assert app.title[0].value == 'Document assistant'
    assert app.sidebar.selectbox[0].value == 'animals'
    app.chat_input[0].set_value('exercise').run()
    assert not app.exception
    assert len(app.chat_message) == 2
    assert any(e.label == 'Sources and exact passages' for e in app.expander)
    assert any('Dogs must rest' in c.value for c in app.code)
    assert any('Edition:' in c.value for c in app.caption)
    helpful = next(b for b in app.button if b.label == 'Helpful')
    helpful.click().run()
    assert not app.exception
    assert any(s.value == 'Feedback saved' for s in app.success)
    previous = next(s for s in app.sidebar.selectbox if s.label == 'Conversation history').value
    next(b for b in app.sidebar.button if b.label == 'New conversation / reset').click().run()
    assert not app.exception
    assert len(app.chat_message) == 0
    history = next(s for s in app.sidebar.selectbox if s.label == 'Conversation history')
    assert len(history.options) == 2
    history.set_value(previous).run()
    assert not app.exception
    assert len(app.chat_message) == 2
    app.chat_input[0].set_value('What about exceptions?').run()
    assert not app.exception
    assert len(app.chat_message) == 4
