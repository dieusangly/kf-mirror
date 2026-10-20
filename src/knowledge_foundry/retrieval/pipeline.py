"""Haystack pipeline for scoped retrieval, provenance resolution and context."""
from haystack import Document, Pipeline, component

from knowledge_foundry.retrieval.context import assemble_context


def document(row):
    return Document(id=row['id'], content=row['text'], score=row.get('score'),
                    meta={'kind': row['kind'], 'evidence_ids': row['evidence_ids'],
                          'source_ids': row['source_ids'], 'document_ids': row['document_ids'],
                          **row['metadata']})


@component
class SnapshotRetriever:
    def __init__(self, store, snapshot, config):
        self.store, self.snapshot, self.config = store, snapshot, config

    @component.output_types(documents=list[Document])
    def run(self, query: str, method: str, mode: str, vector: list[float] | None = None,
            source_ids: list[str] | None = None, document_ids: list[str] | None = None):
        rows = self.store.search(self.snapshot, query, mode, method, self.config['search'], vector,
                                 source_ids, document_ids)
        return {'documents': [document(r) for r in rows]}


@component
class EvidenceResolver:
    def __init__(self, store, snapshot, config):
        self.store, self.snapshot, self.config = store, snapshot, config

    @component.output_types(documents=list[Document], navigation=list[Document])
    def run(self, documents: list[Document], query: str, source_ids: list[str] | None = None,
            document_ids: list[str] | None = None):
        resolved, seen = [], set()
        for doc in documents:
            rows = ([doc] if doc.meta['kind'] == 'passage' else
                    [document(r) for r in self.store.supporting_passages(
                        self.snapshot['id'], doc.meta['evidence_ids'],
                        doc.meta['original'].get('chunk_ids', doc.meta['original'].get('original_chunk_ids', [])),
                        query, self.config['search']['top_k'], self.snapshot['identity']['language'],
                        source_ids, document_ids)])
            for passage in rows:
                if passage.id not in seen:
                    seen.add(passage.id)
                    resolved.append(passage)
        return {'documents': resolved[:self.config['search']['top_k']], 'navigation': documents}


@component
class ContextAssembler:
    def __init__(self, store, snapshot, config):
        self.store, self.snapshot, self.config = store, snapshot, config

    @component.output_types(context=dict)
    def run(self, documents: list[Document], question: str, history: list[str],
            source_ids: list[str] | None = None, document_ids: list[str] | None = None):
        return {'context': assemble_context(self.store, self.snapshot['id'], documents, question,
                                            history, self.config, source_ids, document_ids)}


def retrieval_pipeline(store, snapshot, config):
    pipeline = Pipeline()
    pipeline.add_component('retrieve', SnapshotRetriever(store, snapshot, config))
    pipeline.add_component('resolve', EvidenceResolver(store, snapshot, config))
    pipeline.add_component('context', ContextAssembler(store, snapshot, config))
    pipeline.connect('retrieve.documents', 'resolve.documents')
    pipeline.connect('resolve.documents', 'context.documents')
    return pipeline
