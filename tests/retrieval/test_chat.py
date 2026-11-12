"""Unified Streamlit UI on synthetic animal data and isolated real PostgreSQL."""
import json

import pytest
from streamlit.testing.v1 import AppTest

from knowledge_foundry.assistant.bindings import KnowledgeBinding
from knowledge_foundry.assistant.service import Assistant
from knowledge_foundry.retrieval.config import ROOT
from rag_fixtures import assistant_poc_binding, json_stream


@pytest.fixture
def ui(store, published, tmp_path, monkeypatch):
    cfg, sid = published
    binding = assistant_poc_binding(store, sid)
    path = tmp_path / 'assistant-poc-config.json'; path.write_text(json.dumps(cfg))
    monkeypatch.setenv('KF_DATABASE_URL', store.dsn)
    monkeypatch.setenv('KF_RETRIEVAL_CONFIG', str(path))
    monkeypatch.setenv('KF_ASSISTANT_POC_ENABLED', '1')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setattr(KnowledgeBinding, 'load', classmethod(lambda cls, path=None: binding))
    def forbidden(*args, **kwargs): pytest.fail('offline UI must not call providers')
    monkeypatch.setattr('knowledge_foundry.assistant.service.openai_stream', forbidden)
    app = AppTest.from_file(str(ROOT / 'apps/chat/app.py'), default_timeout=30).run()
    return app, binding


def test_chat_history_creation_switching_and_source_panel(store, ui):
    app, binding = ui
    assert not app.exception and app.title[0].value == 'Assistant'
    assert not any(s.label in {'Assistant', 'Answer mode', 'Search', 'Retrieve via'} for s in app.selectbox)
    app.chat_input[0].set_value('exercise').run()
    assert not app.exception and len(app.chat_message) == 2
    assert any('Dogs must rest' in c.value for c in app.code)
    assert any(e.label == 'Diagnostics' and not e.proto.expanded for e in app.expander)
    selector = next(s for s in app.selectbox if s.label == 'Conversations')
    previous = selector.value
    next(b for b in app.button if b.label == 'New conversation').click().run()
    assert not app.exception and not app.chat_message
    next(s for s in app.selectbox if s.label == 'Conversations').select(previous).run()
    assert not app.exception and len(app.chat_message) == 2
    app.chat_input[0].set_value('What about exceptions?').run()
    assert not app.exception and len(app.chat_message) == 4
    assert len(store.assistant_history(previous, app.query_params['chat_token'], binding.value)) == 4


def test_inventory_button_uses_metadata_without_models(ui):
    app, _ = ui
    next(b for b in app.button if b.label == 'Documents in corpus').click().run()
    assert not app.exception and len(app.dataframe) == 1
    assert any('Documents (1)' in m.value for m in app.markdown)


def test_transient_empty_selector_preserves_owned_url_conversation(ui, store, monkeypatch):
    from streamlit.delta_generator import DeltaGenerator
    app, binding = ui
    next(b for b in app.button if b.label == 'New conversation').click().run()
    app.chat_input[0].set_value('exercise').run()
    cid = app.query_params['conversation_id']
    token = app.query_params['chat_token']
    original = DeltaGenerator.selectbox

    def empty_conversation_selection(self, label, *args, **kwargs):
        value = original(self, label, *args, **kwargs)
        # Browser widget state can briefly clear as an empty conversation gains
        # its first question/title. Its owned URL remains the context binding.
        return None if label == 'Conversations' else value

    monkeypatch.setattr(DeltaGenerator, 'selectbox', empty_conversation_selection)
    app.chat_input[0].set_value('What about exceptions?').run()
    assert not app.exception
    assert app.query_params['conversation_id'] == cid
    assert len(store.assistant_conversations(token)) == 1
    assert len(store.assistant_history(cid, token, binding.value)) == 4


def test_question_from_unavailable_url_creates_one_separate_history(ui, store):
    from uuid import uuid4
    app, binding = ui
    app.chat_input[0].set_value('exercise').run()
    prior = app.query_params['conversation_id']
    token = app.query_params['chat_token']
    app.query_params['conversation_id'] = str(uuid4())
    app.run()
    app.chat_input[0].set_value('cats').run()
    assert not app.exception
    current = app.query_params['conversation_id']
    assert current != prior
    assert len(store.assistant_conversations(token)) == 2
    assert len(store.assistant_history(prior, token, binding.value)) == 2
    assert len(store.assistant_history(current, token, binding.value)) == 2


def test_reopen_older_conversation_without_duplicate_default_warning(ui, monkeypatch, caplog):
    from streamlit.elements.lib import policies
    app, _ = ui
    app.chat_input[0].set_value('exercise').run()
    prior = app.query_params['conversation_id']
    next(b for b in app.button if b.label == 'New conversation').click().run()
    app.query_params['conversation_id'] = prior
    monkeypatch.setattr(policies, '_shown_default_value_warning', False)
    caplog.clear()
    app.run()
    assert not app.exception and len(app.chat_message) == 2
    assert next(s for s in app.selectbox if s.label == 'Conversations').value == prior
    assert not any('created with a default value' in r.message for r in caplog.records)


