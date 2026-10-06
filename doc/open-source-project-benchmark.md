# Open-source project benchmark: documents to AI-ready knowledge

**Research date:** 1 October 2026. This source-based architecture review compares 12 repositories; it does not measure accuracy or throughput. Recommendations assume Knowledge Foundry will turn document collections into reusable, traceable artifacts for retrieval and model training.

## 1. Executive summary

The closest references are [RAGFlow](https://github.com/infiniflow/ragflow) for an end-to-end knowledge-base application, [Docling Graph](https://github.com/docling-project/docling-graph) for schema-guided extraction of entities and relationships, and [Data Prep Kit](https://github.com/data-prep-kit/data-prep-kit) for reusable data transforms. [Docling](https://github.com/docling-project/docling), [Unstructured](https://github.com/Unstructured-IO/unstructured), and [MinerU](https://github.com/opendatalab/MinerU) are parser candidates; [Microsoft GraphRAG](https://github.com/microsoft/graphrag) is a graph-index reference. No single project in this selection covers all desired outputs equally well.

The selected projects commonly **ingest → parse → normalize → chunk → enrich → export**. A *normalized document model* retains structure across file types; *chunks* are smaller units for search or model input. *Semantic enrichment* adds claims, entities, or relationships. *Provenance* links derived knowledge back to source locations. RAG (retrieval-augmented generation) retrieves relevant material to support an answer; fine-tuning trains a model on prepared examples. Knowledge Foundry's architecture uses a durable **claim → topic knowledge base** stage before downstream retrieval, graph, and dataset views. This recommendation is an inference across the selected projects; RAGFlow's topic-oriented Wiki is the closest direct reference.

### Selection and evidence

Projects were selected for technical relevance and coverage. Scope comes from linked first-party sources. Licenses, stars, and last push are a **GitHub metadata snapshot on 1 October 2026**; stars indicate visibility, and a recent push is only a weak activity signal. Limitations below describe documented scope, not measured failures. Hosted offerings may differ from the linked open-source code.

Table abbreviations: OCR = optical character recognition; VLM = vision-language model; ETL = extract, transform, load; SFT = supervised fine-tuning.

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

## 3. Detailed project reviews

The table covers formats, outputs, licenses, and adoption. These reviews focus on the mechanism worth studying and its main boundary.

### Parsing and normalization

**Docling** routes files through format-specific converters and preserves document hierarchy and page geometry. Its structured document is a strong parsing foundation; cross-document entity resolution and training-example governance require later stages. [Formats](https://github.com/docling-project/docling/blob/main/docs/usage/supported_formats.md), [document model](https://docling-project.github.io/docling/concepts/docling_document/).

**Unstructured** partitions files into typed elements, then chunks them by rules such as titles. This flexible element stream is useful for ingestion, although it retains less hierarchy than a full layout tree. [Partitioner](https://github.com/Unstructured-IO/unstructured/blob/main/unstructured/partition/auto.py), [chunking](https://docs.unstructured.io/api-reference/partition/chunking).

**MinerU** targets difficult page layouts through OCR and vision-language processing. It is worth testing on scanned forms and tables; inference cost and its custom license matter before adoption. [Repository](https://github.com/opendatalab/MinerU), [license](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md).

### Retrieval frameworks and applications

**LlamaIndex** turns reader output into reusable nodes through cached transformations, then writes indexes or document stores. Its property-graph option extends retrieval to entities and relationships; source fidelity still depends on the chosen reader and transformations. [Ingestion](https://developers.llamaindex.ai/python/framework-api-reference/ingestion/), [property graph](https://developers.llamaindex.ai/python/framework/module_guides/indexing/index_guide/).

**Haystack** connects converters, splitters, embedders, and writers as typed pipelines that can be serialized. It is a clear orchestration reference, but detailed page structure must be carried through from a capable converter. [Converters](https://docs.haystack.deepset.ai/docs/converters), [serialization](https://docs.haystack.deepset.ai/docs/serialization).

**LangChain** offers broad loader, splitter, and vector-store integrations. Its core text-plus-metadata document is a useful compatibility format but does not itself define a lossless document structure or domain ontology. [Document abstraction](https://reference.langchain.com/python/langchain-core/documents/base), [architecture](https://github.com/langchain-ai/langchain/blob/master/libs/README.md).

**RAGFlow** combines ingestion, domain-oriented chunking, indexing, and retrieval in one application, with graph, tree, and wiki views. Its Wiki template can organize pages by topic and use extracted claims, and its Wiki is generated at knowledge-base scope from compiled documents. This is the closest direct comparator for topic pages across sources. Assess intermediate-artifact portability before using its coupled stack as a pipeline dependency. [Wiki template](https://github.com/infiniflow/ragflow/blob/main/docs/guides/knowledge_compilation/built_in_templates_and_dedicated_configuration.md), [knowledge-base generation](https://github.com/infiniflow/ragflow/blob/main/docs/guides/knowledge_compilation/apply_knowledge_compilation_template.md).

### Graph and semantic extraction

**Microsoft GraphRAG** extracts entities and relationships from text units, clusters them into communities, and summarizes each community for graph-aware search. Its community reports are topic-like derived summaries, though their grouping follows graph communities rather than curated topic IDs. It assumes document text is already available; extraction cost and graph updates need evaluation. [Indexing model](https://github.com/microsoft/graphrag/blob/main/docs/index/default_dataflow.md), [outputs](https://microsoft.github.io/graphrag/index/outputs/).

**Docling Graph** applies domain templates and validated schemas to parsed documents, then exports graph data with source evidence. Its provenance ledger connects derived nodes to chunks and source locations; this is useful for grounding the claim layer. It is the nearest auditable graph-layer reference, but its smaller, younger project merits an integration check. [Architecture](https://github.com/docling-project/docling-graph), [provenance](https://github.com/docling-project/docling-graph/blob/main/docs/fundamentals/graph-management/provenance.md).

### Corpus and training-data pipelines

**Data Prep Kit** composes transforms for conversion, filtering, annotation, and tokenization on Python or Ray. Its batch execution model is useful for repeatable corpus builds; a rich semantic document model is outside its central contract. [Transforms](https://github.com/data-prep-kit/data-prep-kit), [repository layout](https://github.com/data-prep-kit/data-prep-kit/blob/dev/doc/repo.md).

**DataTrove** streams text corpora through filtering, deduplication, and dataset writers at scale. It fits after document parsing; its minimal text record does not represent complex layout. [Repository](https://github.com/huggingface/datatrove).

**Distilabel** generates and judges model-training or evaluation rows from prepared data. Document ingestion belongs upstream; its README notes a maintainer transition, so check release and support activity before relying on it. [README](https://github.com/argilla-io/distilabel/blob/main/README.md), [releases](https://github.com/argilla-io/distilabel/releases).

## 4. Common architecture patterns

1. **Route by format.** PDFs and scans need layout and OCR processing; HTML and email need different parsers. Record the parser and settings used. [Docling formats](https://github.com/docling-project/docling/blob/main/docs/usage/supported_formats.md).
2. **Normalize before chunking.** Preserve headings, tables, page locations, and source IDs in a common model so chunks can follow document boundaries. [Docling model](https://docling-project.github.io/docling/concepts/docling_document/), [Unstructured chunking](https://docs.unstructured.io/api-reference/partition/chunking).
3. **Compose replaceable stages.** Conversion, cleaning, extraction, indexing, and dataset writing can run or change independently. [Haystack pipelines](https://docs.haystack.deepset.ai/docs/serialization), [Data Prep Kit transforms](https://github.com/data-prep-kit/data-prep-kit).
4. **Compile grounded topics.** Extracted claims need defined fields, validation, and links to supporting passages. Group them into cross-document topic pages and review summaries and conflicts against their claims. [RAGFlow Wiki template](https://github.com/infiniflow/ragflow/blob/main/docs/guides/knowledge_compilation/built_in_templates_and_dedicated_configuration.md), [Docling Graph provenance](https://github.com/docling-project/docling-graph/blob/main/docs/fundamentals/graph-management/provenance.md).
5. **Export for several uses.** Structured documents, retrieval chunks, topic pages, graph tables, and training rows serve different consumers. Keep the knowledge records independent of any one index or model. [GraphRAG outputs](https://microsoft.github.io/graphrag/index/outputs/).

## 5. Gaps and opportunities

These are design opportunities across the selected projects, not claims that every project lacks each feature.

| Tension | Knowledge Foundry opportunity |
| --- | --- |
| Parser quality changes with document layout | Compare parsers on representative files; route failures to a fallback. |
| Retrieval records often lose structure | Preserve a versioned document model with stable IDs, hierarchy, tables, and source spans. |
| Cross-document topic pages can merge incompatible facts | Keep claims atomic, source-versioned, and reviewable; record conflicts instead of silently resolving them. |
| Indexes and training datasets have different output needs | Make both rebuildable from durable, model-neutral artifacts with lineage. |
| Quality, cost, and changes are hard to compare | Record stage versions, failures, runtime, and task-level evaluation results. |

## 6. Implications and recommendations for Knowledge Foundry

Build a document-to-knowledge pipeline whose durable outputs are portable artifacts. RAGFlow, LlamaIndex, Haystack, and LangChain are possible consumers; the other projects offer candidate parsers or patterns for graph and dataset stages. This is an architectural recommendation, not a dependency commitment.

1. **Define the artifact contract:** source version, normalized structure, chunks, grounded claims, and topics, each with source location and transformation lineage. Use portable JSON/JSONL for the pilot and add derived views as needed.
2. **Measure parser quality:** test born-digital PDFs, scans, forms, tables, HTML, and email for reading order, structure, evidence locations, throughput, and license fit. Select a default and fallback from results.
3. **Compile topics:** chunk at structural boundaries, derive atomic claims with passage evidence and generation versions, then group reviewed claims by stable topic ID across documents.
4. **Export and evaluate:** produce readable topic pages and RAG-ready chunks, then graph data and training/evaluation rows from reviewed claims. Use a small human-annotated reference set to measure parse fidelity, claim grounding, topic consistency, citation accuracy, and cost.

The first design decision is the canonical document, claim, topic, and provenance schema. It lets parsers, indexes, graph tools, and models change without losing the source-to-output trail.
