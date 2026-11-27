# File migration map

This ledger covers all 242 tracked paths at `940d65209827c1032f15d789189cb12cdd5fd5c7`. Each current path appears once in the first column. Paths are repository-relative. **=** keeps the current physical path. **= +** keeps it and assigns selected responsibilities to the named package files. A destination without **=** is the intended implementation home. For the 29 relocated top-level modules, the package initializer maps the historical import name to the implementation in the destination column. For the frozen engine, formalizer and evaluation sources, **=** includes the original bytes. The destination column now describes the Stage A2 implementation. The dependency column records A1 imports. The four frozen review modules have forwarding imports at their destination and retain their original implementations. The pinned pilot dataset validator and formalizer also retain their original implementations. The evaluation successor shares its construction logic in `workflows/assistant_evaluation/construction.py`. See the [step records](repository-migration-progress.md) for verification and file-by-file patches.

The code tables give callable or command examples and direct in-repository dependencies, including function-local imports. Bare module names in the dependency column are relative to `knowledge_foundry`. A dash means no direct internal Python import was found. External libraries, file reads, indirect imports and subprocess interactions are described in the [architecture](architecture.md). The [plan](repository-migration-plan.md) defines the sequence and when compatibility facades are appropriate.

## Package modules

| Current path | Target path | Responsibility and interface | Direct dependencies |
| --- | --- | --- | --- |
| `src/knowledge_foundry/__init__.py` | = | Package namespace and lazy lookup for historical module names. | — |
| `src/knowledge_foundry/assistant/__init__.py` | = | Assistant package namespace. `package marker` | — |
| `src/knowledge_foundry/assistant/cases.py` | = | Build a fresh symbolic case from a policy and cited user assertions only. `build_case, InterpretationError` | `assistant.datasets` |
| `src/knowledge_foundry/assistant/config.py` | = | Configuration for independently replaceable language-model roles. `load_config, local_endpoint` | — |
| `src/knowledge_foundry/assistant/datasets.py` | = + `src/knowledge_foundry/provenance/reasoning_packages.py`, `src/knowledge_foundry/assistant/policies.py`, `src/knowledge_foundry/reasoning_engine/runtime.py` | Validate accepted package receipts, project BO/KYC policies, load the engine by path and dispatch replay/fresh cases. `DatasetRegistry, accepted_pins, accepted_query_path, resolve_passages` | `assistant.config`, `assistant.search`, `scripts.aml_cft_reasoning (by pinned path)` |
| `src/knowledge_foundry/assistant/diagnostics.py` | = | Bounded exception structure without messages, payloads, URLs or local values. `exception_diagnostic` | — |
| `src/knowledge_foundry/assistant/llm.py` | = | Bounded structured requests to hosted or local Responses-compatible servers. `LanguageModels, ModelError, ROUTE_SCHEMA` | `assistant.config`, `assistant.diagnostics`, `embeddings`, `openai_tools` |
| `src/knowledge_foundry/assistant/search.py` | = | Cached policy embeddings and exact cosine ranking, without customer assertions. `ScenarioSearch.search, ScenarioEmbeddings` | `assistant.config`, `assistant.llm`, `embeddings`, `semantic_clusters`, `structural_chunks` |
| `src/knowledge_foundry/assistant/service.py` | = | Evidence-aware orchestration with terminal unknown/conflict/error outcomes. `Assistant.ask, DocumentEvidence` | `assistant.cases`, `assistant.config`, `assistant.datasets`, `assistant.diagnostics`, `assistant.llm`, `retrieval.answering`, `retrieval.config`, `retrieval.service`, `storage.postgres` |
| `src/knowledge_foundry/chunking.py` | `src/knowledge_foundry/knowledge_engineering/structuring/chunking.py` + `src/knowledge_foundry/knowledge_engineering/structuring/chunk_text.py` | Deterministic structural chunking of saved parsing artifacts (schema v1.0.0). `build_collection, main` | `document_structure`, `document_structuring`, `pdf_tables`, `scripts.validate_artifact_examples` |
| `src/knowledge_foundry/chunking_audit.py` | `src/knowledge_foundry/knowledge_engineering/review/chunking_audit.py` | Evaluate saved chunks against parsed evidence and prepare source-linked review samples. `evaluate` | `chunking`, `document_structuring`, `review`, `scripts.validate_artifact_examples` |
| `src/knowledge_foundry/claim_context.py` | `src/knowledge_foundry/knowledge_engineering/semantic/claim_context.py` + `src/knowledge_foundry/knowledge_engineering/claims/request_contract.py` | Freeze semantic preparation and its claim-request sizing, then validate candidate ancestry. `prepare_claim_context, load_preparation, validate_candidate_lineage` | `claim_generation_and_classification`, `claim_source_context`, `concurrency`, `document_structure`, `document_structuring`, `embeddings`, `generation_context`, `openai_tools`, `semantic_clusters`, `structural_chunks`, `scripts.validate_artifact_examples` |
| `src/knowledge_foundry/claim_generation_and_classification.py` | `src/knowledge_foundry/knowledge_engineering/claims/claim_generation_and_classification.py` + `src/knowledge_foundry/knowledge_engineering/claims/request_contract.py` | Generate grounded candidate claims and classify them into draft topics. `generate_claims, classify_claims, FixtureProvider, OpenAIProvider` | `claim_context`, `claim_source_context`, `classification_reuse`, `concurrency`, `evidence`, `generation_context`, `openai_tools`, `structural_chunks`, `structural_links`, `scripts.validate_artifact_examples` |
| `src/knowledge_foundry/claim_source_context.py` | `src/knowledge_foundry/knowledge_engineering/claims/claim_source_context.py` | Located citation support from immutable chunks, without changing membership. `source_context_index, supporting_passages` | `evidence` |
| `src/knowledge_foundry/classification_reuse.py` | `src/knowledge_foundry/knowledge_engineering/claims/classification_reuse.py` | Validate prior classification outcomes and persist per-candidate progress. `load_reuse, ClassificationJournal` | `claim_generation_and_classification`, `scripts.deterministic_claim_review` |
| `src/knowledge_foundry/concurrency.py` | `src/knowledge_foundry/shared/concurrency.py` | Run a bounded number of independent requests with coordinator-owned results. `validate_workers, run_bounded` | — |
| `src/knowledge_foundry/corpus.py` | `src/knowledge_foundry/knowledge_engineering/corpus.py` | Active corpus scope and explicitly accepted, source-specific limitations. `readiness` | `ingestion` |
| `src/knowledge_foundry/document_structure.py` | `src/knowledge_foundry/knowledge_engineering/structuring/document_structure.py` | Document hierarchy assembled from parser metadata and conservative source flow. `assemble_document_hierarchy, ancestor_ids, validate_structure` | — |
| `src/knowledge_foundry/document_structuring.py` | `src/knowledge_foundry/knowledge_engineering/structuring/document_structuring.py` | Freeze, review and accept structural output before semantic processing. `freeze_structure, validate_frozen, accept_structure, load_validated_structure` | `chunking`, `document_structure`, `evidence`, `structural_chunks`, `scripts.validate_artifact_examples` |
| `src/knowledge_foundry/document_structuring_review.py` | `src/knowledge_foundry/knowledge_engineering/review/document_structuring_review.py` | Deterministic prepare/finalize phases around source inspection by a reviewer. `review_structure, main` | `chunking_audit`, `document_structuring`, `parsing_audit`, `parsing_review` |
| `src/knowledge_foundry/embeddings.py` | `src/knowledge_foundry/shared/embeddings.py` | Small bounded embedding adapter and content-addressed persistent cache. `EmbeddingAdapter.embed, token_count, atomic_json` | `semantic_clusters`, `structural_chunks` |
| `src/knowledge_foundry/evidence.py` | `src/knowledge_foundry/provenance/evidence.py` | Resolve exact claim passages without changing saved chunk references. `claim_evidence_references, evidence_key` | — |
| `src/knowledge_foundry/generation_context.py` | `src/knowledge_foundry/knowledge_engineering/claims/generation_context.py` + `src/knowledge_foundry/shared/request_counts.py` | Exact generation request construction and cached provider token counts. `generation_payload, RequestCounter` | `embeddings`, `openai_tools`, `structural_links` |
| `src/knowledge_foundry/ingestion.py` | `src/knowledge_foundry/knowledge_engineering/ingestion.py` | Validate sources and persist reusable, content-addressed original bytes. `ingest, load_ingested, main` | — |
| `src/knowledge_foundry/openai_tools.py` | `src/knowledge_foundry/shared/openai_tools.py` | Minimal OpenAI Responses API client for structured claim outputs. `OpenAIResponsesTool, response_payload` | — |
| `src/knowledge_foundry/parsers.py` | `src/knowledge_foundry/knowledge_engineering/structuring/parsers.py` | Format adapters producing source-located blocks for parsing. `parse_html, parse_markdown, parse_docx, parse_pdf` | `pdf_source`, `pdf_structure`, `pdf_tables` |
| `src/knowledge_foundry/parsing.py` | `src/knowledge_foundry/knowledge_engineering/structuring/parsing.py` + `src/knowledge_foundry/knowledge_engineering/structuring/provider_contract.py` | Parse verified saved sources into v1 artifacts and a loss-aware sidecar. `parse_collection, main` | `ingestion`, `parsers`, `structure_providers` |
| `src/knowledge_foundry/parsing_audit.py` | `src/knowledge_foundry/knowledge_engineering/review/parsing_audit.py` | Audit normalized parsing artifacts and source-checked landmarks. `evaluate, audit_document` | `parsers`, `parsing`, `pdf_source`, `pdf_structure`, `pdf_tables`, `provider_exports`, `scripts.validate_artifact_examples` |
| `src/knowledge_foundry/parsing_review.py` | `src/knowledge_foundry/knowledge_engineering/review/parsing_review.py` | Select source-linked parsing review samples and score judgments. `prepare, seed` | `review` |
| `src/knowledge_foundry/pdf_source.py` | `src/knowledge_foundry/knowledge_engineering/structuring/pdf_source.py` | Source-evidenced corrections to native PDF structure, replayed by the audit. `adapt_native` | `pdf_structure`, `pdf_tables` |
| `src/knowledge_foundry/pdf_structure.py` | `src/knowledge_foundry/knowledge_engineering/structuring/pdf_structure.py` | Repair PDF text flow using page geometry and ordering. `repair_footnotes, order_single_column_pages, restore_numeric_identifiers` | `pdf_tables` |
| `src/knowledge_foundry/pdf_tables.py` | `src/knowledge_foundry/knowledge_engineering/structuring/pdf_tables.py` | Repair Docling table structure using source geometry. `table_row_map, context_for_spans, link_table_continuations` | — |
| `src/knowledge_foundry/provider_exports.py` | `src/knowledge_foundry/knowledge_engineering/structuring/provider_exports.py` | Adapt exported PDF responses and retain their native payloads for review. `adapt_export` | `parsers`, `structure_providers` |
| `src/knowledge_foundry/retrieval/__init__.py` | = | Evidence retrieval and document-assistant application service. `telemetry default on import` | — |
| `src/knowledge_foundry/retrieval/answering.py` | = | Bounded answer requests, streamed drafts and fail-closed citation validation. `generate, validate_answer, request_payload` | `embeddings`, `openai_tools`, `retrieval.config`, `structural_chunks` |
| `src/knowledge_foundry/retrieval/artifacts.py` | = + `src/knowledge_foundry/retrieval/adapters/engineering.py` | Read accepted engineering outputs and project searchable records, original memberships and evidence. `load_artifacts, project, profile, evidence_id` | `claim_context`, `embeddings`, `evidence`, `structural_chunks`, `summaries`, `scripts.validate_artifact_examples` |
| `src/knowledge_foundry/retrieval/checkpoint.py` | = | Durable per-case retrieval results, bound to one evaluation's inputs. `EvaluationCheckpoint` | `embeddings`, `structural_chunks` |
| `src/knowledge_foundry/retrieval/config.py` | = | Small validated runtime configuration, independent of clustering settings. `load_config, corpora` | — |
| `src/knowledge_foundry/retrieval/context.py` | = | Admit ranked evidence spans with their complete governing context. `assemble_context` | `retrieval.answering` |
| `src/knowledge_foundry/retrieval/evaluation.py` | = | Separate retrieval smoke metrics from mechanical answer and support checks. `evaluate, audit_answer, SnapshotEvaluationStore` | `concurrency`, `retrieval.answering`, `retrieval.checkpoint`, `structural_chunks` |
| `src/knowledge_foundry/retrieval/indexing.py` | = | Import verified artifacts into a search snapshot. `index_artifacts` | `embeddings`, `retrieval.artifacts`, `retrieval.config` |
| `src/knowledge_foundry/retrieval/pipeline.py` | = | Haystack pipeline for scoped retrieval, provenance resolution and context. `retrieval_pipeline, SnapshotRetriever, EvidenceResolver` | `retrieval.context`, `retrieval.ranking` |
| `src/knowledge_foundry/retrieval/provenance.py` | = + `src/knowledge_foundry/provenance/graph_lookup.py` | Resolve a snapshot to a trusted local graph using configuration and receipt checks. `graph_path` | `retrieval.artifacts`, `retrieval.config` |
| `src/knowledge_foundry/retrieval/query.py` | = | Bounded, language-independent conversational query rewriting. `rewrite_query` | `embeddings`, `openai_tools`, `retrieval.answering`, `retrieval.config`, `structural_chunks` |
| `src/knowledge_foundry/retrieval/ranking.py` | = | Bounded relevance selection over snapshot-scoped search candidates. `rank_candidates` | `embeddings`, `openai_tools`, `retrieval.answering`, `retrieval.config`, `structural_chunks` |
| `src/knowledge_foundry/retrieval/service.py` | = | Reusable application boundary shared by the chatbot, CLIs and evaluation. `DocumentAssistant.retrieve/stream/ask` | `embeddings`, `retrieval.answering`, `retrieval.artifacts`, `retrieval.config`, `retrieval.pipeline`, `retrieval.query` |
| `src/knowledge_foundry/review.py` | `src/knowledge_foundry/knowledge_engineering/review/review.py` | Shared deterministic scoring for source-reviewed chunk and claim samples. `apply_reviews, select_samples, read_review_policy` | — |
| `src/knowledge_foundry/semantic_clusters.py` | `src/knowledge_foundry/knowledge_engineering/semantic/semantic_clusters.py` + `src/knowledge_foundry/shared/vectors.py` | All-members clustering plus shared vector validation and cosine operations. `normalized, cosine, cluster_members, refine_clusters` | — |
| `src/knowledge_foundry/storage/__init__.py` | = | Storage adapter namespace. `package marker` | — |
| `src/knowledge_foundry/storage/postgres.py` | = | Transactional snapshot storage and session-scoped conversation persistence. `PostgresStore.migrate/import_snapshot/publish/search/history/feedback` | `semantic_clusters`, `structural_chunks` |
| `src/knowledge_foundry/structural_chunks.py` | `src/knowledge_foundry/knowledge_engineering/structuring/structural_chunks.py` | Focused structural chunks with explicit, non-merging document context. `assemble_structural_chunks` | `document_structure`, `evidence`, `structural_links` |
| `src/knowledge_foundry/structural_links.py` | `src/knowledge_foundry/knowledge_engineering/structuring/structural_links.py` + `src/knowledge_foundry/shared/hashing.py` | Detect document-local links and expose canonical JSON hashing used across the repository. `detect_links, stable_hash` | `evidence` |
| `src/knowledge_foundry/structure_providers.py` | `src/knowledge_foundry/knowledge_engineering/structuring/structure_providers.py` + `src/knowledge_foundry/knowledge_engineering/structuring/provider_contract.py` | Provider boundary for Document Structuring, independent of acquisition. `ProviderResult, StructureProvider, resolve_provider` | `parsers`, `parsing`, `provider_exports`, `configured module:factory` |
| `src/knowledge_foundry/summaries.py` | `src/knowledge_foundry/knowledge_engineering/summaries/summaries.py` | Independent, bounded recursive summaries of frozen semantic clusters. `generate_summaries, load_summaries` | `claim_context`, `claim_generation_and_classification`, `concurrency`, `embeddings`, `evidence`, `generation_context`, `openai_tools`, `semantic_clusters`, `structural_chunks` |
| `src/knowledge_foundry/unstructured_provider.py` | `src/knowledge_foundry/knowledge_engineering/structuring/unstructured_provider.py` | Partition PDFs locally with Unstructured. `Provider.structure` | `provider_exports` |
| `src/knowledge_foundry/workflow.py` | `src/knowledge_foundry/workflows/document_pipeline.py` | Run the document-to-publication stage graph, reviewers, invalidation, budgets and recovery. `Workflow, execute, main` | `corpus`, `document_structuring`, `ingestion`, `workflow_reporting`, `script subprocesses and reviewer CLI` |
| `src/knowledge_foundry/workflow_reporting.py` | `src/knowledge_foundry/workflows/reporting.py` | Derived run summaries using explicit stage telemetry, never projected build costs. `build_report` | `claim_context`, `claim_generation_and_classification` |

