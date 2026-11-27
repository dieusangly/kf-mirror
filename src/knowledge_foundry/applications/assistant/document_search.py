"""Direct document search over published snapshots in the unified assistant."""
import os
import re
import secrets
from urllib.parse import urlparse

import streamlit as st

from knowledge_foundry.retrieval import config as retrieval_config
from knowledge_foundry.retrieval.config import MODES, corpora
from knowledge_foundry.provenance.graph_lookup import graph_path
from knowledge_foundry.retrieval.service import DocumentAssistant
from knowledge_foundry.storage.postgres import PostgresStore


@st.dialog('Provenance graph', width='large')
def show_graph(path):
    st.caption('Artifact provenance · draft claims and classifications are not verified evidence.')
    st.iframe(path, height=700, alt='Knowledge provenance graph')


def render_answer(message, store, session_id, token):
    answer, mid = message['payload'], message['id']
    labels = {e['label'] for e in answer.get('citations', [])}
    # Extractive source text is displayed literally; generated Markdown links are
    # constructed only for labels already resolved by application code.
    if answer.get('status') == 'inventory':
        st.text(message['content'])
        st.caption('Document inventory from the selected snapshot metadata, with the current source filter.')
    elif answer.get('status') == 'extractive':
        st.text(message['content'])
        st.markdown(' · '.join(f'[{label}](#evidence-{mid}-{label})' for label in sorted(labels)))
    else:
        text = re.sub(r'\[(E\d+)\]', lambda m: f'[{m[1]}](#evidence-{mid}-{m[1]})' if m[1] in labels else m[0], message['content'])
        st.markdown(text)
    if answer.get('status') != 'inventory':
        st.caption('Exact source excerpts · no generated synthesis' if answer.get('status') == 'extractive' else
                   'Draft answer · review the cited passages before relying on it')
    telemetry = answer.get('telemetry', {})
    st.caption(f"Model: {telemetry.get('model', 'unknown')} · {telemetry.get('total_latency_seconds', '?')} s · "
               f"Estimated answer cost: {telemetry.get('estimated_cost_usd')} USD")
    with st.expander('Documents and snapshot' if answer.get('status') == 'inventory' else 'Sources and exact passages', expanded=True):
        citations = answer.get('citations', [])
        if answer.get('status') == 'inventory':
            st.dataframe(answer['documents'], hide_index=True, width='stretch',
                         column_config={'uri': st.column_config.LinkColumn('Source')})
        elif not citations:
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
        with st.expander('Request and retrieval details'):
            st.json({'telemetry': telemetry, 'retrieval': {k: v for k, v in answer.get('retrieval', {}).items() if k != 'evidence'}})
    comment = st.text_input('Optional feedback', key=f'comment-{mid}', max_chars=2000)
    up, down = st.columns(2)
    for column, rating, label in [(up, 1, 'Helpful'), (down, -1, 'Needs work')]:
        if column.button(label, key=f'feedback-{mid}-{rating}'):
            store.feedback(session_id, token, mid, rating, comment)
            st.success('Feedback saved')


