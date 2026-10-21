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
    assert history.value != previous
    history.set_value(previous).run()
    assert not app.exception
    assert len(app.chat_message) == 2
    app.chat_input[0].set_value('What about exceptions?').run()
    assert not app.exception
    assert len(app.chat_message) == 4


def test_generated_mode_uses_resolved_citations_without_a_live_model(store, published, tmp_path, monkeypatch):
    c, _ = published
    c['answer']['provider'] = 'openai'
    path = tmp_path / 'retrieval.json'
    path.write_text(json.dumps(c))
    monkeypatch.setenv('KF_DATABASE_URL', store.dsn)
    monkeypatch.setenv('KF_RETRIEVAL_CONFIG', str(path))
    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-no-network')
    def stream(payload, config):
        evidence = json.loads(payload['input'])['evidence']
        selected = next(e for e in evidence if e['text'].startswith('Dogs must'))
        yield {'type': 'delta', 'text': 'Dogs rest after exercise [' + selected['label'] + '].'}
    monkeypatch.setattr('knowledge_foundry.retrieval.answering.openai_stream', stream)
    app = AppTest.from_file(str(ROOT / 'apps/chat/app.py'), default_timeout=20).run()
    assert next(s for s in app.sidebar.selectbox if s.label == 'Answer mode').value == 'openai'
    next(s for s in app.sidebar.selectbox if s.label == 'Answer mode').select('openai').run()
    app.chat_input[0].set_value('Dogs rest exercise').run()
    assert not app.exception
    assert any('Draft answer' in item.value for item in app.caption)
    assert any('Dogs must rest' in item.value for item in app.code)
    assert any('evidence-' in item.value and 'Dogs rest' in item.value for item in app.markdown)


def test_missing_key_falls_back_to_keyword_excerpts(store, published, tmp_path, monkeypatch):
    c, _ = published
    c['answer']['provider'] = 'openai'
    c['search']['method'] = 'vector'
    path = tmp_path / 'retrieval.json'
    path.write_text(json.dumps(c))
    monkeypatch.setenv('KF_DATABASE_URL', store.dsn)
    monkeypatch.setenv('KF_RETRIEVAL_CONFIG', str(path))
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    app = AppTest.from_file(str(ROOT / 'apps/chat/app.py'), default_timeout=20).run()
    assert not app.exception
    assert next(s for s in app.sidebar.selectbox if s.label == 'Answer mode').value == 'extractive'
    assert next(s for s in app.sidebar.selectbox if s.label == 'Search').value == 'keyword'
    assert any('OPENAI_API_KEY is not configured' in item.value for item in app.info)
    app.chat_input[0].set_value('exercise').run()
    assert not app.exception
    assert any('Dogs must rest' in item.value for item in app.code)