## Commands

| Current path | Target path | Responsibility and interface | Direct dependencies |
| --- | --- | --- | --- |
| `scripts/accept_structure.py` | = | Accept source-reviewed Document Structuring output. `main` | `document_structuring` |
| `scripts/aml_cft_formalization.py` | = + `src/knowledge_foundry/workflows/reasoning_pilot/formalization.py` | Map readable inputs and questions to local symbolic records. `verify, encode, queries, compare` | `scripts.aml_cft_reasoning` |
| `scripts/aml_cft_reasoning.py` | = + `src/knowledge_foundry/reasoning_engine/runtime.py` | Local symbolic inference and proof verification, version 1.0.0. `validate-selection, verify-setup, validate, execute, check` | — |
| `scripts/build_semantic_clusters.py` | = | Cluster validated structural chunks, saving embeddings and provenance. `--chunked-dir, --output-dir` | `claim_context` |
| `scripts/chunk.py` | = | Run structural chunking directly from a repository checkout. `--input-dir, --output-dir` | `chunking` |
| `scripts/classify_claims.py` | = | Classify saved candidate claims into curated topic records. `--candidates-dir, --output-dir, --resume, --reuse-classification-from` | `claim_generation_and_classification` |
| `scripts/compare_structuring.py` | = + `src/knowledge_foundry/knowledge_engineering/review/comparison.py` | Compare two frozen provider runs against the same original PDF text layer. `compare, main` | `document_structuring`, `scripts.validate_pilot` |
| `scripts/deterministic_claim_review.py` | = + `src/knowledge_foundry/knowledge_engineering/review/claim_review.py`, `src/knowledge_foundry/knowledge_engineering/claims/saved_classification.py` | Evaluate saved claim generation and classification artifacts and prepare source-linked review samples. No API calls. `prepare, finalize, evaluate` | `claim_generation_and_classification`, `review`, `scripts.validate_artifact_examples` |
| `scripts/deterministic_document_structuring_review.py` | = | Prepare deterministic checks, then finalize source-reviewed Document Structuring. `prepare, finalize` | `document_structuring_review` |
| `scripts/evaluate_reasoning_assistant_inputs.py` | = + `src/knowledge_foundry/workflows/assistant_evaluation/execution.py` | Version 1 natural-language evaluation runner. No references/queries enter Assistant.ask. `inventory by default, --execute, --agreement, --resume` | `assistant.config`, `assistant.datasets`, `assistant.service`, `scripts.generate_reasoning_assistant_evaluation`, `scripts.validate_reasoning_assistant_evaluation` |
| `scripts/evaluate_retrieval.py` | = | Compare retrieval methods/modes without generating answers or upstream data. `--methods, --modes, --output` | `embeddings`, `retrieval.config`, `retrieval.evaluation`, `retrieval.service`, `storage.postgres` |
| `scripts/generate_claims.py` | = | Generate durable evidence-linked candidate claims from a saved chunk bundle. `--context-dir, --output-dir, --resume, --retry-failed-from` | `claim_generation_and_classification` |
| `scripts/generate_reasoning_assistant_evaluation.py` | = + `src/knowledge_foundry/workflows/assistant_evaluation/preparation.py` | Reproduce Stage 1 v1 questions and engine-checked provisional references offline. `generate, --output` | `assistant.datasets`, `scripts.validate_reasoning_assistant_evaluation` |
| `scripts/generate_summaries.py` | = | Generate recursive summaries directly from frozen semantic clusters. `--context-dir, --output-dir, --cache-dir` | `summaries` |
| `scripts/index_artifacts.py` | = | Thin downstream import, migration and publication CLI. `migrate, import, publish, list` | `retrieval.config`, `retrieval.indexing`, `storage.postgres` |
| `scripts/ingest.py` | = | Run ingestion directly from a repository checkout. `--config, --output-dir, --fetch` | `ingestion` |
| `scripts/parse.py` | = | Run parsing directly from a repository checkout. `--config, --profile, --ingested-dir, --output-dir` | `parsing` |
| `scripts/query.py` | = | Query a published snapshot using the same service as the chatbot. `--corpus, --method, --mode` | `retrieval.config`, `retrieval.evaluation`, `retrieval.service`, `storage.postgres` |
| `scripts/render_knowledge_graph.py` | = + `src/knowledge_foundry/provenance/graph.py`, `src/knowledge_foundry/applications/kpg_viewer/render.py` | Render one artifact bundle as a standalone interactive provenance graph. `build_graph, render_html, --bundle, --output` | `summaries`, `apps/kpg_viewer assets` |
| `scripts/replay_reasoning_pilot.py` | = + `src/knowledge_foundry/workflows/reasoning_pilot/replay.py` | Execute accepted queries and compare full results and proof checks. `--registry, --cases, --output` | `assistant.datasets` |
| `scripts/validate_aml_cft_reasoning_dataset.py` | = + `src/knowledge_foundry/workflows/reasoning_pilot/readable_dataset.py` | Validate the Stage 1 readable dataset's structure, coverage and hashes. `--manifest, --output` | — |
| `scripts/validate_artifact_examples.py` | = + `src/knowledge_foundry/provenance/bundle.py` | Validate artifact bundles with jsonschema and cross-record checks. `check(bundle), positional example paths` | `evidence` |
| `scripts/validate_pilot.py` | = + `src/knowledge_foundry/knowledge_engineering/review/pilot.py` | Validate the 13-document pilot's frozen structuring output without model calls. `--run-dir, --output` | `document_structure`, `document_structuring`, `embeddings`, `ingestion` |
| `scripts/validate_reasoning_assistant_evaluation.py` | = + `src/knowledge_foundry/workflows/assistant_evaluation/validation.py` | Validate v1 dataset structure, provenance, engine certificates and contrasts offline. `--dataset, --output` | `assistant.datasets`, `scripts.generate_reasoning_assistant_evaluation` |
| `scripts/workflow.py` | = | Run, inspect, resume or explicitly publish the persisted pipeline workflow. `run, step, status, resume, report, publish` | `workflow` |