def render_document_search(*, local_only):
    config_path = os.environ.get('KF_RETRIEVAL_CONFIG')
    config = retrieval_config.load_config(config_path)
    if not config_path:
        config['search'].update(method='vector', mode='passage-and-claim')
    has_key = bool(os.environ.get('OPENAI_API_KEY'))
    can_embed = not local_only and (has_key or config['embedding']['provider'] != 'openai')
    can_generate = has_key and not local_only
    if local_only:
        st.info('Local-only mode uses keyword search and exact source excerpts.')
    elif not has_key:
        st.info('OPENAI_API_KEY is not configured. Generated answers are unavailable.')
    if not can_embed or (not can_generate and config['answer']['provider'] == 'openai'):
        config['search']['method'] = 'keyword'
    if not can_generate:
        config['answer']['provider'] = 'extractive'
    try:
        store = PostgresStore()
        snapshots = [s for s in store.snapshots() if s['status'] == 'published']
    except ValueError:
        st.error('Set KF_DATABASE_URL to connect to the document corpus. See doc/aml-cft-pipeline.md.')
        st.stop()
    except Exception:
        st.error('PostgreSQL is unavailable or migrations have not run. Follow doc/aml-cft-pipeline.md.')
        st.stop()
    if not snapshots:
        st.info('No published snapshots. Import validated artifacts, then explicitly publish the snapshot.')
        st.stop()
    definitions = corpora(os.environ.get('KF_CORPORA_CONFIG'))
    corpus = st.sidebar.selectbox('Corpus', sorted({s['corpus'] for s in snapshots}),
                                  key='document-corpus', persist_state='page',
                                  format_func=lambda c: definitions.get(c, {}).get('title', c))
    choices = [s for s in snapshots if s['corpus'] == corpus]
    choices.sort(key=lambda s: not s.get('active'))
    selected_id = st.sidebar.selectbox('Published snapshot', [s['id'] for s in choices],
        key=f'document-snapshot-{corpus}', persist_state='page',
        format_func=lambda sid: ('Active · ' if next(s for s in choices if s['id'] == sid).get('active') else 'Previous · ') + sid[-12:])
    snapshot = next(s for s in choices if s['id'] == selected_id)
    with st.sidebar.expander('Snapshot provenance'):
        st.write('Chunk build: ' + snapshot['identity']['chunk_build_id'])
        st.write('Snapshot: ' + selected_id)
    st.sidebar.caption('Publication selects an index version; it does not approve source content or draft claims.')
    methods = ['keyword', 'vector', 'combined'] if can_embed else ['keyword']
    method = st.sidebar.selectbox('Search', methods, key='document-method', persist_state='page',
        index=methods.index(config['search']['method']), disabled=not can_embed,
        help='Vector and combined search use query embeddings and require OPENAI_API_KEY.')
    mode = st.sidebar.selectbox('Retrieve via', MODES, index=MODES.index(config['search']['mode']),
                               key='document-retrieval-mode', persist_state='page')
    all_coverage = store.vector_coverage(selected_id)
    kinds = ('passage', 'claim') if mode == 'passage-and-claim' else (mode,)
    coverage = {key: sum(all_coverage.get(kind, {}).get(key, 0) for kind in kinds)
                for key in ('records', 'vectors')}
    st.sidebar.caption(f"{mode.title()}: {coverage['records']} records · {coverage['vectors']} with vectors")
    if method != 'keyword' and coverage['vectors'] < coverage['records']:
        st.sidebar.warning('Vector coverage is incomplete. Reimport with --embed-missing, or use keyword search.')
    providers = ['extractive', 'openai'] if can_generate else ['extractive']
    config['answer']['provider'] = st.sidebar.selectbox('Answer mode', providers,
        key='document-answer-mode', persist_state='page', disabled=not can_generate,
        index=providers.index(config['answer']['provider']),
        format_func=lambda p: 'Source excerpts' if p == 'extractive' else 'Generated answer (OpenAI)')
    if config['answer']['provider'] == 'openai':
        st.sidebar.caption('Generated answers require OPENAI_API_KEY and may incur API costs.')
    source_map = {s['id']: s['title'] + ' · ' + s['version'] for s in snapshot['identity'].get('sources', [])}
    source_ids = st.sidebar.multiselect('Source filter', list(source_map), format_func=source_map.get,
                                        key=f'document-sources-{selected_id}', persist_state='page')
    graph = graph_path(snapshot, definitions.get(corpus, {}))
    if graph and st.sidebar.button('Open provenance graph'):
        show_graph(graph)
    token = st.query_params.get('chat_token')
    if not token or len(token) < 32:
        token = secrets.token_urlsafe(32)
        st.query_params['chat_token'] = token
    st.sidebar.caption('This local demo uses a private history token in the URL. Keep the URL private to retain your history.')
    conversation_key = 'conversation-' + selected_id
    if st.sidebar.button('New conversation / reset'):
        st.session_state[conversation_key] = store.new_session(selected_id, token)
        st.rerun()
    sessions = store.sessions(selected_id, token)
    if not sessions:
        store.new_session(selected_id, token)
        sessions = store.sessions(selected_id, token)
    session_id = st.sidebar.selectbox('Conversation history', [s['id'] for s in sessions], key=conversation_key, persist_state='page',
                                      format_func=lambda sid: sid[:8])
    st.info('Draft document assistance. Source capture dates do not establish legal effectiveness. '
            'Claims and summaries only help locate original passages.')
    if config['answer']['provider'] == 'extractive':
        st.caption('Extractive demo: answers show exact source excerpts. Configure the OpenAI provider for generated answers.')
    pending, intent = None, None
    if st.button('Documents in corpus'):
        pending, intent = 'Documents in corpus', 'document_inventory'
    suggestions = definitions.get(corpus, {}).get('suggested_questions', [])
    if suggestions:
        with st.expander('Suggested questions', expanded=True):
            for i, question in enumerate(suggestions):
                if st.button(question, key=f'suggest-{i}'):
                    pending = question
    for message in store.history(session_id, token, selected_id):
        with st.chat_message(message['role']):
            if message['role'] == 'user':
                st.write(message['content'])
            else:
                render_answer(message, store, session_id, token)
    question = st.chat_input('Ask a question about the selected sources') or pending
    if question:
        with st.chat_message('user'):
            st.write(question)
        with st.chat_message('assistant'):
            st.caption('Retrieving evidence · streamed text is an unvalidated draft until complete')
            assistant = DocumentAssistant(store, config)

            def chunks():
                for event in assistant.stream(corpus, question, token=token, session_id=session_id,
                                              snapshot_id=selected_id, method=method, mode=mode,
                                              source_ids=source_ids or None, intent=intent):
                    if event['type'] == 'delta':
                        yield event['text']

            try:
                st.write_stream(chunks())
            except Exception as exc:
                # Do not expose database connection strings or provider payloads.
                st.error('The request could not complete. Check the selected mode, configured embeddings, API key and request budgets.')
                if isinstance(exc, ValueError):
                    st.caption(str(exc))
                st.stop()
        st.rerun()
