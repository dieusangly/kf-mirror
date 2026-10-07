# Knowledge Foundry — implementation plan

Build a configurable pipeline that turns document collections into a topic-based knowledge base with traceable claims. Preserve parsed documents and chunks as evidence-bearing intermediate artifacts. Generate retrieval, graph, and dataset views from the knowledge base. Follow the architectural recommendations in the [benchmark](open-source-project-benchmark.md), reuse existing components, and start with one domain and local files.

Effort ranges are estimates in working days, including implementation, review, and validation with agent assistance. Reassess them after testing representative documents; tasks can take different amounts of time.

## Core pipeline

Choose a reference corpus, define schemas, then ingest, parse, chunk, compile topics, export, and rebuild. The durable path is **saved source → document → parsed element → chunk → grounded claim → topic**. A topic is a stable knowledge unit that groups related supported claims across sources. Its claims retain passage-level provenance and review status.

| Task | Estimated effort | Deliverable and completion criteria |
| --- | --- | --- |
| 1. Define scope and reference corpus | 1–2 days | Select the domain, supported document classes, representative examples, and quality/cost targets. Annotate selected pages and passages for evaluation. |
| 2. Define artifact schemas | 2–4 days | Specify source, document, element, chunk, claim, topic, and build records with stable IDs, schema versions, source locations, and lineage. Example artifacts validate against the [artifact schemas](artifact-schemas.md). |
| 3. Implement local ingestion | 2–3 days | Provide a package, configuration, and command-line entry point. Inventory files, detect formats, hash content, and record an outcome for every source. |
| 4. Integrate parsing and normalization | 4–7 days | Support PDF, Markdown, HTML, and DOCX using an existing parser; enable OCR for tested scans. Preserve hierarchy, tables, available locations, and metadata origins. Measure fidelity on the reference corpus. |
| 5. Clean, deduplicate, and chunk | 3–5 days | Apply conservative cleanup and exact duplicate detection. Produce configurable structural chunks with table handling, parent context, token limits, and resolvable source references. |
| 6. Compile and export the topic knowledge base | 4–7 days | Derive evidence-linked candidate claims from chunks; use an LLM to classify them under stable topic IDs, including claims from multiple source documents. Track claim-generation and topic-classification methods/versions separately, along with separate claim grounding and classification review statuses. Export normalized JSON, chunk and claim JSONL, topic JSON/Markdown, and build manifests. Validate references and avoid presenting unreviewed synthesis as verified knowledge. |
| 7. Support repeat builds | 3–5 days | Cache completed stages, resume interrupted runs, and invalidate chunks, claims, and topics when sources or processing settings change. Verify additions, updates, removals, conflicts, and cleanup of stale artifacts. Preserve prior source editions for traceability. |

Start with a small parser adapter and a primary parser selected from the benchmark. Compare an alternative only when a required document class exposes a concrete limitation. Retain original parsed artifacts so cleaning, chunking, and topic compilation can be rerun independently. For the pilot, start with topics such as beneficial ownership and ongoing vigilance; use the annotated passages as evidence, then review synthesis against the full source context.

**Core milestone:** a reproducible command converts a mixed document collection into a traceable topic knowledge base and handles subsequent collection changes.

## Knowledge views and downstream validation

Evaluate the compiled topic knowledge base, then test retrieval/RAG, a knowledge graph, and training data as downstream uses.

| Task | Estimated effort | Deliverable and completion criteria |
| --- | --- | --- |
| 8. Validate topic knowledge quality | 2–4 days | Review a held-out sample of claims and topic summaries against source passages and context. Measure grounding, coverage, conflict handling, unsupported synthesis, and source-version accuracy; record rejection reasons and generation costs. |
| 9. Validate retrieval and RAG compatibility | 2–4 days | Index chunks and topic records, compare passage retrieval with a chunk-only baseline, and provide an optional RAG example with source citations. Evaluate answer support and topic usefulness when generation is enabled. |
| 10. Export a knowledge graph | 2–4 days | Extract typed entities and relations from reviewed claims under a small domain schema. Build graph JSON and node/edge CSV with passage evidence, model/prompt versions, and validation status. Use conservative identity rules and verify edge integrity and domain queries. |
| 11. Generate training datasets | 3–5 days | Produce a small instruction/answer dataset from reviewed claims and their passages. Split document families before generation, preserve evidence and rejection reasons, and export JSONL/Parquet with a dataset card. Review grounding and split isolation. |

RAG (retrieval-augmented generation) combines retrieved context with model generation. It is a downstream compatibility example. Keep chunks available for precise citations; a topic summary alone is not sufficient evidence for an answer. Graph and dataset outputs should be derived from reviewed claims rather than ungrounded summaries.

**Knowledge milestone:** the same topic knowledge base supports retrieval, a domain graph, and source-grounded training examples, with traceable paths back to saved files.

## Integration and release

Validate the combined workflow and make it reproducible for another user.

| Task | Estimated effort | Deliverable and completion criteria |
| --- | --- | --- |
| 12. Validate and package the workflow | 3–5 days | Run the combined pipeline on a larger representative collection, fix blocking defects, and reproduce installation in a fresh environment. Deliver usage/configuration guides, sample artifacts, tested dependencies, and a quality/cost report. |

Record parsing fidelity, reference validity, retrieval quality, factual precision, dataset grounding, runtime, memory, and inference cost where applicable. Report sample sizes and known failure classes. Use manual reference review alongside automated checks.

**Release milestone:** another user can reproduce the documented workflow and understand its supported scope and measured limitations.

## Dependencies and execution

- Complete scope and schemas before implementing shared interfaces.
- Establish ingestion and parsing before chunking and topic compilation; validate the core before downstream views.
- Topic compilation depends on chunks and elements. Retrieval can index chunks and topics; graph export and dataset generation depend on reviewed claims and source lineage. The graph and dataset stages can proceed independently.
- Assign agents bounded tasks with examples and acceptance criteria. Parallelize independent adapters or exporters once their contracts are stable, and integrate small changes frequently.
- If estimates grow, reduce domain breadth or defer a downstream view while preserving provenance and evaluation.

Follow-on work includes additional formats/connectors, advanced graph retrieval, fuzzy entity matching, actual model training, review interfaces, and service deployment. Prioritize these using the release evaluation results.