## Applications

| Current path | Target path | Responsibility and interface | Direct dependencies |
| --- | --- | --- | --- |
| `apps/assistant/app.py` | = + `src/knowledge_foundry/applications/assistant/ui.py` | Unified chat over accepted reasoning cases, document evidence and general knowledge. `main, EXAMPLES` | `apps.assistant.document_search`, `assistant.config`, `assistant.service` |
| `apps/assistant/document_search.py` | = + `src/knowledge_foundry/applications/assistant/document_search.py` | Direct document search over published snapshots in the unified assistant. `render_document_search, show_graph` | `retrieval.config`, `retrieval.provenance`, `retrieval.service`, `storage.postgres` |
| `apps/assistant/style.css` | = | Streamlit stylesheet loaded at runtime | — |
| `apps/contributors/README.md` | = | Launch, accounts, HTTPS proxy and store procedures | — |
| `apps/contributors/accounts.py` | = + `src/knowledge_foundry/storage/accounts.py` | Accounts and server-side sessions for the contributor workspace. `Accounts, connect, manageable` | — |
| `apps/contributors/app.js` | = | Forms, session state and JSON API client | — |
| `apps/contributors/app.py` | = + `src/knowledge_foundry/applications/contributors/http.py`, `src/knowledge_foundry/applications/contributors/submissions.py`, `src/knowledge_foundry/storage/contributions.py` | Run the local corpus contributor workspace. `Contributions, ContributorHandler, --store, --port, --create-user` | `apps.contributors.accounts`, `apps.contributors.catalog` |
| `apps/contributors/catalog.py` | = + `src/knowledge_foundry/applications/contributors/catalog.py` | Read the pinned, human-readable case artifacts for the contributor UI. `load_catalog` | `assistant.datasets` |
| `apps/contributors/index.html` | = | Contributor UI root resource | — |
| `apps/contributors/styles.css` | = | Contributor view styles | — |
| `apps/kpg_viewer/app.js` | = | Viewer controls, selection and pagination | — |
| `apps/kpg_viewer/core.js` | = | KPG graph indexing, filters and navigation | — |
| `apps/kpg_viewer/detail.js` | = | Record and evidence detail view | — |
| `apps/kpg_viewer/graph.js` | = | Graph layout and SVG rendering | — |
| `apps/kpg_viewer/index.html` | = | Template with title, CSS and script placeholders | — |
| `apps/kpg_viewer/styles.css` | = | Inlined graph styles | — |

