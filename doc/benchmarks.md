# Project comparisons and pilot baseline

This recovers the project survey researched on 1 October 2026 and updates its
relationship to Knowledge Foundry on 27 October 2026. It is a design comparison,
not a measured ranking of parser accuracy or throughput. Popularity statistics
are omitted; check each project's current license and service terms before use.

## Project comparison

Links point to first-party repositories. Capabilities describe the surveyed
projects, not integrations all implemented here.

| Project | Useful capability | Relationship to Knowledge Foundry |
| --- | --- | --- |
| [Docling](https://github.com/docling-project/docling) | Format conversion, OCR, layout, tables and hierarchical document output. | Direct PDF dependency behind the internal structuring provider; Foundry supplies normalization and structural chunking. |
| [Unstructured](https://github.com/Unstructured-IO/unstructured) | Partition documents into typed elements with metadata; optional chunking. | Provider-export adapter exists; no comparative pilot run yet. |
| [MinerU](https://github.com/opendatalab/MinerU) | Layout-aware PDF extraction, OCR and structured outputs/assets. | Provider-export adapter exists; no comparative pilot run yet. |
| [LlamaIndex](https://github.com/run-llama/llama_index) | Readers, transformations, nodes, indexes and retrieval integrations. | Framework comparison; distinct from the LlamaParse service, whose exports have an adapter. |
| [Haystack](https://github.com/deepset-ai/haystack) | Composable retrieval pipelines and document-store integrations. | Direct dependency in the retrieval implementation. |
| [LangChain](https://github.com/langchain-ai/langchain) | Loader, splitter, model and vector-store integrations. | Possible artifact consumer; not the pipeline orchestrator here. |
| [RAGFlow](https://github.com/infiniflow/ragflow) | Integrated parsing, knowledge-base indexing and assistant workflows. | Reference for integrated knowledge compilation and navigation. |
| [Microsoft GraphRAG](https://github.com/microsoft/graphrag) | Entity/relation extraction, graph communities and community summaries. | Design comparison; its communities differ from curated taxonomy topics. |
| [Docling Graph](https://github.com/docling-project/docling-graph) | Schema-guided extraction and graph provenance. | Design reference; Foundry's existing graph tracks artifact/evidence provenance. |
| [Data Prep Kit](https://github.com/data-prep-kit/data-prep-kit) | Composable corpus preparation transforms and scalable execution. | Reference for reusable stage boundaries and corpus processing. |
| [DataTrove](https://github.com/huggingface/datatrove) | Streaming corpus filtering, deduplication and tokenization. | Prospective training-corpus hygiene reference. |
| [Distilabel](https://github.com/argilla-io/distilabel) | Dataset generation and judging pipelines. | Prospective training/evaluation dataset reference. |

[RAPTOR](https://arxiv.org/abs/2401.18059) is a recursive synthesis reference,
not a dependency or a claim of algorithm equivalence. Foundry retains
non-overlapping, every-member similarity clustering and independent claim and
summary branches.

## Measured AML/CFT baseline

The retained [Docling run](../artifacts/20261027-docling-aml-cft-01/run.json)
reuses source-bound native conversions. It measures the current normalization,
structural chunking and automatic audits, not a fresh full-corpus parsing speed.
Results are recorded in [validation.json](../artifacts/20261027-docling-aml-cft-01/validation.json).

| Check | Result |
| --- | --- |
| Corpus | Exactly 13 documents: 11 PDFs / 801 physical pages and 2 HTML documents |
| Normalized structure | 14,700 elements; all 9,202 authoritative elements covered |
| Final structural chunks / figure assets | 4,515 / 148 |
| Development regression anchors | 38 anchors across 17 cases passed; not independent acceptance |
| Automatic audits | Parsing and chunking gates passed; parsing reported 0 errors and 506 warnings |
| Acceptance | Pending source/layout review; not accepted for downstream semantic processing |

No cross-provider accuracy ranking, cold parsing throughput, or paid-service cost
comparison has been measured. The current run does not validate regenerated
claims, summaries, PostgreSQL indexing or assistant answers.

## Comparing another provider

Reuse the same immutable ingestion snapshot and all 13 documents. Save each
provider/configuration execution under `artifacts/YYYYMMDD-provider-aml-cft-NN/`,
recording provider/version/settings, native outputs and normalized structure.
Rendering, OCR and preprocessing belong to that provider execution.

Compare source text and numbers, reading order, heading hierarchy, complete
tables/lists, figures/captions, evidence locations and structural coverage.
Inspect warnings and source/layout reviews alongside regression anchors. Measure
fresh execution time, memory and any service cost separately from replay runs.
Provider-supplied chunks may be retained when suitable; apply the same review and
acceptance requirements before comparing downstream semantic behavior.

See [provider integration](providers.md), the [pilot commands](aml-cft-pipeline.md)
and [downstream uses](downstream-uses.md).
