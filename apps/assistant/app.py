"""Unified chat over accepted reasoning cases, document evidence and general knowledge."""
from pathlib import Path
import sys

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]

from knowledge_foundry.assistant.config import load_config
from knowledge_foundry.assistant.service import Assistant


EXAMPLES = (
    ('Understand a term', ':material/menu_book:', 'What is a COSI?'),
    ('See a deduction', ':material/check:',
     'Selon la politique de démonstration, un client est enregistré comme PPE. '
     'Une approbation sénior est-elle requise ? Montre la déduction.'),
    ('Explore an idea', ':material/auto_awesome:', 'Explain the difference between a fact and an inference.'),
)


def select_example(question):
    st.session_state.assistant_pending_question = question


def render_welcome():
    with st.container(key='welcome'):
        with st.container(key='welcome-intro'):
            st.header('What would you like to understand?')
            st.markdown('Ask a question. Explore the answer and its evidence.')
        with st.container(horizontal=True, key='starters', gap='medium'):
            for index, (label, icon, question) in enumerate(EXAMPLES):
                st.button(label, icon=icon, help=question, key=f'example-{index}', width='stretch',
                          on_click=select_example, args=(question,))


def new_conversation():
    st.session_state.assistant_messages = []
    st.session_state.pop('assistant_pending_question', None)


def render_answer(answer):
    route, status = answer['route'], answer['status']
    labels = {'reasoning': 'Engine-checked proof', 'documents': 'Document sources', 'general': 'General knowledge',
              'clarify': 'Clarification', 'error': 'Request error'}
    statuses = {'entailed': 'Supported', 'unknown': 'Insufficient information', 'conflict': 'Conflicting information'}
    # Conversation/model text is literal: it cannot embed remote images or links.
    st.text(answer['text'])
    st.caption(labels.get(route, route) + (' · ' + statuses[status] if status in statuses else ''))
    if route == 'general':
        st.caption('General LLM answer · no document evidence or engine-checked proof.')
    if route == 'reasoning':
        context = answer['context']
        st.caption('Fictional demonstration policy · synthetic scenario facts.' if context['context_kind'] == 'synthetic'
                   else 'Source-backed rules · inspect applicability and synthetic customer assumptions.')
        if status == 'conflict':
            st.warning('Conflicting information · no supported conclusion can be relied on. Open the deduction to inspect the conflict.')
        elif status == 'unknown':
            st.info('Insufficient information · open the deduction to see which facts are missing.')
        with st.expander('Show deduction', icon=':material/account_tree:'):
            generated = any('generated_text' in step for step in answer['proof_steps'])
            st.caption('Proof checked by code · LLM-written explanation.' if generated
                       else 'Proof checked by code · source text and template explanation.')
            if status == 'conflict':
                st.info('These steps show candidate support. Conflicting premises and dependent conclusions are withheld.')
            if not answer['proof_steps']:
                st.write('No supported deduction steps are available for this question.')
            for index, step in enumerate(answer['proof_steps'], 1):
                with st.container(border=True):
                    st.markdown(f'**{index}. ' + ('Supplied fact' if step['kind'] == 'assertion' else 'Policy application') + '**')
                    st.text(step.get('generated_text', step['text']))
                    st.caption('Sources: ' + ', '.join(step['source_ids']))
            for finding in answer['diagnostics']:
                st.text(('Conflicted premise: ' if finding['kind'] == 'conflicted-premise' else 'Needed: ') + finding['text'])
        with st.expander('View sources', icon=':material/menu_book:'):
            st.caption(context['case_id'] + ' · dataset ' + context['dataset_version'] + ' · ' + context['language'])
            for source in answer['sources']:
                st.caption(source['id'])
                st.text(source['text'])
            st.text(context['scope']['text'])
        with st.expander('Question interpretation', icon=':material/chat_bubble_outline:'):
            st.text(answer['interpretation'])
            st.caption('LLM interpretation · check it matches your question.')
            for fact in context['scenario_facts']:
                st.text('• ' + fact['text'])
            st.caption('Facts copied from the selected dataset scenario.')
    if route == 'documents':
        with st.expander('View sources', icon=':material/menu_book:'):
            for citation in answer.get('citations', []):
                st.text(citation['label'] + ' · ' + citation['source']['title'])
                st.caption('Edition: ' + citation['source']['version'])
                st.text(citation['text'])
                location = citation['location']
                parts = [f'Page {location["page"]}'] if location.get('page') else []
                parts += [str(location[k]) for k in ('label', 'article_id', 'clause') if location.get(k)]
                if parts:
                    st.caption(' · '.join(parts))
            if answer.get('documents'):
                st.dataframe(answer['documents'], hide_index=True)
        st.caption('Document synthesis is separate from engine-checked reasoning.')
    for warning in answer.get('warnings', []):
        st.warning(warning)


def main():
    st.set_page_config(page_title='Knowledge Foundry · Assistant', page_icon=':material/forum:',
                       layout='wide', initial_sidebar_state='collapsed')
    st.set_option('client.toolbarMode', 'minimal')
    st.html('<style>' + Path(__file__).with_name('style.css').read_text() + '</style>')
    with st.container(key='app-header', horizontal=True, vertical_alignment='center'):
        st.title('Knowledge Foundry Assistant')
        st.button('New conversation', icon=':material/add:', key='new-conversation', on_click=new_conversation)
    if 'assistant_service' not in st.session_state:
        try:
            st.session_state.assistant_service = Assistant(load_config())
        except Exception:
            st.error('The assistant configuration or accepted reasoning dataset could not be loaded. Check the runbook.')
            st.stop()
    service = st.session_state.assistant_service
    st.session_state.setdefault('assistant_messages', [])
    conversation = st.container(key='conversation')
    with conversation:
        for message in st.session_state.assistant_messages:
            avatar = ':material/auto_awesome:' if message['role'] == 'assistant' else None
            with st.chat_message(message['role'], avatar=avatar):
                if message['role'] == 'assistant':
                    render_answer(message['answer'])
                else:
                    st.text(message['text'])
    welcome = st.empty()
    if not st.session_state.assistant_messages:
        with welcome.container():
            render_welcome()
    question = st.chat_input('Ask a question in English or French', max_chars=service.config['max_question_characters'])
    question = question or st.session_state.pop('assistant_pending_question', None)
    if question:
        welcome.empty()
        with conversation:
            with st.chat_message('user'):
                st.text(question)
            with st.chat_message('assistant', avatar=':material/auto_awesome:'):
                with st.spinner('Finding the right context and checking the answer…'):
                    answer = service.ask(question, st.session_state.assistant_messages)
                render_answer(answer)
        st.session_state.assistant_messages.extend([
            {'role': 'user', 'text': question},
            {'role': 'assistant', 'text': answer['text'], 'route': answer['route'],
             'candidate_id': answer.get('candidate_id'), 'answer': answer}])
        st.rerun()


if __name__ == '__main__':
    main()