## Prompt resources

| Current path | Target path | Responsibility or consumer |
| --- | --- | --- |
| `src/knowledge_foundry/prompts/agent_prompt_claim_review` | = | Review procedure read by humans/workflow |
| `src/knowledge_foundry/prompts/agent_prompt_document_structuring_review` | = | Review procedure read by humans/workflow |
| `src/knowledge_foundry/prompts/agent_prompt_repair_failed_review_findings` | = | Review procedure read by humans/workflow |
| `src/knowledge_foundry/prompts/assistant_general_v1.txt` | = | Assistant runtime model input |
| `src/knowledge_foundry/prompts/assistant_reasoning_answer_v1.txt` | = | Assistant runtime model input |
| `src/knowledge_foundry/prompts/assistant_route_v1.txt` | = | Assistant runtime model input |
| `src/knowledge_foundry/prompts/claim_generation_v1.txt` | = | Engineering model input and build identity |
| `src/knowledge_foundry/prompts/rag_answer_v1.txt` | = | Retrieval model input |
| `src/knowledge_foundry/prompts/rag_query_v2.txt` | = | Retrieval model input |
| `src/knowledge_foundry/prompts/rag_rank_v1.txt` | = | Retrieval model input |
| `src/knowledge_foundry/prompts/summary_generation_v1.txt` | = | Engineering model input and build identity |
| `src/knowledge_foundry/prompts/topic_classification_v1.txt` | = | Engineering model input and build identity |

