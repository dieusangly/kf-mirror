"""Rendered Streamlit flows with deterministic model responses and no database."""
import pytest
from streamlit.testing.v1 import AppTest

from apps.assistant.app import EXAMPLES

from knowledge_foundry.assistant.config import ROOT
from knowledge_foundry.assistant.service import DocumentEvidence
from knowledge_foundry.assistant.llm import LanguageModels
from knowledge_foundry.assistant.search import ScenarioSearch
from test_reasoning_assistant import CASES, CoveredDocuments, FixtureEmbeddings, FixtureModels, decision


@pytest.fixture
def ui_models(monkeypatch):
    original = ScenarioSearch.__init__
    def search_init(self, records, config, **kwargs):
        embeddings = FixtureEmbeddings(records)
        embeddings.query_vectors[EXAMPLES[1][2]] = embeddings.vectors['PEP-OH-01']
        original(self, records, config, embedder=embeddings)

    monkeypatch.setattr(ScenarioSearch, '__init__', search_init)
    models = FixtureModels(decision(CASES[0]))
    monkeypatch.setattr(LanguageModels, '_request', lambda self, payload, step: models.respond(payload, step))
    monkeypatch.setattr(DocumentEvidence, 'preview', lambda self, question: {'available': False, 'passages': []})
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('KF_ASSISTANT_CONFIG', raising=False)
    return models


def app():
    return AppTest.from_file(str(ROOT / 'apps/assistant/app.py'), default_timeout=30).run()


def test_initial_screen_has_one_chat_input_and_no_mandatory_selectors(ui_models):
    view = app()
    assert not view.exception
    assert view.title[0].value == 'Knowledge Foundry Assistant'
    assert len(view.chat_input) == 1
    assert not view.selectbox
    assert not ui_models.requests
    assert not view.sidebar.button
    assert not view.sidebar.expander
    assert sum(button.label == 'New conversation' for button in view.main.button) == 1
    assert not any(element.label in {'About this assistant', 'Technical details'} for element in view.expander)
    assert any(element.value == 'What would you like to understand?' for element in view.header)
    assert {label for label, _, _ in EXAMPLES} <= {button.label for button in view.button}


def test_reasoning_proof_sources_details_and_reset(ui_models):
    view = app()
    view.chat_input[0].set_value(CASES[0]['question']).run()
    assert not view.exception
    assert len(view.chat_message) == 2
    assert any('Under the example policy: entailed' in element.value for element in view.text)
    assert any('Engine-checked proof · Supported' in element.value for element in view.caption)
    assert any('synthetic scenario facts' in element.value for element in view.caption)
    assert {'Question interpretation', 'Show deduction', 'View sources'} == {
        element.label for element in view.expander}
    assert any('identity document is verified' in element.value for element in view.text)
    assert all(not element.proto.expanded for element in view.expander if element.label in {
        'Question interpretation', 'Show deduction', 'View sources'})
    request_count = len(ui_models.requests)
    view.session_state.assistant_pending_question = EXAMPLES[1][2]
    next(button for button in view.main.button if button.label == 'New conversation').click().run()
    assert not view.exception
    assert len(view.chat_message) == 0
    assert len(ui_models.requests) == request_count
    assert 'assistant_pending_question' not in view.session_state
    assert any(element.value == 'What would you like to understand?' for element in view.header)


@pytest.mark.parametrize('index,case_index', [(0, 12), (1, 5), (2, 8)])
def test_examples_submit_the_real_question_through_the_assistant(ui_models, index, case_index):
    import json

    ui_models.value = decision(CASES[case_index])
    view = app()
    next(button for button in view.button if button.label == EXAMPLES[index][0]).click().run()
    assert not view.exception
    assert len(view.chat_message) == 2
    assert json.loads(ui_models.requests[0]['input'])['question'] == EXAMPLES[index][2]
    assert not view.header  # Welcome content disappears once the conversation starts.


