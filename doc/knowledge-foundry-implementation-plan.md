# Knowledge Foundry — implementation plan

Roadmap for traceable claims/summaries and retrieval, graph and dataset views.
Start with one domain and [benchmarked components](open-source-project-benchmark.md).
Estimates include review/validation and are planning assumptions, not completion status.

## Core pipeline

```text
Saved source
→ document
→ parsed elements
→ original chunks
→ structural chunks with document hierarchy
→ semantic clusters
        |→ evidence-linked claims → topic classification and review
        |→ level-one summaries → clusters of summaries → higher-level summaries ↻
```

Immutable chunks feed hierarchy/budget-aware clusters. Claims and recursive
summaries retain provenance independently; claims require grounding/classification review.

| Task | Working days | Deliverable / completion criteria |
| --- | --- | --- |
| 1. Scope/reference corpus | 1–2 | Domain, supported document classes, representative files, quality/cost targets and annotated evaluation passages. |
| 2. Artifact schemas | 2–4 | Source/document/element/chunk, structural hierarchy/cluster, claim/summary/topic and build contracts: stable IDs, versions, locations, lineage and validating [examples](artifact-schemas.md). |
| 3. Ingestion | 2–3 | Package, config and CLI; inventory files, detect formats, hash bytes and record every source outcome. |
| 4. Parsing/normalization | 4–7 | Existing parser for PDF/Markdown/HTML/DOCX and tested OCR scans; preserve hierarchy, tables, available locations and metadata origins; measure reference fidelity. |
| 5. Cleanup/chunking | 3–5 | Conservative cleanup/exact deduplication; focused original chunks with tables, parent context, token limits and resolvable evidence. |
| 6. Structural chunk and semantic cluster build | 2–4 | Explicit section/list hierarchy across chunks, bounded structural assembly and conservative links. Cache embeddings, cluster by all-member similarity and partition oversized requests; preserve immutable originals and exact lineage. |
| 7. Claim generation, classification and export | 4–7 | Evidence-linked candidate claims and LLM topic/null classification across documents. Separate versions and grounding/classification reviews; export JSON/JSONL, topic views and manifests with validated links. |
| 8. Recursive summaries | 2–4 | Summarize each semantic cluster independently of claims, then cluster/summarize earlier summaries within similarity, member, token and level limits. Export stable IDs, transitive provenance, draft status and model/runtime metadata. |
| 9. Repeat builds | 3–5 | Cache/resume stages; invalidate affected chunks, clusters, claims, summaries and topics after source/settings changes. Test additions, updates, removals, conflicts and stale-artifact cleanup; retain prior editions. |

**Milestone:** reproducible mixed-document builds with small parser adapters,
domain topics, validated synthesis and collection-change handling.

## Knowledge views and downstream validation

Choose reviewed passages, claims or summaries for the task.

| Task | Working days | Deliverable / completion criteria |
| --- | --- | --- |
| 10. Output quality | 2–4 | Held-out source/context review of the chosen claims, topics or summaries: grounding, coverage, conflicts, unsupported synthesis, source versions, rejection reasons and generation costs. |
| 11. Retrieval/RAG | 2–4 | Compare passage retrieval with optional claim/topic or summary indexes against a chunk-only baseline. For cited RAG, assess answer support and source retrieval. |
| 12. Knowledge graph | 2–4 | Inspect claim or summary provenance. Optionally derive typed entities/relations from reviewed claims under a domain schema, with passage evidence, conservative identity rules and edge/query checks. |
| 13. Training data | 3–5 | Extraction/classification examples from reviewed claims, or synthesis examples from reviewed summaries. Split document families before generation; retain evidence/rejections and export JSONL/Parquet with a dataset card. |

**Milestone:** retrieval, domain graphs and training examples traceable to saved
files. Retain source chunks for citations; combine outputs only when useful.

## Integration and release

| Task | Working days | Deliverable / completion criteria |
| --- | --- | --- |
| 14. Validate/package | 3–5 | Run a larger representative collection, fix blockers and reproduce installation in a fresh environment. Deliver usage/config guides, sample artifacts, tested dependencies and quality/cost report. |

Report sample sizes, failure classes, fidelity, reference validity, retrieval,
factual precision, grounding, runtime, memory and inference cost where applicable.
Combine automated checks with manual review.

**Milestone:** another user can reproduce the workflow and understand supported
scope and measured limits.

## Dependencies and execution

- Stabilize scope/schemas before shared interfaces; follow the pipeline above.
- Validate inputs before downstream use.
- Bound agent tasks/acceptance criteria; parallelize after contracts stabilize.
- If estimates grow, defer scope while retaining provenance/evaluation.

Consider further formats, connectors, graph retrieval, entity matching, training,
review interfaces and deployment after release evaluation.
