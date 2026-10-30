# State of the art

Survey researched 1 October 2026; repository relationship updated 27 October.
Design comparison; accuracy/throughput are unmeasured. Links are first-party;
verify licenses/service terms before adoption.

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
