# Local artifacts

These folders hold local pipeline runs, reasoning evaluations, application data and caches. For commands and run procedures, see the [project guides](../doc/README.md).

| Folder | Contents |
| --- | --- |
| `ingested/` | Original source documents stored by content hash, immutable ingestion snapshots and the ingestion report. |
| `20261027-unstructured-aml-cft-01/` | Outputs from the Unstructured fast and high-resolution parsing runs, with source reviews and benchmark comparisons. |
| `20261028-docling-aml-cft-01/` | The Docling pilot’s accepted structural chunks, semantic clusters, claims, summaries, graphs, review evidence and retrieval evaluation. |
| `aml-cft-reasoning-pilot/` | Pilot datasets, engine setup, formalized rules, reasoning results and reviews for the synthetic and source-backed cases. |
| `aml-cft-reasoning-assistant/` | Cached embeddings for the assistant’s scenario search. |
| `aml-cft-reasoning-assistant-evaluation/` | Bilingual evaluation questions and reference answers, live assistant responses, review findings and scores. |
| `contributions/` | The contributor app’s local SQLite database for accounts and contributions. |
| `db/` | PostgreSQL backup dumps from before and after the document database rebuild. |
| `cache/` | Cached PDF source-verification results. |
| `embeddings-and-token-counts/` | Shared caches of embedding vectors and model request token counts. |
| `summary-cache/` | Embeddings, token counts and model responses reused by summary generation. |
