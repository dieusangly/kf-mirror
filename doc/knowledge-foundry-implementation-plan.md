# Knowledge Foundry — implementation plan

Build a configurable document-to-knowledge pipeline with traceable claims and
summaries. Retain parsed documents/chunks as evidence; derive retrieval, graph
and dataset views.
Start with one domain and reuse [benchmarked components](open-source-project-benchmark.md).
Estimates include implementation, review and validation; revise after representative tests.

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

Original chunks remain immutable. Structural preparation preserves hierarchy
across focused chunks and bounds clusters to request budgets. Claims and summaries
are independent outputs; higher-level summaries recursively combine earlier ones.
Both retain provenance, while claims also follow grounding/classification review.

| Task | Working days | Deliverable / completion criteria |
| --- | --- | --- |
| 1. Scope/reference corpus | 1–2 | Domain, supported document classes, representative files, quality/cost targets and annotated evaluation passages. |
| 2. Artifact schemas | 2–4 | Source/document/element/chunk, structural hierarchy/cluster, claim/summary/topic and build contracts: stable IDs, versions, locations, lineage and validating [examples](artifact-schemas.md). |
| 3. Ingestion | 2–3 | Package, config and CLI; inventory files, detect formats, hash bytes and record every source outcome. |
| 4. Parsing/normalization | 4–7 | Existing parser for PDF/Markdown/HTML/DOCX and tested OCR scans; preserve hierarchy, tables, available locations and metadata origins; measure reference fidelity. |
| 5. Cleanup/chunking | 3–5 | Conservative cleanup/exact deduplication; focused original chunks with tables, parent context, token limits and resolvable evidence. |
| 6. Structural preparation and semantic clustering | 2–4 | Explicit section/list hierarchy across chunks, bounded structural assembly and conservative links. Cache embeddings, cluster by all-member similarity and partition oversized requests; preserve immutable originals and exact lineage. |
| 7. Claim generation, classification and export | 4–7 | Evidence-linked candidate claims and LLM topic/null classification across documents. Separate versions and grounding/classification reviews; export JSON/JSONL, topic views and manifests with validated links. |
| 8. Recursive summaries | 2–4 | Summarize each semantic cluster independently of claims, then cluster/summarize earlier summaries within similarity, member, token and level limits. Export stable IDs, transitive provenance, draft status and model/runtime metadata. |
| 9. Repeat builds | 3–5 | Cache/resume stages; invalidate affected chunks, clusters, claims, summaries and topics after source/settings changes. Test additions, updates, removals, conflicts and stale-artifact cleanup; retain prior editions. |

Keep parser adapters small and parsed artifacts rebuildable. Compare alternatives
when required formats expose limits. Configure topics for the selected domain
and verify synthesis against full context.

**Milestone:** one reproducible command builds a traceable knowledge base from
mixed documents and handles collection changes.

## Knowledge views and downstream validation

Choose reviewed claims, summaries or source passages according to each use;
neither generated output requires the other.

| Task | Working days | Deliverable / completion criteria |
| --- | --- | --- |
| 10. Output quality | 2–4 | Held-out source/context review of the chosen claims, topics or summaries: grounding, coverage, conflicts, unsupported synthesis, source versions, rejection reasons and generation costs. |
| 11. Retrieval/RAG | 2–4 | Compare passage retrieval with optional claim/topic or summary indexes against a chunk-only baseline. For cited RAG, assess answer support and source retrieval. |
| 12. Knowledge graph | 2–4 | Inspect claim or summary provenance. Optionally derive typed entities/relations from reviewed claims under a domain schema, with passage evidence, conservative identity rules and edge/query checks. |
| 13. Training data | 3–5 | Extraction/classification examples from reviewed claims, or synthesis examples from reviewed summaries. Split document families before generation; retain evidence/rejections and export JSONL/Parquet with a dataset card. |

RAG combines retrieval with generation. Keep source chunks for precise citations;
combine claims and summaries only when that improves the selected downstream task.

**Milestone:** one knowledge base supports retrieval, a domain graph and grounded
training examples, all traceable to saved files.

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

- Define scope/schemas before shared interfaces; ingest/parse and chunk before
  structural preparation. Claims and summaries then run independently from clusters.
- Validate the chosen inputs before downstream use. Retrieval, graph and dataset
  views can use passages, claims or summaries independently, or combine them.
- Give agents bounded tasks and acceptance criteria; parallelize adapters/exporters
  after contracts stabilize.
- If estimates grow, narrow scope or defer a downstream view; retain provenance/evaluation.

Consider further formats, connectors, graph retrieval, entity matching, training,
review interfaces and deployment after release evaluation.
