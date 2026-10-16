# Knowledge Foundry — implementation plan

Build a configurable document-to-topic pipeline with traceable claims. Retain
parsed documents/chunks as evidence; derive retrieval, graph and dataset views.
Start with one domain and reuse [benchmarked components](open-source-project-benchmark.md).
Estimates include implementation, review and validation; revise after representative tests.

## Core pipeline

**Saved source → document → parsed element → chunk → grounded claim → topic.**
Topics group claims across sources; claims retain passages and review status.

| Task | Working days | Deliverable / completion criteria |
| --- | --- | --- |
| 1. Scope/reference corpus | 1–2 | Domain, supported document classes, representative files, quality/cost targets and annotated evaluation passages. |
| 2. Artifact schemas | 2–4 | Source/document/element/chunk/claim/topic/build contracts: stable IDs, versions, locations, lineage and validating [examples](artifact-schemas.md). |
| 3. Ingestion | 2–3 | Package, config and CLI; inventory files, detect formats, hash bytes and record every source outcome. |
| 4. Parsing/normalization | 4–7 | Existing parser for PDF/Markdown/HTML/DOCX and tested OCR scans; preserve hierarchy, tables, available locations and metadata origins; measure reference fidelity. |
| 5. Cleanup/chunking | 3–5 | Conservative cleanup/exact deduplication; configurable structural chunks with tables, parent context, token limits and resolvable evidence. |
| 6. Claim generation, classification and export | 4–7 | Evidence-linked candidate claim generation and LLM classification under stable topic IDs across documents. Separate generation/classification methods, versions and grounding/classification review statuses. Export normalized JSON, chunk/claim JSONL, topic JSON/Markdown and manifests; validate links and label unreviewed synthesis. |
| 7. Repeat builds | 3–5 | Cache/resume stages; invalidate affected chunks/claims/topics after source/settings changes. Test additions, updates, removals, conflicts and stale-artifact cleanup; retain prior editions. |

Keep parser adapters small and parsed artifacts rebuildable. Compare alternatives
when required formats expose limits. Pilot topics include beneficial ownership
and ongoing vigilance; verify synthesis against full context.

**Milestone:** one reproducible command builds a traceable knowledge base from
mixed documents and handles collection changes.

## Knowledge views and downstream validation

Validate topics before deriving retrieval/RAG, graph or training-data views.

| Task | Working days | Deliverable / completion criteria |
| --- | --- | --- |
| 8. Topic quality | 2–4 | Held-out source/context review of claims and summaries: grounding, coverage, conflicts, unsupported synthesis, source versions, rejection reasons and generation costs. |
| 9. Retrieval/RAG | 2–4 | Index chunks/topics; compare passage retrieval with a chunk-only baseline. Optional cited RAG example; assess answer support/topic usefulness when generation is enabled. |
| 10. Knowledge graph | 2–4 | Typed entities/relations from reviewed claims under a small domain schema. Graph JSON, node/edge CSV, passage evidence, model/prompt versions and validation status; conservative identity rules, edge integrity and query checks. |
| 11. Training data | 3–5 | Small instruction/answer dataset from reviewed claims/passages. Split document families before generation; retain evidence/rejections; export JSONL/Parquet and dataset card. Review grounding/split isolation. |

RAG combines retrieval with generation. Keep chunks for precise citations;
graphs/datasets derive from reviewed claims.

**Milestone:** one knowledge base supports retrieval, a domain graph and grounded
training examples, all traceable to saved files.

## Integration and release

| Task | Working days | Deliverable / completion criteria |
| --- | --- | --- |
| 12. Validate/package | 3–5 | Run a larger representative collection, fix blockers and reproduce installation in a fresh environment. Deliver usage/config guides, sample artifacts, tested dependencies and quality/cost report. |

Report sample sizes, failure classes, fidelity, reference validity, retrieval,
factual precision, grounding, runtime, memory and inference cost where applicable.
Combine automated checks with manual review.

**Milestone:** another user can reproduce the workflow and understand supported
scope and measured limits.

## Dependencies and execution

- Define scope/schemas before shared interfaces; ingest/parse before chunking/claim generation.
- Validate the core before downstream views. Retrieval uses chunks/topics;
  graph/dataset exports use reviewed claims and can proceed independently.
- Give agents bounded tasks and acceptance criteria; parallelize adapters/exporters
  after contracts stabilize.
- If estimates grow, narrow scope or defer a downstream view; retain provenance/evaluation.

Consider further formats, connectors, graph retrieval, entity matching, training,
review interfaces and deployment after release evaluation.