@pytest.mark.parametrize('cid,predicate,args,status,notice', [
    ('KYC-MP-01', 'onboarding_complete', ['A'], 'unknown', 'Insufficient information'),
    ('TFS-CF-01', 'sanctions_match_entry', ['A', 'E'], 'conflict', 'Conflicting information'),
])
def test_unknown_and_conflict_remain_visible_with_collapsed_proofs(ui_models, cid, predicate, args, status, notice):
    ui_models.value = decision(candidate_id=f'synthetic-v1/{cid}/en', predicate=predicate, args=args)
    view = app()
    view.chat_input[0].set_value(f'Under the example policy for {cid}, assess {predicate}.').run()
    assert not view.exception
    assert any(notice in element.value for element in view.caption)
    alerts = view.warning if status == 'conflict' else view.info
    assert any(notice in element.value for element in alerts)
    assert not next(element for element in view.expander if element.label == 'Show deduction').proto.expanded


def test_clarification_is_rendered_and_followup_can_reach_reasoning(ui_models):
    ui_models.value = decision(route='clarify')
    view = app()
    view.chat_input[0].set_value('Can my customer open an account?').run()
    assert not view.exception
    assert any('Which policy and customer facts' in element.value for element in view.text)
    ui_models.value = decision(CASES[0])
    view.chat_input[0].set_value(CASES[0]['question']).run()
    assert not view.exception
    assert len(view.chat_message) == 4
    assert any('Show deduction' == element.label for element in view.expander)


@pytest.mark.parametrize('route,expected', [('general', 'General knowledge'), ('documents', 'Document sources')])
def test_other_routes_and_unavailable_documents(ui_models, route, expected):
    ui_models.value = decision(route=route)
    view = app()
    view.chat_input[0].set_value('Explain this question.').run()
    assert not view.exception
    assert any(expected in element.value for element in view.caption)
    if route == 'documents':
        assert any('unavailable' in element.value for element in view.text)
        assert len(ui_models.requests) == 1


def test_malformed_model_output_renders_error_without_traceback(ui_models, monkeypatch):
    monkeypatch.setattr(LanguageModels, '_request', lambda *args: {'status': 'incomplete'})
    view = app()
    view.chat_input[0].set_value('Please answer.').run()
    assert not view.exception
    assert any('Request error' in element.value for element in view.caption)
    assert any('No fallback answer' in element.value for element in view.warning)


def test_conversation_text_cannot_embed_remote_markdown_images(ui_models):
    ui_models.value = decision(route='clarify')
    ui_models.value['clarification'] = '![remote](https://external.example/private)'
    view = app()
    view.chat_input[0].set_value('![user-image](https://external.example/user)').run()
    assert not view.exception
    assert any('![remote]' in element.value for element in view.text)
    assert any('![user-image]' in element.value for element in view.text)
    assert all('external.example' not in element.value for element in view.markdown)


@pytest.mark.parametrize('question,language', [('What is a COSI?', 'en'), ('Qu’est-ce qu’une COSI ?', 'fr')])
def test_cosi_corpus_answer_renders_even_when_router_proposes_general(ui_models, monkeypatch, question, language):
    documents = CoveredDocuments()
    ui_models.value = decision(route='general', language=language)
    monkeypatch.setattr(DocumentEvidence, 'preview', lambda self, text: documents.preview(text))
    monkeypatch.setattr(DocumentEvidence, 'answer', lambda self, *args: documents.answer(*args))
    view = app()
    view.chat_input[0].set_value(question).run()
    assert not view.exception
    assert any(element.value == 'Document sources' for element in view.caption)
    assert any("COSI : Communication systématique d'informations." in element.value for element in view.text)
    assert any(element.label == 'View sources' for element in view.expander)
    assert not any('General LLM answer' in element.value for element in view.caption)
    assert len(ui_models.requests) == 1
    assert documents.calls[0][2] == 'fixture-snapshot'