## SQL migrations

| Current path | Target path | Responsibility or consumer |
| --- | --- | --- |
| `src/knowledge_foundry/storage/migrations/001_initial.sql` | = | PostgreSQL migration · filename and SQL checksum in kf_migrations |
| `src/knowledge_foundry/storage/migrations/002_provenance_lookup.sql` | = | PostgreSQL migration · filename and SQL checksum in kf_migrations |

## Schema contracts

| Current path | Target path | Responsibility or consumer |
| --- | --- | --- |
| `schemas/aml-cft-reasoning-schema-v1.json` | = | Reasoning Engine · symbolic encodings, queries, failures and proof steps |
| `schemas/artifact-schema-v1.0.0.json` | = | Provenance · canonical source-to-topic artifact bundle |
| `schemas/claim-candidate-artifact-schema-v1.0.0.json` | = | Knowledge Engineering · grounded candidate records and bundle references |
| `schemas/compilation-model-output-v2.json` | = | Knowledge Engineering · compilation model response alternatives |
| `schemas/semantic-clustering-artifact-schema.json` | = | Knowledge Engineering · prepared structural/semantic records and lineage manifest |
| `schemas/summary-artifact-schema-v1.json` | = | Knowledge Engineering · recursive summary and cluster records, output manifest |

## Configuration

