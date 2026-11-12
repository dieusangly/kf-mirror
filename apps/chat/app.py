"""Unified Assistant POC; all evidence checks and persistence live in services."""
import os
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from knowledge_foundry.retrieval.config import corpora, load_config
from knowledge_foundry.retrieval.provenance import graph_path
from knowledge_foundry.assistant.bindings import KnowledgeBinding
from knowledge_foundry.assistant.service import Assistant
from knowledge_foundry.storage.postgres import PostgresStore


@st.dialog('Provenance graph', width='large')
def show_graph(path):
    st.caption('Artifact provenance · draft claims and classifications are not verified evidence.')
    st.iframe(path, height=700, alt='Knowledge provenance graph')


def render_answer(message, store, session_id, token, *, feedback=True):
    answer, mid = message['payload'], message['id']
    labels = {e['label'] for e in answer.get('citations', [])}
    # Extractive source text is displayed literally; generated Markdown links are
    # constructed only for labels already resolved by application code.
    unified = 'parts' in answer
    if unified:
        render_parts(answer, mid)
    elif answer.get('status') == 'inventory':
        st.text(message['content'])
        st.caption('Document inventory from the selected snapshot metadata, with the current source filter.')
    elif answer.get('status') == 'extractive':
        st.text(message['content'])
        st.markdown(' · '.join(f'[{label}](#evidence-{mid}-{label})' for label in sorted(labels)))
    else:
        text = re.sub(r'\[(E\d+)\]', lambda m: f'[{m[1]}](#evidence-{mid}-{m[1]})' if m[1] in labels else m[0], message['content'])
        st.markdown(text)
    reasoning = answer.get('retrieval', {}).get('reasoning')
    french = answer.get('language') == 'fr'
    if reasoning is not None:
        if answer.get('knowledge_label'):
            st.warning(answer['knowledge_label'])
        if answer.get('clarification'):
            st.text(answer['clarification'])
        if answer.get('status') == 'reasoned' and reasoning.get('status') == 'asserted':
            st.caption('Assertions de la source correspondantes ; aucune nouvelle déduction.' if french else
                       'Matching source assertions; no new deduction.')
        st.caption('Les certificats contrôlent les étapes symboliques sous les hypothèses fournies. '
                   'La vérité des sources et la rédaction du modèle ne sont pas certifiées.' if french else
                   'Certificates check symbolic steps under supplied assumptions. '
                   'Source truth and model wording are not certified.')
        if reasoning.get('jurisdiction'):
            st.caption(('Périmètre de la source : ' if french else 'Source scope: ') +
                       str(reasoning['jurisdiction']) + ' · ' + str(reasoning.get('as_of', '')) +
                       (' · Limite de l’instantané, sans constat d’applicabilité juridique actuelle.' if french else
                        ' · Snapshot boundary, without a finding of current legal applicability.'))
        if reasoning.get('conflicts'):
            st.error('Des prémisses contradictoires empêchent de conclure.' if french else
                     'Contradictory premises prevent a conclusion.')
        with st.expander('Détails techniques' if french else 'Technical details', expanded=False):
            st.caption('Le statut du certificat enregistré décrit la réponse initiale.' if french else
                       'Saved certificate status describes the original answer run.')
            st.json({'reasoning': reasoning, 'query_plan': answer.get('retrieval', {}).get('query_plan'),
                     'execution': answer.get('retrieval', {}).get('execution'),
                     'semantic_checks': answer.get('retrieval', {}).get('semantic_checks'),
                     'telemetry': answer.get('telemetry')})
    elif not unified and answer.get('status') != 'inventory':
        st.caption('Exact source excerpts · no generated synthesis' if answer.get('status') == 'extractive' else
                   'Draft answer · review the cited passages before relying on it')
    telemetry = answer.get('telemetry', {})
    if not unified:
        st.caption(f"Model: {telemetry.get('model', 'unknown')} · {telemetry.get('total_latency_seconds', '?')} s · "
                   f"Estimated answer cost: {telemetry.get('estimated_cost_usd')} USD")
    with st.expander('Documents and snapshot' if answer.get('status') == 'inventory' else
                     'Sources et extraits originaux' if french else 'Sources and exact passages', expanded=not unified):
        citations = answer.get('citations', [])
        if answer.get('status') == 'inventory':
            st.dataframe(answer['documents'], hide_index=True, width='stretch',
                         column_config={'uri': st.column_config.LinkColumn('Source')})
        elif not citations and not any(p.get('outcome') == 'inventory' for p in answer.get('parts', [])):
            st.info('No supporting citations were available or citation validation failed.')
        for e in citations:
            anchor = f'evidence-{mid}-{e["label"]}'
            st.markdown(f'<span id="{anchor}"></span>', unsafe_allow_html=True)
            st.markdown(f'**{e["label"]} · {e["source"]["title"]}**')
            location = e['location']
            parts = ([f"Page {location['page']}"] if location.get('page') else [])
            parts += [str(location[k]) for k in ('label', 'article_id', 'clause', 'anchor') if location.get(k)]
            if location.get('heading_path'):
                parts.append(' › '.join(location['heading_path']))
            if 'line_start' in location:
                parts.append(f"Lines {location['line_start']}–{location['line_end']}")
            parts += [f"{k.replace('_index', '').title()} {location[k]}" for k in
                      ('block_index', 'table_index', 'row_index', 'cell_index') if k in location]
            st.caption(f"Edition: {e['source']['version']} · " + ' · '.join(parts or [e['document']['id']]))
            st.code(e['text'], language=None, wrap_lines=True)
            if urlparse(e['source']['uri']).scheme in {'http', 'https'}:
                st.link_button('Open source', e['source']['uri'], key=f'source-{mid}-{e["label"]}')
            with st.expander(f"{e['label']} provenance"):
                st.json({'evidence_id': e['id'], 'reference': e['reference'],
                         'original_chunk_ids': e['original_chunk_ids'], 'location': e['location']})
        for warning in answer.get('retrieval', {}).get('warnings', []) + answer.get('warnings', []):
            st.warning(warning)
        if reasoning is None and not unified:
            with st.expander('Request and retrieval details'):
                st.json({'telemetry': telemetry, 'retrieval': {k: v for k, v in answer.get('retrieval', {}).items() if k != 'evidence'}})
        elif reasoning is not None:
            context = [c for c in reasoning.get('citations', []) if c.get('role') == 'governing_context']
            for citation in context:
                st.caption(citation['label'] + ' · ' + ('Contexte original' if french else 'Original governing context'))
                st.code(citation['text'], language=None, wrap_lines=True)
    if not feedback:
        return
    comment = st.text_input('Optional feedback', key=f'comment-{mid}', max_chars=2000)
    up, down = st.columns(2)
    for column, rating, label in [(up, 1, 'Helpful'), (down, -1, 'Needs work')]:
        if column.button(label, key=f'feedback-{mid}-{rating}'):
            store.feedback(session_id, token, mid, rating, comment)
            st.success('Feedback saved')


