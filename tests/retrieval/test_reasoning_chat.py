"""Rendered Streamlit interactions using real reviewed pilot spans and isolated PostgreSQL."""
import json
import os

import pytest
from streamlit.testing.v1 import AppTest

from knowledge_foundry.reasoning.catalogue import accepted_projection, load_dataset
from knowledge_foundry.retrieval.config import ROOT
from rag_fixtures import config
from rag_fixtures import suggestion_interpreter, excerpt_explanation, faithful_audit


@pytest.fixture
def pilot_app(store, tmp_path, monkeypatch):
    dataset = load_dataset()
    records, evidence, identity = accepted_projection(dataset)
    sid = store.import_snapshot('pilot', identity, records, evidence, None)['snapshot_id']
    store.publish('pilot', sid)  # Test schema only.
    c = config(tmp_path)
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(c))
    monkeypatch.setenv('KF_DATABASE_URL', store.dsn)
    monkeypatch.setenv('KF_RETRIEVAL_CONFIG', str(path))
    monkeypatch.setenv('KF_REASONING_OUTPUT', str(tmp_path / 'proofs'))
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    from knowledge_foundry.reasoning.assistant import ReasoningAssistant
    original = ReasoningAssistant.__init__
    def mocked_init(self, *args, **kwargs):
        original(self, *args, **kwargs, interpretation_stream=suggestion_interpreter,
                 answer_stream=excerpt_explanation, audit_stream=faithful_audit)
    monkeypatch.setattr(ReasoningAssistant, '__init__', mocked_init)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('rendered catalogue tests must not call paid APIs')
    monkeypatch.setattr('knowledge_foundry.embeddings.EmbeddingAdapter._request', forbidden)
    monkeypatch.setattr('knowledge_foundry.retrieval.answering.openai_stream', forbidden)
    monkeypatch.setattr('knowledge_foundry.retrieval.query.openai_stream', forbidden)
    yield dataset, sid
    assert not calls


def selector(app, label):
    return next(s for s in app.sidebar.selectbox if s.label == label)


def test_basel_invalid_interpretation_and_retry_keep_history(store, pilot_app, monkeypatch):
    from knowledge_foundry.reasoning.catalogue import resolve_checker
    from rag_fixtures import plan_for
    checker = resolve_checker()
    if not os.access(checker, os.X_OK):
        pytest.skip('install the pinned checker or set OO_HORN')
    monkeypatch.setenv('OO_HORN', checker)
    dataset, sid = pilot_app
    question = dataset['catalogue']['questions'][1]['question']
    plan = plan_for(dataset['mapping']['mappings'][1])
    plan['as_of'] = '2026-10-28 (captured snapshot; does not establish current applicability)'
    app = AppTest.from_file(str(ROOT / 'apps/chat/app.py'), default_timeout=30).run()
    selector(app, 'Assistant').select('Reasoning assistant').run()
    # Replay a bad plan returned by the interpreter; application validation must
    # remain strict even if provider-side schema enforcement is bypassed.
    with monkeypatch.context() as bad_interpreter:
        bad_interpreter.setattr('knowledge_foundry.reasoning.assistant.interpret', lambda *args: plan)
        next(b for b in app.button if b.label == question).click().run()
    assert not app.exception
    assert any('valid interpretation' in m.value and 'retry' in m.value for m in app.markdown)
    session = selector(app, 'Conversation history').value
    token = app.query_params['chat_token']
    failed = store.history(session, token, sid, 'reasoning')[-1]['payload']
    assert failed['status'] == 'invalid_plan' and not failed['citations']
    assert failed['retrieval']['query_plan']['as_of'] == plan['as_of']
    assert failed['retrieval']['execution'] is None
    # A new request uses the valid mocked interpretation and the real checker;
    # the saved failure remains an unchanged, separate historical turn.
    next(b for b in app.button if b.label == question).click().run()
    assert not app.exception
    history = store.history(session, token, sid, 'reasoning')
    assert len(history) == 4 and history[1]['payload'] == failed
    assert history[-1]['payload']['status'] == 'reasoned'
    assert history[-1]['payload']['retrieval']['reasoning']['certificate']['status'] == 'accepted'
    selector(app, 'Assistant').select('Document assistant').run()
    app.chat_input[0].set_value('COSI Tracfin').run()
    assert not app.exception and app.code


