"""Offline browser launcher: actual snapshots/SQL, explicitly synthetic language."""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src'), str(ROOT / 'tests/assistant')]
from scripts.assistant_poc import ISOLATED_DSN, isolated
from knowledge_foundry.assistant.service import Assistant
from poc_fixtures import audits, document_answer, interpretation, ranking, reasoning_answer, route

isolated()  # Refuse the application database, even if the environment is wrong.
os.environ['KF_DATABASE_URL'] = ISOLATED_DSN
os.environ['KF_ASSISTANT_POC_ENABLED'] = '1'
os.environ['KF_REASONING_OUTPUT'] = '/tmp/assistant-poc-browser-proofs'
os.environ['OPENAI_API_KEY'] = 'poc-offline-fixture-no-network'


def blocked(*args, **kwargs):
    raise AssertionError('offline POC browser transport cannot access providers')


import knowledge_foundry.assistant.service as orchestration
import knowledge_foundry.reasoning.assistant as reasoning
import knowledge_foundry.retrieval.answering as answering
import knowledge_foundry.retrieval.query as query
import knowledge_foundry.retrieval.ranking as ranker
from knowledge_foundry.embeddings import EmbeddingAdapter
for module in (orchestration, reasoning, answering, query, ranker):
    module.openai_stream = blocked
EmbeddingAdapter._request = blocked

if not hasattr(Assistant, '_poc_fixture_init'):
    Assistant._poc_fixture_init = Assistant.__init__


def fixture_init(self, store, config, binding, **kwargs):
    config['answer']['provider'] = 'openai'
    Assistant._poc_fixture_init(self, store, config, binding, route_stream=route,
        audit_stream=audits, document_stream=document_answer, interpretation_stream=interpretation,
        reasoning_stream=reasoning_answer, reasoning_audit_stream=audits, ranking_stream=ranking, **kwargs)


Assistant.__init__ = fixture_init
runpy.run_path(str(ROOT / 'apps/chat/app.py'), run_name='__main__')
import streamlit as st
st.sidebar.caption('Offline QA fixture · synthetic language and audits · no provider calls')