def render_parts(answer, mid):
    targets = {}
    labels_by_id = {row['id']: row['label'] for row in answer['citations']}
    for row in answer['citations']:
        uri = row['source']['uri']
        if urlparse(uri).scheme in {'http', 'https'}:
            targets[row['label']] = uri
        else:
            targets[row['label']] = '#evidence-' + mid + '-' + row['label']
    for index, part in enumerate(answer['parts'], 1):
        if len(answer['parts']) > 1:
            st.caption(f"{index}. {part.get('question', '')}")
        if part.get('verbatim'):
            if part.get('coverage_fallback'):
                st.info('Le résultat contrôlé demandé reste non établi ; ces passages apportent seulement un contexte documentaire.'
                        if answer['language'] == 'fr' else
                        'The requested checked result remains unestablished; these passages provide documentary background only.')
            # Keep source bytes literal and attach the final label by evidence
            # identity. Retrieval labels embedded in the saved excerpt string
            # may predate multipart renumbering and are not citation links.
            for row in part['citations']:
                label = labels_by_id[row['id']]
                st.text(row['text'])
                st.markdown(f'[{label}]({targets[label]})')
        else:
            labels = set(part['citation_labels'])
            st.markdown(re.sub(r'\[(E\d+)\]', lambda m:
                f'[{m[1]}]({targets[m[1]]})' if m[1] in labels else m[0], part['display_text']))
        if part.get('documents') is not None:
            st.dataframe(part['documents'], hide_index=True, width='stretch',
                         column_config={'uri': st.column_config.LinkColumn('Source')})
        proof = part.get('retrieval', {}).get('reasoning')
        if part.get('support') == 'accepted_source_assertions':
            st.caption('Assertions de la source ; aucune nouvelle déduction.' if answer['language'] == 'fr' else
                       'Source assertions; no new deduction.')
        if part.get('support') == 'checked_deduction':
            st.caption('Déduction contrôlée sous les prémisses et règles indiquées.' if answer['language'] == 'fr' else
                       'Checked deduction under the stated premises and rules.')
        if proof and part.get('status') == 'supported':
            with st.expander(('Détails de la preuve' if answer['language'] == 'fr' else 'Proof details') + f' · {index}'):
                st.caption('Certificates check symbolic steps under supplied assumptions, not source truth or current legal applicability.')
                st.json(proof)
    if not answer['parts']:
        st.text(answer['text'])
    with st.expander('Diagnostics', expanded=False):
        st.caption('Saved checks describe the original turn; reopening does not re-run providers.')
        st.json({'orchestration': answer.get('orchestration'), 'bindings': answer.get('bindings'),
                 'filters': answer.get('filters'), 'checks': [p.get('checks') for p in answer['parts']],
                 'usage_and_timing': answer.get('telemetry')})