def test_mode_histories_survive_switch_reset_and_reload(store, pilot_app, monkeypatch):
    from open_ontologies_lite import find_checker
    try:
        monkeypatch.setenv('OO_HORN', str(find_checker()))
    except RuntimeError:
        pytest.skip('build pinned checker and set OO_HORN')
    dataset, sid = pilot_app
    app = AppTest.from_file(str(ROOT / 'apps/chat/app.py'), default_timeout=30).run()
    assert not app.exception
    assert selector(app, 'Assistant').value == 'Document assistant'
    app.chat_input[0].set_value('COSI Tracfin').run()
    assert not app.exception and len(app.chat_message) == 2
    document_session = selector(app, 'Conversation history').value
    token = app.query_params['chat_token']
    selector(app, 'Assistant').select('Reasoning assistant').run()
    assert not app.exception and len(app.chat_message) == 0
    assert app.title[0].value == 'Reasoning assistant'
    assert len([b for b in app.button if b.key and b.key.startswith('reasoning-')]) == 3
    for index in [0, 2]:  # English question / French source; French case question.
        next(b for b in app.button if b.label == dataset['catalogue']['questions'][index]['question']).click().run()
        assert not app.exception
        assert any(('Technical details' == e.label or 'Détails techniques' == e.label) and not e.proto.expanded for e in app.expander)
        assert any('accepted' in str(t.value) for t in app.json)
    reasoning_session = selector(app, 'Conversation history').value
    assert reasoning_session != document_session and len(app.chat_message) == 4
    assert any('dernière déclaration' in c.value for c in app.code)
    selector(app, 'Assistant').select('Document assistant').run()
    assert not app.exception and len(app.chat_message) == 2
    assert selector(app, 'Conversation history').value == document_session
    selector(app, 'Assistant').select('Reasoning assistant').run()
    assert len(app.chat_message) == 4
    fresh = AppTest.from_file(str(ROOT / 'apps/chat/app.py'), default_timeout=30)
    fresh.query_params['chat_token'] = token
    fresh.run()
    assert not fresh.exception and len(fresh.chat_message) == 2
    selector(fresh, 'Assistant').select('Reasoning assistant').run()
    assert not fresh.exception and len(fresh.chat_message) == 4
    next(b for b in fresh.sidebar.button if b.label == 'New conversation / reset').click().run()
    assert not fresh.exception and len(fresh.chat_message) == 0
    assert len(store.sessions(sid, token, 'reasoning')) == 2
    assert len(store.history(document_session, token, sid, 'document')) == 2
    fresh.sidebar.multiselect[0].select('src:bcbs-aml-cft-risk-management-2020').run()
    assert not fresh.exception
    assert selector(fresh, 'Assistant').value == 'Reasoning assistant'
    next(b for b in fresh.button if b.label == dataset['catalogue']['questions'][0]['question']).click().run()
    assert not fresh.exception
    assert any('cannot establish' in t.value for t in fresh.markdown)
    assert not fresh.code


def test_missing_checker_and_unsupported_question_never_fall_back(store, pilot_app, monkeypatch):
    dataset, _ = pilot_app
    monkeypatch.setenv('OO_HORN', '/missing/oo-horn')
    app = AppTest.from_file(str(ROOT / 'apps/chat/app.py'), default_timeout=30).run()
    selector(app, 'Assistant').select('Reasoning assistant').run()
    question = dataset['catalogue']['questions'][0]['question']
    next(b for b in app.button if b.label == question).click().run()
    assert not app.exception
    assert any('unchecked' in str(t.value) for t in app.json)
    assert not app.code
    app.chat_input[0].set_value('Invent a legal decision').run()
    assert not app.exception and len(app.chat_message) == 4
    assert any('cannot establish' in t.value for t in app.markdown)
    selector(app, 'Assistant').select('Document assistant').run()
    app.chat_input[0].set_value('COSI Tracfin').run()
    assert not app.exception and any('COSI' in c.value for c in app.code)


def test_missing_dataset_does_not_break_document_mode(store, pilot_app, monkeypatch):
    monkeypatch.setenv('KF_REASONING_DATASET', '/missing/dataset')
    app = AppTest.from_file(str(ROOT / 'apps/chat/app.py'), default_timeout=30).run()
    assert not app.exception
    selector(app, 'Assistant').select('Reasoning assistant').run()
    assert not app.exception
    assert any('dataset is unavailable' in w.value for w in app.warning)
    app.chat_input[0].set_value('COSI').run()
    assert not app.exception
    assert any('unavailable' in t.value for t in app.markdown)
    selector(app, 'Assistant').select('Document assistant').run()
    app.chat_input[0].set_value('COSI Tracfin').run()
    assert not app.exception and app.code


def test_plain_launch_answers_all_three_visible_questions(store, pilot_app, tmp_path, monkeypatch):
    """Reproduce the user's launch: no OO_HORN and a persistent project checker."""
    import shutil
    from knowledge_foundry.reasoning.catalogue import resolve_checker
    from knowledge_foundry.reasoning.engine import UPSTREAM_COMMIT
    from knowledge_foundry.retrieval.artifacts import file_hash
    binary = resolve_checker()
    if not os.access(binary, os.X_OK):
        pytest.skip('install the pinned checker or set OO_HORN')
    installed = tmp_path / 'installed/oo-horn'
    installed.parent.mkdir()
    shutil.copy2(binary, installed)
    installed.with_name('manifest.json').write_text(json.dumps({
        'upstream_commit': UPSTREAM_COMMIT, 'binary_sha256': file_hash(installed)}))
    monkeypatch.setattr('knowledge_foundry.reasoning.catalogue.DEFAULT_CHECKER', installed)
    monkeypatch.delenv('OO_HORN', raising=False)
    dataset, sid = pilot_app
    app = AppTest.from_file(str(ROOT / 'apps/chat/app.py'), default_timeout=30).run()
    selector(app, 'Assistant').select('Reasoning assistant').run()
    assert not app.exception
    assert not any('checker is unavailable' in w.value for w in app.warning)
    for entry in dataset['catalogue']['questions']:
        buttons = {b.label: b for b in app.button}
        buttons[entry['question']].click().run()
        assert not app.exception
        session = selector(app, 'Conversation history').value
        answer = store.history(session, app.query_params['chat_token'], sid, 'reasoning')[-1]['payload']
        assert answer['status'] == 'reasoned'
        assert answer['explanation_kind'] == 'evaluated_llm'
        assert not answer['text'].startswith(entry['answer'])
        assert answer['retrieval']['reasoning']['certificate']['status'] == 'accepted'
        assert answer['retrieval']['reasoning']['status'] == 'inferred'
    assert len(app.chat_message) == 6
