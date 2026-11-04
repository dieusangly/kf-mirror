# State of the art

Survey researched 1 October 2026; repository relationship updated 27 October.
Design comparison; accuracy/throughput are unmeasured. Links are first-party;
verify licenses/service terms before adoption.

## Document Processing and Knowledge Pipeline Tools

| Project | Useful capability | Relationship to Foundry |
| --- | --- | --- |
| [Docling](https://github.com/docling-project/docling) | OCR, layout, tables, hierarchical conversion. | Internal PDF engine; Foundry normalizes/chunks. |
| [Unstructured](https://github.com/Unstructured-IO/unstructured) | Typed partition elements, metadata, chunking. | Local PDF provider/export adapter; [measured pilots](benchmarks.md). |
| [MinerU](https://github.com/opendatalab/MinerU) | Layout/OCR, structured output/assets. | Export adapter; no pilot comparison. |
| [LlamaIndex](https://github.com/run-llama/llama_index) | Readers, nodes, transformations, indexes. | Framework reference; separate LlamaParse service has an export adapter. |
| [Haystack](https://github.com/deepset-ai/haystack) | Retrieval pipelines/document stores. | Retrieval dependency. |
| [LangChain](https://github.com/langchain-ai/langchain) | Loaders, splitters, model/store integrations. | Possible artifact consumer. |
| [RAGFlow](https://github.com/infiniflow/ragflow) | Parsing, indexing, assistant workflows. | Integrated knowledge/navigation reference. |
| [Microsoft GraphRAG](https://github.com/microsoft/graphrag) | Entities/relations, communities, summaries. | Design reference; communities differ from curated topics. |
| [Docling Graph](https://github.com/docling-project/docling-graph) | Schema-guided extraction, graph provenance. | Reference; Foundry currently tracks evidence provenance. |
| [Data Prep Kit](https://github.com/data-prep-kit/data-prep-kit) | Scalable corpus transforms. | Stage-boundary/corpus-processing reference. |
| [DataTrove](https://github.com/huggingface/datatrove) | Filtering, deduplication, tokenization. | Prospective training-corpus hygiene. |
| [Distilabel](https://github.com/argilla-io/distilabel) | Dataset generation/judging. | Prospective training/evaluation data. |

[RAPTOR](https://arxiv.org/abs/2401.18059) informs recursive synthesis, not an
implementation-equivalence claim or dependency. Foundry uses non-overlapping,
every-member similarity clustering and independent claim/summary branches.

## Reasoning Engine

| Open-source solution | Reasoning capabilities | Expected adapter effort | Main advantage |
| --- | --- | --- | --- |
| [PyOxigraph](https://github.com/oxigraph/oxigraph?utm_source=chatgpt.com) + [pySHACL](https://github.com/RDFLib/pySHACL?utm_source=chatgpt.com) | RDF, SPARQL, SHACL, OWL-RL support | Low–medium | Python integration with native Rust-backed storage |
| [Apache Jena](https://jena.apache.org/?utm_source=chatgpt.com) | RDF, SPARQL, configurable rule reasoning, SHACL | Medium | Mature, comprehensive semantic stack |
| [Ontop](https://ontop-vkg.org/?utm_source=chatgpt.com) | Virtual RDF graphs, SPARQL, RDFS/OWL 2 QL | Medium | Queries PostgreSQL without duplicating data |
| [Soufflé Datalog](https://souffle-lang.github.io/?utm_source=chatgpt.com) | Compiled rule execution, recursive deductions | Medium | Optimized execution of explicit rules |
| [Open Ontologies](https://github.com/fabio-rovai/open-ontologies?utm_source=chatgpt.com) | RDF/OWL, SHACL, rules, inference certificates, lifecycle | Medium | Integrated reasoning, governance and verification |

Foundry integrates the pinned Open Ontologies Python subproject for a small,
reviewed semantic projection. PostgreSQL/pgvector and the provenance graph remain
the extraction/retrieval system. See [Reasoning Engine](reasoning-engine.md) for
implemented capabilities, certificate boundaries and a runnable AML/CFT example.
