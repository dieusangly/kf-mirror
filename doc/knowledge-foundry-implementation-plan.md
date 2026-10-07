# Knowledge Foundry — implementation plan

Build a configurable pipeline from document collections to a topic-based knowledge base with traceable claims. Keep parsed documents and chunks as evidence; derive retrieval, graph, and dataset views. Start with one domain and reuse components identified in the [benchmark](open-source-project-benchmark.md).

Effort estimates are working days for implementation, review, and validation; revise them after testing representative files.

## Core pipeline

The durable path is **saved source → document → parsed element → chunk → grounded claim → topic**. Topics group supported claims across sources; claims retain passage evidence and review status.

| Task | Estimated effort | Deliverable and completion criteria |
| --- | --- | --- |
| 1. Define scope and reference corpus | 1–2 days | Select the domain, supported document classes, representative examples, and quality/cost targets. Annotate selected pages and passages for evaluation. |
| 2. Define artifact schemas | 2–4 days | Specify source, document, element, chunk, claim, topic, and build records with stable IDs, schema versions, source locations, and lineage. Example artifacts validate against the [artifact schemas](artifact-schemas.md). |
| 3. Implement local ingestion | 2–3 days | Provide a package, configuration, and command-line entry point. Inventory files, detect formats, hash content, and record an outcome for every source. |
| 4. Integrate parsing and normalization | 4–7 days | Support PDF, Markdown, HTML, and DOCX using an existing parser; enable OCR for tested scans. Preserve hierarchy, tables, available locations, and metadata origins. Measure fidelity on the reference corpus. |
| 5. Clean, deduplicate, and chunk | 3–5 days | Apply conservative cleanup and exact duplicate detection. Produce configurable structural chunks with table handling, parent context, token limits, and resolvable source references. |
| 6. Compile and export the topic knowledge base | 4–7 days | Derive evidence-linked candidate claims from chunks; use an LLM to classify them under stable topic IDs, including claims from multiple source documents. Track claim-generation and topic-classification methods/versions separately, along with separate claim grounding and classification review statuses. Export normalized JSON, chunk and claim JSONL, topic JSON/Markdown, and build manifests. Validate references and avoid presenting unreviewed synthesis as verified knowledge. |
| 7. Support repeat builds | 3–5 days | Cache completed stages, resume interrupted runs, and invalidate chunks, claims, and topics when sources or processing settings change. Verify additions, updates, removals, conflicts, and cleanup of stale artifacts. Preserve prior source editions for traceability. |

Keep parser adapters small and retain parsed artifacts for independent rebuilds. Compare another parser if a required document class exposes a limitation. Pilot topics include beneficial ownership and ongoing vigilance; review synthesis against full source context.

**Core milestone:** a reproducible command converts a mixed document collection into a traceable topic knowledge base and handles subsequent collection changes.

## Knowledge views and downstream validation

Evaluate topics before deriving retrieval/RAG, graph, and training-data views.

| Task | Estimated effort | Deliverable and completion criteria |
| --- | --- | --- |
| 8. Validate topic knowledge quality | 2–4 days | Review a held-out sample of claims and topic summaries against source passages and context. Measure grounding, coverage, conflict handling, unsupported synthesis, and source-version accuracy; record rejection reasons and generation costs. |
| 9. Validate retrieval and RAG compatibility | 2–4 days | Index chunks and topic records, compare passage retrieval with a chunk-only baseline, and provide an optional RAG example with source citations. Evaluate answer support and topic usefulness when generation is enabled. |
| 10. Export a knowledge graph | 2–4 days | Extract typed entities and relations from reviewed claims under a small domain schema. Build graph JSON and node/edge CSV with passage evidence, model/prompt versions, and validation status. Use conservative identity rules and verify edge integrity and domain queries. |
| 11. Generate training datasets | 3–5 days | Produce a small instruction/answer dataset from reviewed claims and their passages. Split document families before generation, preserve evidence and rejection reasons, and export JSONL/Parquet with a dataset card. Review grounding and split isolation. |

RAG (retrieval-augmented generation) combines retrieved context with generation. Keep chunks for precise citations; derive graphs and datasets from reviewed claims.

**Knowledge milestone:** the same topic knowledge base supports retrieval, a domain graph, and source-grounded training examples, with traceable paths back to saved files.

## Integration and release

Validate and package the combined workflow.

| Task | Estimated effort | Deliverable and completion criteria |
| --- | --- | --- |
| 12. Validate and package the workflow | 3–5 days | Run the combined pipeline on a larger representative collection, fix blocking defects, and reproduce installation in a fresh environment. Deliver usage/configuration guides, sample artifacts, tested dependencies, and a quality/cost report. |

Report sample sizes, failure classes, fidelity, reference validity, retrieval quality, factual precision, grounding, runtime, memory, and inference cost where applicable. Pair automated checks with manual review.

**Release milestone:** another user can reproduce the documented workflow and understand its supported scope and measured limitations.

## Dependencies and execution

- Define scope and schemas before shared interfaces; ingest and parse before chunking and topic compilation.
- Validate the core before downstream views. Retrieval indexes chunks and topics; graph and dataset exports use reviewed claims and can proceed independently.
- Give agents bounded tasks and acceptance criteria; parallelize adapters or exporters once contracts stabilize.
- If estimates grow, narrow the domain or defer a downstream view while retaining provenance and evaluation.

Consider further formats, connectors, graph retrieval, entity matching, training, review interfaces, and deployment after release evaluation.
