# Open-source project benchmark: documents to AI-ready knowledge

**Research date: 1 October 2026.** Comparison of 12 repositories using first-party
sources, without accuracy/throughput measurements. Licenses, stars and last pushes
are a dated GitHub snapshot, not quality scores. Scope reflects documentation,
not tested failures; hosted products may differ.

## 1. Executive summary

Closest references: [RAGFlow](https://github.com/infiniflow/ragflow) for topic
compilation, [Docling Graph](https://github.com/docling-project/docling-graph) for
grounded graphs and [Data Prep Kit](https://github.com/data-prep-kit/data-prep-kit)
for reusable transforms. Parser candidates are [Docling](https://github.com/docling-project/docling),
[Unstructured](https://github.com/Unstructured-IO/unstructured) and
[MinerU](https://github.com/opendatalab/MinerU); [GraphRAG](https://github.com/microsoft/graphrag)
is a graph-index reference.

The shared flow is **ingest → parse → normalize → chunk → enrich → export**.
Knowledge Foundry adds durable **claim → topic** records before retrieval, graph
and dataset views. This is an architectural inference; RAGFlow Wiki is the
closest direct example. Preserve structure during normalization and source
locations through provenance.

Abbreviations: OCR = optical character recognition; VLM = vision-language model;
ETL = extract, transform, load; SFT = supervised fine-tuning.

## 2. Comparison table

| Project | Scope and architecture | Representative inputs | Knowledge representation / outputs | Extensibility and downstream fit | License; adoption / activity snapshot |
| --- | --- | --- | --- | --- | --- |
| [Docling](https://github.com/docling-project/docling) | Format-aware conversion with OCR, layout, table and optional VLM pipelines | PDF, Office, HTML, Markdown, images, audio/video and more | Hierarchical `DoclingDocument`; JSON, Markdown, HTML, text, chunk JSONL | Configurable backends and pipelines; strong parser for RAG, less of a collection-level knowledge system | MIT; ~68.2k stars; pushed 1 Oct |
| [Unstructured](https://github.com/Unstructured-IO/unstructured) | File-type partitioners → typed elements → chunkers | PDF, Office, email, HTML, XML, CSV, images, audio | Element list with metadata; JSON/NDJSON serialization | Custom partitioners and chunk strategies; good ETL boundary, graph/training semantics left downstream | Apache-2.0; ~15.5k; 30 Sep |
| [MinerU](https://github.com/opendatalab/MinerU) | OCR/VLM-centered complex document parser | PDF, images, DOCX, PPTX, XLSX; other inputs documented in package | Layout-aware Markdown and JSON plus assets | Useful parser alternative for difficult layouts; limited collection orchestration | **Custom MinerU license**; ~80.9k; 30 Sep |
| [LlamaIndex](https://github.com/run-llama/llama_index) | Readers → transformations → nodes → indexes / stores | Files and data sources through readers; local reader supports PDF, Office, images, media | `Document`/`Node`, metadata and relationships; vector and property-graph indexes | Broad model/store integrations, metadata extractors, cache and docstore; application-oriented framework | MIT; ~52.4k; 29 Sep |
| [Haystack](https://github.com/deepset-ai/haystack) | Typed component graphs for indexing and querying | PDF, Office, images, text, many converters/integrations | `Document` objects in document stores; serializable YAML pipelines | Custom components, embedders and stores; strong orchestration, limited canonical layout model | Apache-2.0; ~26.6k; 1 Oct |
| [LangChain](https://github.com/langchain-ai/langchain) | Loader / splitter / embedding / vector-store ecosystem | Broad connector and loader ecosystem | Text `Document` + metadata; retrieval indexes via integrations | Largest integration surface; provenance and document semantics depend on chosen loaders | MIT; ~147.3k; 1 Oct |
| [RAGFlow](https://github.com/infiniflow/ragflow) | Knowledge-base application: parsers → chunkers → enrichment → indexes → chat/agents | PDF, Office, tables, images, email, HTML/Markdown, media through pipeline | Searchable chunks, metadata, knowledge compilation including graph/tree/wiki views | Configurable domain chunk templates, parsers, models and search engines; more operationally coupled | Apache-2.0; ~91.6k; 1 Oct |
| [Microsoft GraphRAG](https://github.com/microsoft/graphrag) | Text-unit indexing → entity/relationship extraction → communities → summaries | TXT, CSV, JSON/JSONL, Parquet; MarkItDown input option | Document, text-unit, entity, relationship, claim and community tables; Parquet + vectors | Configurable prompts/workflows; graph-centric retrieval, parsing usually upstream | MIT; ~36.2k; 28 Sep |
| [Docling Graph](https://github.com/docling-project/docling-graph) | Docling conversion → schema-guided extraction → validated graph | Docling-supported files, URLs, Docling JSON/DocLang | Pydantic models; graph JSON, node/edge CSV, Cypher, provenance ledger | Domain templates/ontologies, local VLM or LiteLLM backends; young project | MIT; ~910; 24 Sep |
| [Data Prep Kit](https://github.com/data-prep-kit/data-prep-kit) | Composable transforms with Python/Ray execution | Docling, HTML/web, code ZIP; Parquet, JSONL/NDJSON | Tabular corpus artifacts, annotations, tokenized data | Custom transforms and scalable jobs; RAG and fine-tuning preparation, no unified semantic graph | Apache-2.0; ~966; 29 Sep |
| [DataTrove](https://github.com/huggingface/datatrove) | Streaming reader/filter/dedup/tokenizer/writer blocks | Text corpora, WARC, Arrow/Parquet, Hugging Face datasets | `Document(text, id, metadata)`; JSONL/Parquet/HF dataset writers | Custom blocks, local/Slurm/Ray execution; excellent pretraining corpus hygiene, not layout parsing | Apache-2.0; ~3.4k; 30 Sep |
| [Distilabel](https://github.com/argilla-io/distilabel) | Generator/judge pipelines over existing datasets | Rows/Hugging Face datasets, prompts and model outputs | `Distiset` / Hugging Face dataset rows for SFT, preference and evaluation data | Model-provider and task components, Argilla export; source-document ingestion upstream | Apache-2.0; ~3.4k; 28 Sep; maintainer transition noted in README |

Sources for snapshot fields: the [GitHub repository API](https://docs.github.com/en/rest/repos/repos#get-a-repository) for each linked repository. MinerU's repository metadata reports `NOASSERTION`; the [license text](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md) adds commercial-threshold and online-service attribution terms to Apache 2.0. Treat it as a custom license when evaluating dependencies.

## 3. Project mechanisms and limits

### Parsing and normalization

**Docling:** format-specific conversion preserves hierarchy and page geometry; cross-document knowledge needs later stages. [Formats](https://github.com/docling-project/docling/blob/main/docs/usage/supported_formats.md), [model](https://docling-project.github.io/docling/concepts/docling_document/).

**Unstructured:** typed elements and rule-based chunking offer a flexible ingestion boundary, with less hierarchy than a layout tree. [Partitioner](https://github.com/Unstructured-IO/unstructured/blob/main/unstructured/partition/auto.py), [chunking](https://docs.unstructured.io/api-reference/partition/chunking).

**MinerU:** OCR and vision processing target scans and complex layouts; measure inference cost and check its custom license. [Repository](https://github.com/opendatalab/MinerU), [license](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md).

### Retrieval frameworks and applications

**LlamaIndex:** cached transformations build reusable nodes and indexes, including property graphs. Source fidelity depends on readers. [Ingestion](https://developers.llamaindex.ai/python/framework-api-reference/ingestion/), [property graph](https://developers.llamaindex.ai/python/framework/module_guides/indexing/index_guide/).

**Haystack:** serializable typed pipelines are an orchestration reference; page structure depends on converters. [Converters](https://docs.haystack.deepset.ai/docs/converters), [serialization](https://docs.haystack.deepset.ai/docs/serialization).

**LangChain:** broad integrations use a text-plus-metadata document; structure and ontology depend on the loader and application. [Document model](https://reference.langchain.com/python/langchain-core/documents/base), [architecture](https://github.com/langchain-ai/langchain/blob/master/libs/README.md).

**RAGFlow:** its knowledge-base Wiki can organize extracted claims into cross-document topic pages. It is the closest topic comparator; assess artifact portability before adopting the coupled application stack. [Wiki template](https://github.com/infiniflow/ragflow/blob/main/docs/guides/knowledge_compilation/built_in_templates_and_dedicated_configuration.md), [generation](https://github.com/infiniflow/ragflow/blob/main/docs/guides/knowledge_compilation/apply_knowledge_compilation_template.md).

### Graph and semantic extraction

**Microsoft GraphRAG:** clusters extracted entities and relationships into communities with summaries. These resemble topics but follow graph communities, not curated IDs; parsing is upstream. [Indexing](https://github.com/microsoft/graphrag/blob/main/docs/index/default_dataflow.md), [outputs](https://microsoft.github.io/graphrag/index/outputs/).

**Docling Graph:** templates and schemas extract graph records from parsed files; its provenance ledger connects nodes to chunks and locations. Check integration maturity. [Architecture](https://github.com/docling-project/docling-graph), [provenance](https://github.com/docling-project/docling-graph/blob/main/docs/fundamentals/graph-management/provenance.md).

### Corpus and training-data pipelines

**Data Prep Kit:** Python/Ray transforms support repeatable corpus builds; semantic document structure is outside its central contract. [Transforms](https://github.com/data-prep-kit/data-prep-kit), [layout](https://github.com/data-prep-kit/data-prep-kit/blob/dev/doc/repo.md).

**DataTrove:** scalable text filtering, deduplication, and writing fit after parsing; its text record lacks layout. [Repository](https://github.com/huggingface/datatrove).

**Distilabel:** generates and judges training/evaluation rows from prepared data. Ingestion is upstream; check activity after the README's maintainer-transition note. [README](https://github.com/argilla-io/distilabel/blob/main/README.md), [releases](https://github.com/argilla-io/distilabel/releases).

## 4. Common architecture patterns

1. **Route by format:** use appropriate PDF/scan, HTML and email parsers; record
   parser/settings.
2. **Normalize before chunking:** preserve hierarchy, tables, pages, source IDs
   and boundaries in a common model.
3. **Separate replaceable stages:** conversion, cleaning, extraction, indexing
   and dataset writing should run/change independently.
4. **Compile grounded topics:** validate claim fields/evidence; group across
   documents and review summaries/conflicts against the underlying claims.
5. **Export portable views:** documents, chunks, topics, graphs and training rows
   serve different consumers; keep records independent of an index/model.

## 5. Gaps and opportunities

Design opportunities across the selection, not universal missing features:

| Tension | Knowledge Foundry response |
| --- | --- |
| Layout-dependent parser quality | Benchmark representative files and route failures to a fallback. |
| Structure lost in retrieval records | Preserve versioned structure, IDs, hierarchy, tables and spans. |
| Incompatible facts merged in topics | Keep atomic, versioned, reviewable claims; record conflicts explicitly. |
| Different index/training needs | Rebuild both from durable, model-neutral artifacts with lineage. |
| Hard-to-compare quality/cost/changes | Record stage versions, failures, runtime and task-level evaluations. |

## 6. Recommendations for Knowledge Foundry

Use portable artifacts as the contract; RAGFlow, LlamaIndex, Haystack and LangChain
are possible consumers. Other projects supply parser, graph or dataset patterns.
These are architectural options, not dependency commitments.

1. Define canonical document/claim/topic/provenance schemas, source versions,
   locations and transformation lineage. Start with JSON/JSONL; derive other views.
2. Benchmark born-digital PDFs, scans, forms, tables, HTML and email for fidelity,
   reading order, locations, throughput and license fit; select default/fallback parsers.
3. Chunk structurally, generate atomic passage-linked/versioned claims, then
   group reviewed claims across documents under stable topic IDs.
4. Export readable topic pages/RAG chunks, followed by reviewed graph and training/
   evaluation data. Use human annotations to measure parsing, grounding, topic
   consistency, citation accuracy and cost.

The canonical contract keeps parsers, indexes and models replaceable without
losing the source-to-output trail.

## 7. What Knowledge Foundry uses and adapts

Implementation mapping checked on **16 October 2026**. The survey above describes
external projects; this table distinguishes actual use from design comparisons.
Similarity alone does not establish historical influence or copied code.

| Project | Relationship | Knowledge Foundry implementation |
| --- | --- | --- |
| **Docling** | Direct PDF-parsing dependency, pinned in [requirements](../requirements.txt). Its [document model](https://docling-project.github.io/docling/concepts/docling_document/) provides structure and provenance. | [PDF conversion](../src/knowledge_foundry/parsers.py) uses `DocumentConverter` for text, layout, tables and configured OCR. Normalization, [original chunking](../src/knowledge_foundry/chunking.py) and [structural assembly](../src/knowledge_foundry/structural_chunks.py) are custom; we do not use Docling's chunker. |
| **RAPTOR** | Conceptual comparison, not a dependency or implementation of its [recursive embedding/clustering/summarization tree](https://arxiv.org/abs/2401.18059). | We also group evidence using embeddings before generation. Our [clustering](../src/knowledge_foundry/semantic_clusters.py) is deterministic, nonoverlapping and constrained by every member's cosine similarity. Clusters produce atomic candidate claims, without recursive summaries or a retrieval tree. |
| **Docling Graph** | Design reference in this survey: [source-linked graph provenance](https://docling-project.github.io/docling-graph/fundamentals/graph-management/provenance/). No integration. | Our [artifact contract](artifact-schemas.md) preserves original chunk IDs and exact source/element spans; [candidate-claim validation](../src/knowledge_foundry/claim_generation_and_classification.py) resolves evidence labels against saved passages. These are local implementations. |
| **Other surveyed frameworks** | RAGFlow, LlamaIndex, Haystack, LangChain, GraphRAG and the corpus/dataset tools above remain comparison references or possible consumers. | No framework integration is implemented. Our saved JSON/JSONL artifacts, curated topics, numerical clustering, cache and review workflow remain repository-owned components. |

The implemented flow is **original chunks → structural chunks → semantic clusters
→ claims → topics**. Structural assembly uses document structure; semantic
clustering uses embeddings. These stages serve different purposes.
