"""Indexing orchestration; never generates or modifies upstream artifacts."""
from knowledge_foundry.embeddings import EmbeddingAdapter
from knowledge_foundry.retrieval.artifacts import PROJECTION_VERSION, load_artifacts, profile, project
from knowledge_foundry.retrieval.config import ROOT


def index_artifacts(store, corpus, config, *, embed_missing=False, fixture=None, graph_url=None, **selection):
    artifacts = load_artifacts(**selection)
    records, evidence = project(artifacts, config['embedding'])
    telemetry = {'requests': 0, 'input_tokens': 0, 'estimated_cost_usd': 0., 'cache_hits': 0}
    if embed_missing:
        adapter = EmbeddingAdapter(config['embedding'], ROOT / config['cache_dir'], fixture=fixture,
                                   pricing=config.get('embedding_pricing'))
        pending = [r for r in records if r['vector'] is None]
        embedded = adapter.embed(pending)
        by_id = {r['structural_chunk_id']: r['vector'] for r in embedded}
        for r in pending:
            r['vector'] = by_id[r['id']]
        telemetry = adapter.telemetry
    identity = {**artifacts['identity'], 'projection_version': PROJECTION_VERSION,
                'builds': artifacts['bundle']['builds'], 'sources': artifacts['bundle']['sources'],
                'documents': artifacts['bundle']['documents'], 'graph_url': graph_url}
    result = store.import_snapshot(corpus, identity, records, evidence, profile(config['embedding']), config['search']['language'])
    result['embedding_usage'] = telemetry
    result['saved_vectors_reused'] = sum(r['vector'] is not None for r in records) - (len(pending) if embed_missing else 0)
    return result