| Current path | Target path | Responsibility or consumer |
| --- | --- | --- |
| `config/aml-cft-reasoning-assistant-evaluation-cases.jsonl` | = | Assistant evaluation authoring inputs and document references |
| `config/aml-cft-reasoning-assistant-evaluation-criteria.json` | = | Fixed independent review denominators, scoring and repair limits |
| `config/aml-cft-reasoning-assistant.json` | = | Assistant modes, roles, budgets, scenario search and document configuration |
| `config/aml-cft-reasoning-datasets.json` | = | Per-package receipt, status, path and byte pins |
| `config/aml-cft-reasoning-vocabulary.json` | = | Predicate signatures and meanings for candidate policies |
| `config/claim-generation-and-classification.json` | = | Generation/classification requests, budgets and pricing |
| `config/pipeline.json` | = | Manifest selection, paths, chunking and provider factories |
| `config/retrieval-corpora.json` | = | Published corpus, saved input and trusted graph paths |
| `config/retrieval-multilingual.json` | = | Multilingual document-search/reasoning document route profile |
| `config/retrieval.json` | = | Default retrieval methods, budgets, embedding and answer profiles |
| `config/review-policy.json` | = | Source review samples, dimensions and thresholds |
| `config/semantic-clustering.json` | = | Structural build, embedding profile and cluster/request sizing |
| `config/summaries.json` | = | Recursive summary settings, model limits and pricing |
| `config/topics-v1.json` | = | Curated topic identity and taxonomy version |
| `config/workflow.json` | = | Document workflow configuration links, reviews and retrieval gates |

## Tests and fixtures