def legacy_history(store, token):
    with st.sidebar.expander('Previous conversations', expanded=False):
        try:
            rows = store.legacy_conversations(token)
        except Exception:
            st.caption('Previous history is unavailable.')
            return False
        choices = {r['id']: r for r in rows}
        selected = st.selectbox('Open previous history', [None, *choices],
            key='assistant-poc-legacy',
            format_func=lambda cid: 'Select a previous conversation' if cid is None else
                str(choices[cid]['created_at'])[:16] + ' · ' + cid[:8])
    if not selected:
        return False
    row = choices[selected]
    st.info('Previous conversation · read-only · original provenance retained. Create or select a conversation to continue in Assistant.')
    with st.expander('Original provenance'):
        st.json(row)
    for message in store.history(selected, token, row['snapshot_id'], row['assistant_mode']):
        with st.chat_message(message['role']):
            if message['role'] == 'user':
                st.text(message['content'])
            else:
                render_answer(message, store, selected, token, feedback=False)
    return True


def select_conversation():
    st.session_state['assistant-poc-legacy'] = None
    selected = st.session_state.get('assistant-poc-conversation')
    if selected:
        st.query_params['conversation_id'] = selected


def main():
    st.set_page_config(page_title='Knowledge Foundry · Assistant', page_icon='📚', layout='wide')
    st.title('Assistant')
    st.caption('Knowledge Foundry · source-backed answers and checked reasoning')
    st.info('Experimental POC · Coverage is limited. Some questions may remain unsupported. '
            'Source editions do not establish current legal applicability.')
    token = st.query_params.get('chat_token')
    if not token or len(token) < 32:
        token = secrets.token_urlsafe(32)
        st.query_params['chat_token'] = token
    st.sidebar.caption('Keep this private URL to reopen your conversations. The history token controls access.')
    try:
        store = PostgresStore()
        default_binding = KnowledgeBinding.load(os.environ.get('KF_ASSISTANT_POC_CONFIG'))
        full, _ = default_binding.snapshots(store)
    except Exception:
        st.error('The pinned knowledge configuration or PostgreSQL is unavailable. No alternative snapshot was selected.')
        st.stop()
    if os.environ.get('KF_ASSISTANT_POC_ENABLED') != '1':
        legacy_history(store, token)
        st.warning('The unified Assistant is implemented locally and awaits scoped application migration and activation approval.')
        st.stop()
    try:
        conversations = store.assistant_conversations(token)
    except Exception:
        st.error('Assistant conversation storage is not installed. Apply the approved POC migration before activation.')
        st.stop()
    if st.sidebar.button('New conversation', type='primary'):
        cid = store.new_assistant_conversation(token, default_binding.value)
        st.session_state['assistant-poc-conversation'] = cid
        st.session_state['assistant-poc-legacy'] = None
        st.query_params['conversation_id'] = cid
        st.rerun()
    choices = {c['id']: c for c in conversations}
    if choices:
        requested = st.query_params.get('conversation_id')
        unavailable = bool(requested and requested not in choices)
        if requested in choices:
            # Restore the owned URL selection before constructing its widget.
            # A new conversation's label changes when its first turn is saved.
            st.session_state['assistant-poc-conversation'] = requested
        elif unavailable:
            st.warning('The requested conversation is unavailable for this private URL. Select or create a conversation.')
            st.session_state['assistant-poc-conversation'] = None
        selected = st.sidebar.selectbox('Conversations', [None, *choices] if unavailable else list(choices),
            index=0,
            key='assistant-poc-conversation',
            on_change=select_conversation, format_func=lambda cid:
                'Select a conversation' if cid is None else
                (choices[cid]['title'] or 'New conversation')[:65] + ' · ' + cid[:8])
        if selected is None and requested in choices:
            selected = requested
        if selected:
            st.query_params['conversation_id'] = selected
            try:
                binding = KnowledgeBinding(choices[selected]['binding'])
                full, _ = binding.snapshots(store)
            except Exception:
                st.error('This conversation’s original knowledge binding is unavailable. No other snapshot was selected.')
                st.stop()
        else:
            binding = default_binding
    else:
        selected, binding = None, default_binding
        st.sidebar.caption('Create a conversation or ask your first question.')
    binding_key = 'assistant-poc-binding-' + binding.digest
    if binding_key not in st.session_state:
        st.session_state[binding_key] = binding
    binding = st.session_state[binding_key]
    if legacy_history(store, token):
        st.stop()
    with st.sidebar.expander('Sources and documents', expanded=False):
        source_map = {s['id']: s['title'] + ' · ' + s['version'] for s in full['identity']['sources']}
        doc_map = {d['id']: source_map[d['source_id']] + ' · ' + d['id'][-8:] for d in full['identity']['documents']}
        scope_key = selected or 'new'
        source_ids = st.multiselect('Source filter', list(source_map), format_func=source_map.get,
                                   key='assistant-poc-source-' + scope_key)
        document_ids = st.multiselect('Document filter', list(doc_map), format_func=doc_map.get,
                                     key='assistant-poc-document-' + scope_key)
        st.caption('Both filters apply to every answering capability. Empty selection means the full pinned scope.')
        st.json(binding.value)
    definitions = corpora(os.environ.get('KF_CORPORA_CONFIG'))
    graph = graph_path(full, definitions.get(full['corpus'], {}))
    if graph and st.sidebar.button('Open provenance graph'):
        show_graph(graph)
    config = load_config(os.environ.get('KF_RETRIEVAL_CONFIG'))
    if not os.environ.get('OPENAI_API_KEY') and config['answer']['provider'] == 'openai':
        config['answer']['provider'] = 'extractive'
        st.caption('Language service is not configured. Standalone questions show exact source excerpts; follow-ups may require clarification.')
    if selected:
        for message in store.assistant_history(selected, token, binding.value):
            with st.chat_message(message['role']):
                if message['role'] == 'user':
                    st.text(message['content'])
                else:
                    render_answer(message, store, selected, token, feedback=False)
    inventory_request = st.button('Documents in corpus')
    question = st.chat_input('Ask a question · Posez votre question',
                             max_chars=config['budget']['max_question_tokens'] * 8)
    if inventory_request:
        question = 'Documents in corpus'
    if question:
        if selected is None:
            selected = store.new_assistant_conversation(token, binding.value)
            st.session_state['assistant-poc-source-' + selected] = source_ids
            st.session_state['assistant-poc-document-' + selected] = document_ids
            st.query_params['conversation_id'] = selected
        with st.chat_message('user'):
            st.text(question)
        with st.chat_message('assistant'):
            try:
                with st.status('Checking sources and preparing your answer', expanded=False) as status:
                    Assistant(store, config, binding, checker=os.environ.get('OO_HORN'),
                        output_dir=os.environ.get('KF_REASONING_OUTPUT'),
                        progress=lambda text: status.update(label=text)).ask(
                            question, token=token, conversation_id=selected,
                            source_ids=source_ids or None, document_ids=document_ids or None,
                            intent='document_inventory' if inventory_request else None)
                    status.update(label='Answer checked and saved', state='complete')
            except Exception:
                st.error('The request could not complete. Check conversation access, pinned snapshots and configured limits. No automatic retry was made.')
                st.stop()
        st.rerun()


if __name__ == '__main__':
    main()
