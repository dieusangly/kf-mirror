"""Cross-corpus mechanics with validated new documents and deterministic models.

Fixture vectors are explicit relevance oracles, not a semantic-quality benchmark.
"""
import json

import pytest

from knowledge_foundry.retrieval.artifacts import profile, project
from knowledge_foundry.retrieval.answering import generate
from knowledge_foundry.retrieval.service import DocumentAssistant
from knowledge_foundry.structural_chunks import stable_hash
from rag_fixtures import bundle, config


@pytest.mark.parametrize(('corpus', 'language', 'passages', 'query', 'anchor'), [
    ('equipment', 'english', ['Battery policy', 'Battery XQ3 operates for twelve hours below forty degrees.',
                             'Battery XQ3 must be stored dry.', 'The warranty excludes immersion.'],
     'How long does battery XQ3 operate?', 'twelve hours'),
    ('horticulture', 'french', ['Culture du basilic', 'Le basilic demande six heures de lumière chaque jour.',
                               'Le basilic craint le gel.', 'Les orchidées demandent un autre substrat.'],
     'Combien de lumière demande le basilic ?', 'six heures'),
    ('transport', 'german', ['Genehmigungen', 'Transportgenehmigungen gelten drei Jahre ab Ausstellung.',
                            'Die Genehmigung gilt nur im Inland.', 'Reisepässe sind andere Dokumente.'],
     'Wie lange gelten Transportgenehmigungen?', 'drei Jahre'),
])
def test_new_validated_documents_use_the_same_scoped_service(store, tmp_path, corpus, language, passages, query, anchor):
    b = bundle([('heading' if i == 0 else 'paragraph', text) for i, text in enumerate(passages)], source_name=corpus)
    structural = [{'id': 'structural:' + stable_hash(c['id']), 'text': c['text'],
                   'original_chunk_ids': [c['id']], 'document_id': c['document_id'],
                   'evidence_memberships': [{'reference': c['evidence'][0], 'original_chunk_ids': [c['id']]}]}
                  for c in b['chunks']]
    a = {'bundle': b, 'structural': structural, 'claims': [], 'summaries': [], 'hierarchy': [], 'links': [], 'embeddings': []}
    records, evidence = project(a)
    for r in records:
        r['vector'] = [1., 0., 0.] if anchor in r['text'] else [0., 1., 0.]
    c = config(tmp_path)
    identity = {'chunk_build_id': b['builds'][0]['id'], 'sources': b['sources'], 'documents': b['documents']}
    sid = store.import_snapshot(corpus, identity, records, evidence, profile(c['embedding']), language)['snapshot_id']
    store.publish(corpus, sid)
    # A second, otherwise identical corpus must not change the selected scope.
    other = store.import_snapshot('other-' + corpus, identity, records, evidence, profile(c['embedding']), language)['snapshot_id']
    store.publish('other-' + corpus, other)
    fixture = {'embedding_vectors': {stable_hash(query): [1., 0., 0.]}}
    assistant = DocumentAssistant(store, c, embedding_fixture=fixture)
    for method in ('keyword', 'vector', 'combined'):
        result = assistant.retrieve(corpus, query, method=method)
        assert result['snapshot_id'] == sid
        assert any(anchor in e['text'] for e in result['evidence'])
        answer = list(generate(c, result['payload'], result['evidence']))[-1]['answer']
        assert anchor in answer['text'] and answer['status'] == 'extractive'
        saved = store.evidence(sid, [e['id'] for e in answer['citations']])
        assert all(saved[e['id']]['reference'] == e['reference'] for e in answer['citations'])
    def stream(payload, config):
        rows = json.loads(payload['input'])['evidence']
        row = next(e for e in rows if anchor in e['text'])
        yield {'type': 'delta', 'text': row['text'] + ' [' + row['label'] + ']'}
    c['answer']['provider'] = 'openai'
    answer = list(generate(c, result['payload'], result['evidence'], stream))[-1]['answer']
    assert answer['status'] == 'draft' and anchor in answer['citations'][0]['text']
    assert assistant.retrieve(corpus, query, source_ids=['absent'])['evidence'] == []
    c['search']['rerank'] = True
    def rank(payload, config):
        rows = json.loads(payload['input'])['candidates']
        indices = [r['index'] for r in rows if anchor in r['text']]
        yield {'type': 'delta', 'text': json.dumps({'selected_indices': indices})}
    ranked = DocumentAssistant(store, c, embedding_fixture=fixture, ranking_stream=rank)
    for method in ('keyword', 'vector', 'combined'):
        result = ranked.retrieve(corpus, query, method=method)
        assert result['ranking']['selected'] == 1
        assert result['snapshot_id'] == sid
        assert anchor in result['evidence'][0]['text']