| Current path | Target path | Responsibility or consumer |
| --- | --- | --- |
| `tests/claim_fixtures.py` | = | Offline claim generation, review and classification fixtures |
| `tests/conftest.py` | = | Disposable PostgreSQL schemas and publication fixtures |
| `tests/fixtures/parsing-expanded-review-pages.json` | = | Required pages for production review selection |
| `tests/fixtures/reasoning-assistant-runtime-v1.json` | = | Pinned mocked runtime prompts |
| `tests/fixtures/reasoning-routing-v1.json` | = | Pinned routing/replay development fixtures |
| `tests/fixtures/retrieval-evaluation-123q.json` | = | Development anchors and locations also used in pilot validation |
| `tests/fixtures/structuring-pilot-regressions.json` | = | Source scope and definition regression anchors |
| `tests/rag_fixtures.py` | = | Retrieval projection, model and evidence fixtures |
| `tests/record_reasoning_input_smoke.py` | = | Mocked evaluation CLI and code-pinned smoke evidence |
| `tests/retrieval/test_artifacts.py` | = | Regression suite · claim_context, retrieval.artifacts, retrieval.config, retrieval.indexing, structural_chunks, summaries |
| `tests/retrieval/test_chat.py` | = | Regression suite · assistant.config, retrieval.config |
| `tests/retrieval/test_context_answers.py` | = | Regression suite · retrieval.answering, retrieval.config, retrieval.context, retrieval.pipeline, storage.postgres |
| `tests/retrieval/test_evaluation.py` | = | Regression suite · embeddings, retrieval.answering, retrieval.artifacts, retrieval.evaluation, retrieval.pipeline, retrieval.service, scripts.evaluate_retrieval |
| `tests/retrieval/test_evaluation_execution.py` | = | Regression suite · embeddings, retrieval.answering, retrieval.checkpoint, retrieval.evaluation, retrieval.service, structural_chunks, scripts.evaluate_retrieval |
| `tests/retrieval/test_inventory.py` | = | Regression suite · retrieval.service |
| `tests/retrieval/test_passage_and_claim.py` | = | Regression suite · retrieval.answering, retrieval.config, retrieval.evaluation, retrieval.pipeline, retrieval.ranking, retrieval.service, structural_chunks, scripts.query |
| `tests/retrieval/test_provenance.py` | = | Regression suite · retrieval.artifacts, retrieval.provenance |
| `tests/retrieval/test_query.py` | = | Regression suite · retrieval.query |
| `tests/retrieval/test_ranking.py` | = | Regression suite · retrieval.pipeline, retrieval.ranking |
| `tests/storage/test_generic_corpora.py` | = | Regression suite · retrieval.answering, retrieval.artifacts, retrieval.service, structural_chunks |
| `tests/storage/test_postgres.py` | = | Regression suite · retrieval.answering, retrieval.artifacts, retrieval.config, retrieval.evaluation, retrieval.indexing, retrieval.service, structural_chunks |
| `tests/test_aml_cft_formalization.py` | = | Regression suite · scripts.aml_cft_formalization, scripts.aml_cft_reasoning |
| `tests/test_aml_cft_pilot.py` | = | Regression suite · ingestion, scripts.validate_pilot |
| `tests/test_aml_cft_reasoning.py` | = | Regression suite · external command, fixture or file contract |
| `tests/test_chunking.py` | = | Regression suite · chunking |
| `tests/test_chunking_audit.py` | = | Regression suite · chunking, chunking_audit, document_structuring_review |
| `tests/test_claim_context.py` | = | Regression suite · claim_context, claim_generation_and_classification, generation_context, semantic_clusters, structural_chunks, scripts.deterministic_claim_review, scripts.validate_artifact_examples |
| `tests/test_claim_generation_and_classification_config.py` | = | Regression suite · claim_context, claim_generation_and_classification, embeddings, review, structural_chunks |
| `tests/test_claim_source_context.py` | = | Regression suite · claim_generation_and_classification, claim_source_context, structural_chunks |
| `tests/test_classification_reuse.py` | = | Regression suite · claim_generation_and_classification, scripts.classify_claims, scripts.deterministic_claim_review |
| `tests/test_classify_claims.py` | = | Regression suite · scripts.classify_claims |
| `tests/test_compilation.py` | = | Regression suite · claim_generation_and_classification, openai_tools, scripts.validate_artifact_examples |
| `tests/test_compilation_context.py` | = | Regression suite · chunking, claim_generation_and_classification, ingestion, parsing, parsing_audit, structural_chunks, scripts.deterministic_claim_review, scripts.validate_artifact_examples |
| `tests/test_contributors_auth.py` | = | Regression suite · external command, fixture or file contract |
| `tests/test_corpus.py` | = | Regression suite · corpus |
| `tests/test_deterministic_claim_review.py` | = | Regression suite · claim_generation_and_classification, review, scripts.deterministic_claim_review |
| `tests/test_document_structure.py` | = | Regression suite · claim_generation_and_classification, document_structure, structural_chunks |
| `tests/test_document_structuring.py` | = | Regression suite · chunking, chunking_audit, claim_context, document_structuring, ingestion, parsing, parsing_audit, parsing_review, structural_chunks, structure_providers |
| `tests/test_document_structuring_repair.py` | = | Regression suite · parsers, pdf_source, pdf_structure |
| `tests/test_document_structuring_review.py` | = | Regression suite · chunking, document_structuring, document_structuring_review |
| `tests/test_documented_commands.py` | = | Regression suite · external command, fixture or file contract |
| `tests/test_embeddings.py` | = | Regression suite · embeddings, structural_chunks |
| `tests/test_evidence_accounting.py` | = | Regression suite · claim_generation_and_classification |
| `tests/test_generate_claims.py` | = | Regression suite · scripts.generate_claims |
| `tests/test_generation_duplicates.py` | = | Regression suite · claim_generation_and_classification, claim_source_context, structural_chunks, scripts.render_knowledge_graph |
| `tests/test_generation_recovery.py` | = | Regression suite · claim_generation_and_classification, structural_chunks, scripts.generate_claims |
| `tests/test_ingestion.py` | = | Regression suite · ingestion, scripts.validate_artifact_examples |
| `tests/test_kpg_viewer.py` | = | Regression suite · external command, fixture or file contract |
| `tests/test_ocr_integration.py` | = | Regression suite · parsers |
| `tests/test_openai_tools.py` | = | Regression suite · generation_context, openai_tools, structural_chunks |
| `tests/test_parsing.py` | = | Regression suite · ingestion, parsers, parsing |
| `tests/test_parsing_audit.py` | = | Regression suite · parsing_audit, pdf_structure |
| `tests/test_parsing_review.py` | = | Regression suite · parsing_review |
| `tests/test_pdf_source.py` | = | Regression suite · parsers, parsing, parsing_audit, pdf_source, pdf_structure |
| `tests/test_pdf_structure.py` | = | Regression suite · chunking, parsers, parsing, pdf_structure |
| `tests/test_pdf_tables.py` | = | Regression suite · chunking, chunking_audit, parsers, parsing, parsing_audit, pdf_structure, pdf_tables |
| `tests/test_pipeline_commands.py` | = | Regression suite · claim_generation_and_classification, document_structuring, retrieval.artifacts, retrieval.config, retrieval.provenance, structural_chunks |
| `tests/test_pipeline_concurrency.py` | = | Regression suite · claim_generation_and_classification, concurrency, generation_context, structural_chunks, summaries |
| `tests/test_prepare_claim_context.py` | = | Regression suite · scripts.build_semantic_clusters |
| `tests/test_reasoning_assistant.py` | = | Regression suite · apps.assistant.app, assistant.config, assistant.datasets, assistant.llm, assistant.search, assistant.service, retrieval.service |
| `tests/test_reasoning_assistant_evaluation_dataset.py` | = | Regression suite · assistant.datasets, scripts.evaluate_reasoning_assistant_inputs, scripts.generate_reasoning_assistant_evaluation, scripts.validate_reasoning_assistant_evaluation |
| `tests/test_reasoning_assistant_final_round.py` | = | Regression suite · assistant.datasets, assistant.diagnostics, retrieval.service |
| `tests/test_reasoning_assistant_inputs.py` | = | Regression suite · apps.assistant.app, assistant.config, assistant.datasets, assistant.service, retrieval.service, scripts.evaluate_reasoning_assistant_inputs |
| `tests/test_reasoning_assistant_kyc.py` | = | Regression suite · external command, fixture or file contract |
| `tests/test_reasoning_assistant_search.py` | = | Regression suite · assistant.config, assistant.datasets, assistant.search, embeddings, scripts.replay_reasoning_pilot |
| `tests/test_reasoning_assistant_ui.py` | = | Regression suite · apps.assistant.app, assistant.config, assistant.llm, assistant.search, assistant.service |
| `tests/test_render_knowledge_graph.py` | = | Regression suite · scripts.render_knowledge_graph |
| `tests/test_request_counter_concurrency.py` | = | Regression suite · generation_context |
| `tests/test_semantic_clusters.py` | = | Regression suite · semantic_clusters |
| `tests/test_structural_build_config.py` | = | Regression suite · claim_context, claim_generation_and_classification, structural_chunks |
| `tests/test_structural_chunks.py` | = | Regression suite · structural_chunks |
| `tests/test_structure_providers.py` | = | Regression suite · ingestion, parsing, provider_exports, structure_providers |
| `tests/test_summaries.py` | = | Regression suite · claim_context, semantic_clusters, structural_chunks, summaries, scripts.generate_summaries, scripts.render_knowledge_graph |
| `tests/test_unstructured_provider.py` | = | Regression suite · parsing, parsing_audit, unstructured_provider |
| `tests/test_workflow.py` | = | Regression suite · ingestion, workflow |
| `tests/test_workflow_reporting.py` | = | Regression suite · workflow, workflow_reporting |

## Source corpus