def test_generated_answer_uses_validated_clickable_citations(ui, monkeypatch):
    app, _ = ui
    def init(self, store, config, binding, **kwargs):
        config['answer']['provider'] = 'openai'
        def answer(payload, cfg):
            data = json.loads(payload['input'])
            row = next(e for e in data['evidence'] if e['text'].startswith('Dogs must'))
            yield from json_stream_text('Dogs must rest after exercise, with the stated exception [' + row['label'] + '].')
        original(self, store, config, binding, route_stream=json_stream({'language':'en','parts':[
            {'question':'exercise','retrieval_query':'exercise','capability':'documents'}]}), audit_stream=audit, document_stream=answer, **kwargs)
    def json_stream_text(text):
        yield {'type':'delta','text':text}
        yield {'type':'usage','usage':{'input_tokens':200,'output_tokens':40}}
    def audit(payload, config):
        yield from json_stream(dict.fromkeys(payload['text']['format']['schema']['properties'], True))(payload, config)
    original = Assistant.__init__
    monkeypatch.setattr(Assistant, '__init__', init)
    app.chat_input[0].set_value('exercise').run()
    assert not app.exception
    assert any('https://' in m.value and 'Dogs must rest' in m.value for m in app.markdown)
    assert any('Dogs must rest' in c.value for c in app.code)


def test_excerpts_keep_original_bytes_with_final_citation_labels(ui, monkeypatch):
    import copy
    import knowledge_foundry.assistant.service as service
    app, _ = ui
    generate = service.generate

    def nonsequential_retrieval_labels(config, payload, evidence, stream):
        evidence = copy.deepcopy(evidence)
        for index, row in enumerate(evidence, 20):
            row['label'] = 'E' + str(index)
        yield from generate(config, payload, evidence, stream)

    monkeypatch.setattr(service, 'generate', nonsequential_retrieval_labels)
    app.chat_input[0].set_value('exercise').run()
    assert not app.exception
    assert any(t.value == 'Dogs must rest after exercise. [1]' for t in app.text)
    assert not any('[E20]' in t.value for t in app.text)
    assert any('[E1](https://' in m.value for m in app.markdown)


def test_error_is_safe_and_no_automatic_retry(ui, monkeypatch):
    app, _ = ui
    calls = []
    def failed(*args, **kwargs):
        calls.append(True)
        raise RuntimeError('private provider payload must not be displayed')
    monkeypatch.setattr(Assistant, 'ask', failed)
    app.chat_input[0].set_value('exercise').run()
    assert not app.exception and len(calls) == 1
    assert any('No automatic retry' in e.value for e in app.error)
    assert not any('private provider payload' in e.value for e in app.error)


def test_filters_are_visible_for_all_questions_and_conversation_local(ui, store):
    app, binding = ui
    source = next(m for m in app.multiselect if m.label == 'Source filter')
    source_id = binding.snapshots(store)[0]['identity']['sources'][0]['id']
    source.select(source_id).run()
    app.chat_input[0].set_value('exercise').run()
    assert not app.exception
    assert next(m for m in app.multiselect if m.label == 'Source filter').value == [source_id]
    next(b for b in app.button if b.label == 'New conversation').click().run()
    assert not next(m for m in app.multiselect if m.label == 'Source filter').value
    assert any(m.label == 'Document filter' for m in app.multiselect)


def test_unavailable_conversation_url_does_not_open_another_history(ui, store):
    from uuid import uuid4
    app, binding = ui
    token = app.query_params['chat_token']
    cid = store.new_assistant_conversation(token, binding.value)
    store.save_assistant_turn(cid, token, binding.value, str(uuid4()), 'Saved POC question',
        {'text':'Saved POC answer','status':'withheld','bindings':binding.value,
         'filters':{'source_ids':None,'document_ids':None},'intent':None,'citations':[]})
    fresh = AppTest.from_file(str(ROOT / 'apps/chat/app.py'), default_timeout=30)
    fresh.query_params['chat_token'] = token
    fresh.query_params['conversation_id'] = str(uuid4())
    fresh.run()
    assert not fresh.exception and not fresh.chat_message
    assert any('requested conversation is unavailable' in w.value for w in fresh.warning)
    selection = next(s for s in fresh.selectbox if s.label == 'Conversations')
    assert selection.value is None
    selection.select(cid).run()
    assert not fresh.exception and len(fresh.chat_message) == 2
    fresh.query_params['conversation_id'] = str(uuid4())
    fresh.run()
    assert not fresh.exception and not fresh.chat_message
