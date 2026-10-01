# Knowledge Foundry — implementation plan

Build a configurable pipeline that turns document collections into structured, traceable knowledge artifacts. Follow the architectural recommendations in the [benchmark](benchmark.md), reuse existing components, and start with one domain and local files.

Effort ranges are estimates in working days, including implementation, review, and validation with agent assistance. Reassess them after testing representative documents; tasks can take different amounts of time.

## Core pipeline

Choose a reference corpus, define schemas, then ingest, parse, chunk, export, and rebuild documents.

| Task | Estimated effort | Deliverable and completion criteria |
| --- | --- | --- |
| 1. Define scope and reference corpus | 1–2 days | Select the domain, supported document classes, representative examples, and quality/cost targets. Annotate selected pages and passages for evaluation. |
| 2. Define artifact schemas | 2–4 days | Specify source, document, element, chunk, and build records with stable IDs, schema versions, source locations, and lineage. Example artifacts validate. |
| 3. Implement local ingestion | 2–3 days | Provide a package, configuration, and command-line entry point. Inventory files, detect formats, hash content, and record an outcome for every source. |
| 4. Integrate parsing and normalization | 4–7 days | Support PDF, Markdown, HTML, and DOCX using an existing parser; enable OCR for tested scans. Preserve hierarchy, tables, available locations, and metadata origins. Measure fidelity on the reference corpus. |
| 5. Clean, deduplicate, and chunk | 3–5 days | Apply conservative cleanup and exact duplicate detection. Produce configurable structural chunks with table handling, parent context, token limits, and resolvable source references. |
| 6. Export reusable artifacts | 1–2 days | Export normalized JSON, chunk JSONL, readable Markdown, and build manifests. Validate formats and reference integrity. |
| 7. Support repeat builds | 3–5 days | Cache completed stages, resume interrupted runs, and invalidate outputs when sources or processing settings change. Verify additions, updates, removals, and cleanup of stale artifacts. |

Start with a small parser adapter and a primary parser selected from the benchmark. Compare an alternative only when a required document class exposes a concrete limitation. Retain original parsed artifacts so cleaning, chunking, and enrichment can be rerun independently.

**Core milestone:** a reproducible command converts a mixed document collection into valid, traceable artifacts and handles subsequent collection changes.

## Knowledge views and downstream validation

Test downstream uses—retrieval/RAG, semantic extraction, a knowledge graph, and training data.

| Task | Estimated effort | Deliverable and completion criteria |
| --- | --- | --- |
| 8. Validate retrieval and RAG compatibility | 2–4 days | Integrate one retrieval index and evaluate expected passages against a simple baseline. Provide an optional RAG example with source citations; evaluate answer support when generation is enabled. |
| 9. Add semantic enrichment | 4–6 days | Define a small domain schema and integrate one LLM backend. Extract typed metadata, entities, and relations with evidence links, model/prompt versions, validation status, and usage costs. Review a held-out sample. |
| 10. Export a knowledge graph | 2–4 days | Build graph JSON and node/edge CSV from validated facts. Use conservative identity rules, retain ambiguous matches, and verify edge integrity and example domain queries. |
| 11. Generate training datasets | 3–5 days | Produce a small instruction/answer dataset from curated passages. Split document families before generation, preserve evidence and rejection reasons, and export JSONL/Parquet with a dataset card. Review grounding and split isolation. |

RAG (retrieval-augmented generation) combines retrieved context with model generation. It is useful here as a downstream compatibility example. The core artifacts also support search, structured extraction, graphs, and training datasets without requiring a particular retrieval approach.

**Knowledge milestone:** the same normalized corpus supports retrieval, validated semantic facts, a domain graph, and source-grounded training examples.

## Integration and release

Validate the combined workflow and make it reproducible for another user.

| Task | Estimated effort | Deliverable and completion criteria |
| --- | --- | --- |
| 12. Validate and package the workflow | 3–5 days | Run the combined pipeline on a larger representative collection, fix blocking defects, and reproduce installation in a fresh environment. Deliver usage/configuration guides, sample artifacts, tested dependencies, and a quality/cost report. |

Record parsing fidelity, reference validity, retrieval quality, factual precision, dataset grounding, runtime, memory, and inference cost where applicable. Report sample sizes and known failure classes. Use manual reference review alongside automated checks.

**Release milestone:** another user can reproduce the documented workflow and understand its supported scope and measured limitations.

## Dependencies and execution

- Complete scope and schemas before implementing shared interfaces.
- Establish ingestion and parsing before chunking and exports; validate the core before expanding knowledge views.
- Retrieval depends on chunks. Graph export depends on semantic facts. Dataset generation depends on curated passages and source lineage, and can proceed independently of the graph.
- Assign agents bounded tasks with examples and acceptance criteria. Parallelize independent adapters or exporters once their contracts are stable, and integrate small changes frequently.
- If estimates grow, reduce domain breadth or defer a downstream view while preserving provenance and evaluation.

Follow-on work includes additional formats/connectors, advanced graph retrieval, fuzzy entity matching, actual model training, review interfaces, and service deployment. Prioritize these using the release evaluation results.