| Current path | Target path | Responsibility or consumer |
| --- | --- | --- |
| `data/A-20250423_Lignes_directrices_obligations_Tracfin.pdf` | = | Manifest-selected original · preserve filename and exact bytes |
| `data/BASEL20-bcbs-aml-cft-risk-management-2020.pdf` | = | Manifest-selected original · preserve filename and exact bytes |
| `data/GEL26-20260318_lignes_directrices_gel_des_avoirs.pdf` | = | Manifest-selected original · preserve filename and exact bytes |
| `data/KYC21-20220404_lignes_directrices_revisees_relatives_identification_verification_connaissance.pdf` | = | Manifest-selected original · preserve filename and exact bytes |
| `data/L1-Article L561-2-2 - Code monétaire et financier - Légifrance.html` | = | Manifest-selected original · preserve filename and exact bytes |
| `data/L2-Article L561-6 - Code monétaire et financier - Légifrance.html` | = | Manifest-selected original · preserve filename and exact bytes |
| `data/NRA23-fr-colb-national-risk-assessment-2023.pdf` | = | Manifest-selected original · preserve filename and exact bytes |
| `data/P-acpr-ppe-2018.pdf` | = | Manifest-selected original · preserve filename and exact bytes |
| `data/PANN24-20240705_personnes_politiquement_exposees_hr.pdf` | = | Manifest-selected original · preserve filename and exact bytes |
| `data/R24EN-eu-reg-2024-1624-en.pdf` | = | Manifest-selected original · preserve filename and exact bytes |
| `data/R24FR-eu-reg-2024-1624-fr.pdf` | = | Manifest-selected original · preserve filename and exact bytes |
| `data/T-TRACFIN_2024_Tome3_FR_WEB_0.pdf` | = | Manifest-selected original · preserve filename and exact bytes |
| `data/TFR23EN-eu-reg-2023-1113-en.pdf` | = | Manifest-selected original · preserve filename and exact bytes |
| `data/manifest.json` | = | Corpus manifest and source-edition bindings |

## Artifact examples

| Current path | Target path | Responsibility or consumer |
| --- | --- | --- |
| `examples/artifact-bundle-html.json` | = | Manifest-bound bundle fixture for CLI validation, graph rendering and projection |
| `examples/artifact-bundle-pdf.json` | = | Manifest-bound bundle fixture for CLI validation, graph rendering and projection |

## Existing documentation

| Current path | Target path | Responsibility or consumer |
| --- | --- | --- |
| `doc/README.md` | = | Existing runbook, report or guide · preserve linked path |
| `doc/aml-cft-bo-batch-handoff.md` | = | Existing runbook, report or guide · preserve linked path |
| `doc/aml-cft-kyc-batch-handoff.md` | = | Existing runbook, report or guide · preserve linked path |
| `doc/aml-cft-pipeline.md` | = | Existing runbook, report or guide · preserve linked path |
| `doc/aml-cft-reasoning-assistant-evaluation.md` | = | Existing runbook, report or guide · preserve linked path |
| `doc/aml-cft-reasoning-assistant.md` | = | Existing runbook, report or guide · preserve linked path |
| `doc/aml-cft-reasoning-pilot.md` | = | Existing runbook, report or guide · preserve linked path |
| `doc/benchmarks.md` | = | Existing runbook, report or guide · preserve linked path |
| `doc/downstream-uses.md` | = | Existing runbook, report or guide · preserve linked path |
| `doc/providers.md` | = | Existing runbook, report or guide · preserve linked path |
| `doc/retrieval-evaluation-123q-20261103.md` | = | Existing runbook, report or guide · preserve linked path |
| `doc/state-of-the-art.md` | = | Existing runbook, report or guide · preserve linked path |
| `doc/verification-inventaire-open-data.md` | = | Existing runbook, report or guide · preserve linked path |

## Repository and artifact administration

| Current path | Target path | Responsibility or consumer |
| --- | --- | --- |
| `.envrc.example` | = | Environment variable contract and setup example |
| `.gitignore` | = | Tracking and local artifact policy |
| `artifacts/README.md` | = | Index of local run and persistence directories |
| `requirements.txt` | = | Shared Python dependency constraints |

## Local and generated paths

These recursive rules cover descendants at every depth, including run-specific Python/shell programs, saved code copies, reports, reference captures, checkpoint rows and figure assets. Each destination is the identical relative path. A clean clone provides only the tracked artifact index.

| Current root or pattern | Target | Contents and treatment |
| --- | --- | --- |
| `artifacts/ingested/**` | = | Source objects, content-addressed snapshots, latest report pointer. |
| `artifacts/20261027-unstructured-aml-cft-01/**` | = | Fast baseline, high-resolution attempts, comparison evidence and local execution/validation scripts. |
| `artifacts/20261028-docling-aml-cft-01/**` | = | Retained structure, reviews, claim exception, summaries, graphs, indexing, retrieval runs, inspection archive and history. |
| `artifacts/aml-cft-reasoning-pilot/**` | = | Five stage families with authoring code, setup controls, mappings, source material, independent replays and accepted receipts. The pinned split-reference utility also lives here. |
| `artifacts/aml-cft-reasoning-assistant-evaluation/**` | = | Versioned datasets, live submissions, agreements, reviewer programs, before-images, scores and final status. |
| `artifacts/aml-cft-reasoning-assistant/**` | = | Scenario embedding cache keyed by policy text and profile. |
| `artifacts/cache/**`, `artifacts/embeddings-and-token-counts/**`, `artifacts/summary-cache/**` | = | PDF verification, vector, token and response caches. Reads can affect cost and reproducibility. |
| `artifacts/db/**`, `artifacts/contributions/**` | = | PostgreSQL dumps and local SQLite store. Treat live data as persistent, including any SQLite journal or WAL siblings. |
| `artifacts/ontology-work-20261113.{tar.gz,manifest.json,sha256}` | = | Local archived work, inventory and checksums. Preserve together. |
| Other `artifacts/**` | = | Future or externally supplied run trees and their state retain their full relative paths. |
| `AGENTS.md`, `.agents/**`, `.python-version`, `.envrc` | = | Ignored local workflow, interpreter selection and environment. |
| `.git/**`, `.venv/**`, `.aws/**`, `.codex/**`, `**/__pycache__/**` | = | Checkout metadata, installed environment, local settings and generated interpreter state, outside the package migration. |

The four A1 reports are additions under `doc/` and postdate the inventory commit. Future-only packages, including Reasoning Foundry, have no source path to move.
