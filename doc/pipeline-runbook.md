# Source-to-chatbot runbook

Run from the repository root in one shell. Stop dependent stages on failed/pending
gates. Prerequisites: [Python/direnv](ingestion.md), [Tesseract French/English](parsing.md#setup),
Poppler, `OPENAI_API_KEY` for model steps and [PostgreSQL/pgvector](retrieval-rag.md#1-create-the-local-database-once).
For saved-pilot reuse see [RAG setup](retrieval-rag.md).

Manual commands do not populate [controller state/reports](resumable-pilot-workflow.md).
Launch fresh independent reviewers with `reviewers` settings from
`config/workflow.json`; retain session identities, judgments and usage.
The [17-file scope](french-aml-cft-pilot-sources-and-evaluation.md) includes the accepted PPE limitation.

## Inputs and paths

Save originals and update manifest paths, hashes, sizes and editions; ingestion
does not download. Select IDs and matching HTML selectors, OCR languages and
review fixtures. Defaults select the AML/CFT pilot.

```sh
RUN_ID="your-new-run-id"
INPUT_RUN_ID="$RUN_ID"  # change only when reusing reviewed parsed/chunked inputs
CORPUS_ID="aml-cft-expanded-pilot"  # matches config/workflow.json
SOURCE_ROOT="."  # same source root as pipeline.json's resolved root
MANIFEST_PATH="data/manifest.json"
PIPELINE_CONFIG="config/pipeline.json"
REVIEW_POLICY="config/review-policy.json"
SAMPLE_PER_DOCUMENT=12
PARSING_REVIEW_PAGES="tests/fixtures/parsing-expanded-review-pages.json"
STRUCTURAL_CONFIG="config/structural-chunks-and-semantic-clusters.json"
CLAIM_CONFIG="config/claim-generation-and-classification.json"
SUMMARY_CONFIG="config/summaries.json"
TAXONOMY_PATH="config/topics-v1.json"
RETRIEVAL_CONFIG="config/retrieval-multilingual.json"
CORPORA_CONFIG="config/retrieval-corpora.json"
PARSED_DIR="artifacts/parsed-${INPUT_RUN_ID}"
CHUNKED_DIR="artifacts/chunked-${INPUT_RUN_ID}"
CONTEXT_DIR="artifacts/structural-chunks-and-semantic-clusters-${RUN_ID}"
CANDIDATES_DIR="artifacts/generated-claims-${RUN_ID}"
CLASSIFIED_DIR="artifacts/classified-claims-${RUN_ID}"
SUMMARY_DIR="artifacts/summaries-${RUN_ID}"
IMPORT_RESULT="artifacts/retrieval-import-${RUN_ID}.json"
```

Use fresh output directories. For another corpus, select matching configs,
manifest, source root, review locations and taxonomy. The structural config and
claim config must
[reference each other](structural-chunks-and-semantic-clusters.md#configuration-and-budgets).
Set the retrieval text-search language and embedding profile before importing.

## 1. Ingest, parse and review

```sh
direnv exec . .venv/bin/python scripts/ingest.py --config "$PIPELINE_CONFIG"
direnv exec . .venv/bin/python scripts/parse.py --config "$PIPELINE_CONFIG" \
  --output-dir "$PARSED_DIR" --run-id "$INPUT_RUN_ID"
direnv exec . .venv/bin/python scripts/auto_review_parsing.py \
  --input-dir "$PARSED_DIR" --source-root "$SOURCE_ROOT" \
  --output "$PARSED_DIR/parsing-evaluation-report.json"
```

**Gate:** each command exits 0; parsing evaluation has `gate.status: pass`.
[Source-checked reference fixtures](parsing-evaluation.md) are optional and must
match the selected source editions. Unresolved/partial outputs need inspection.
Then initialize independent parsing review:

```sh
direnv exec . .venv/bin/python scripts/auto_review_parsing.py \
  --input-dir "$PARSED_DIR" --source-root "$SOURCE_ROOT" \
  --manifest "$MANIFEST_PATH" --review-policy "$REVIEW_POLICY" \
  --sample-per-document "$SAMPLE_PER_DOCUMENT" --required-pages "$PARSING_REVIEW_PAGES" \
  --initialize-review "$PARSED_DIR/parsing-review.json" \
  --output "$PARSED_DIR/parsing-review-report.json"
```

Initialization can exit 0 while pending. Ask a fresh agent to follow
[`agent_prompt_parsing_review`](../src/knowledge_foundry/prompts/agent_prompt_parsing_review)
with these paths/settings. It inspects originals and evaluates with `--review-file`.
Reuse existing reviews; never initialize over them. Keep mandatory-page selection
consistent, replacing/omitting it for another corpus.

**Review gate:** `automatic_gate`, `review_gate` and `gate` must all be `pass` in
`$PARSED_DIR/parsing-review-report.json` before step 2. See
[coverage and limitations](parsing-evaluation.md#independent-parsing-review).

## 2. Build and review original chunks

```sh
direnv exec . .venv/bin/python scripts/chunk.py --config "$PIPELINE_CONFIG" \
  --input-dir "$PARSED_DIR" --output-dir "$CHUNKED_DIR"
```

**Gate:** exit 0. Then ask an agent to follow
[`agent_prompt_chunking_review`](../src/knowledge_foundry/prompts/agent_prompt_chunking_review)
with the exact parsed/chunked paths, manifest, source root, `REVIEW_POLICY` and
`SAMPLE_PER_DOCUMENT`.
It initializes/reuses the review, inspects saved originals and evaluates judgments.
[Manual commands](chunking-evaluation.md) are also available.

**Review gate:** `automatic_gate`, `review_gate` and `gate` must all be `pass` in
`$CHUNKED_DIR/chunking-evaluation-report.json`. Never mark uninspected items passed.
Reusing a reviewed build preserves its existing judgments.

## 3. Structural chunk and semantic cluster build

```sh
direnv exec . .venv/bin/python scripts/build_structural_chunks_and_semantic_clusters.py \
  --chunked-dir "$CHUNKED_DIR" --parsed-dir "$PARSED_DIR" \
  --config "$STRUCTURAL_CONFIG" --output-dir "$CONTEXT_DIR" \
  --cache-dir artifacts/embeddings-and-token-counts
```

**Gate:** exit 0 and a complete validated build of vectors, hierarchy, links and
clusters. Claims/summaries consume it independently; there is no separate structural
review. [Settings/rebuild rules](structural-chunks-and-semantic-clusters.md).

## 4. Generate, classify and review claims

```sh
direnv exec . .venv/bin/python scripts/generate_claims.py \
  --context-dir "$CONTEXT_DIR" --config "$CLAIM_CONFIG" --output-dir "$CANDIDATES_DIR"
direnv exec . .venv/bin/python scripts/classify_claims.py \
  --candidates-dir "$CANDIDATES_DIR" --config "$CLAIM_CONFIG" \
  --taxonomy "$TAXONOMY_PATH" --output-dir "$CLASSIFIED_DIR"
```

**Gate:** both exit 0, reports are complete and have no request errors.
Then invoke [`agent_prompt_claim_review`](../src/knowledge_foundry/prompts/agent_prompt_claim_review)
with the exact candidate/classified paths, manifest, source root, taxonomy,
`REVIEW_POLICY` and `SAMPLE_PER_DOCUMENT`.
[Manual review commands](claim-evaluation.md) are also available.

**Review gate:** automatic, review and overall gates must pass in
`$CLASSIFIED_DIR/compilation-evaluation-report.json`. Claims remain drafts;
a policy pass can retain recorded non-critical defects.

## 5. Generate summaries independently

```sh
direnv exec . .venv/bin/python scripts/generate_summaries.py \
  --context-dir "$CONTEXT_DIR" --output-dir "$SUMMARY_DIR" \
  --config "$SUMMARY_CONFIG" --cache-dir artifacts/summary-cache
```

**Gate:** exit 0 and frozen-artifact validation. Summaries need only step 3 and
remain drafts without an independent fidelity gate. Import fills missing final
summary vectors. [Details](summaries.md).

## 6. Build the combined provenance graph

```sh
direnv exec . .venv/bin/python scripts/render_knowledge_graph.py \
  --bundle "$CLASSIFIED_DIR/artifact-bundle.json" \
  --claim-generation-dir "$CANDIDATES_DIR" --summary-dir "$SUMMARY_DIR" \
  --output "$CLASSIFIED_DIR/knowledge-provenance-graph.html"
```

**Gate:** exit 0; inspect both provenance paths in the HTML.
Before opening chat, register these exact directories and `graph_path` under
`CORPUS_ID`/`RUN_ID` in `CORPORA_CONFIG` ([template](retrieval-rag.md#other-corpora-and-commands)).
The chatbot embeds compatible local HTML; no separate graph server is needed.

## 7. Import vectors into an unpublished snapshot

Create the database once using the [RAG setup](retrieval-rag.md#1-create-the-local-database-once).
Then import the outputs above, rather than selecting an older preset run:

```sh
direnv exec . .venv/bin/python scripts/index_artifacts.py migrate
direnv exec . .venv/bin/python scripts/index_artifacts.py import \
  --corpus "$CORPUS_ID" --config "$RETRIEVAL_CONFIG" \
  --preparation-dir "$CONTEXT_DIR" --claims-dir "$CLASSIFIED_DIR" \
  --summaries-dir "$SUMMARY_DIR" --embed-missing > "$IMPORT_RESULT"
cat "$IMPORT_RESULT"
```

**Gate:** exit 0. In `vector_coverage`, every imported kind must have `vectors ==
records`. `--embed-missing` reuses compatible saved/cache vectors and fills missing
claim and summary vectors; it may incur API costs. No upstream files are changed.

```sh
SNAPSHOT_ID=$(direnv exec . .venv/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["snapshot_id"])' "$IMPORT_RESULT")
```

**Gate:** exit 0. Retain the explicit snapshot ID for evaluation; importing does not publish it.
Passage-only RAG can start after step 3: omit the claim/summary directory arguments
and skip their dependent steps. Keyword-only imports may omit `--embed-missing`;
vector/combined queries require vectors for the selected kind.

## 8. Evaluate before publication

Select source-reviewed cases matching the exact corpus and editions. Development
smoke cases are useful for diagnosis but are not acceptance labels. Evaluate the
explicit ready snapshot without selecting it for chat:

```sh
RETRIEVAL_CASES="path/to/source-reviewed-cases.json"
direnv exec . .venv/bin/python scripts/evaluate_retrieval.py \
  --corpus "$CORPUS_ID" --config "$RETRIEVAL_CONFIG" --snapshot-id "$SNAPSHOT_ID" \
  --unpublished --cases "$RETRIEVAL_CASES" --modes passage claim summary \
  --no-rerank --check-excerpts --output "artifacts/retrieval-evaluation-${RUN_ID}.json"
```

**Gate:** all cases complete and each mode meets the acceptance target; inspect
rows/metrics and semantic relevance against originals. Baselines, page overlap and
anchors alone cannot approve. The controller requires combined hit rate ≥0.875
and a source-checked attestation. Its `retrieval_acceptance: null` blocks publication;
the checked-in 22 cases remain unreviewed development data.

## 9. Explicitly publish, query and start chat

Only after all required build, review and retrieval checks pass:

```sh
direnv exec . .venv/bin/python scripts/index_artifacts.py publish \
  --corpus "$CORPUS_ID" --snapshot-id "$SNAPSHOT_ID"
```

**Gate:** exit 0. Publication is explicit; prior snapshots and histories remain.
For controller-managed runs, `scripts/workflow.py publish` enforces the complete
expanded-pilot gates. Manual outputs cannot be published through controller state
automatically; the low-level command above relies on the operator verifying all
required gates and acceptance evidence.

```sh
direnv exec . .venv/bin/python scripts/query.py 'Which documents are in this corpus?' \
  --corpus "$CORPUS_ID" --config "$RETRIEVAL_CONFIG" --snapshot-id "$SNAPSHOT_ID" --provider openai
direnv exec . env KF_RETRIEVAL_CONFIG="$RETRIEVAL_CONFIG" KF_CORPORA_CONFIG="$CORPORA_CONFIG" \
  .venv/bin/python -m streamlit run apps/chat/app.py \
  --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

Open <http://127.0.0.1:8501>. Select the corpus/snapshot, ask corpus-specific
questions, inspect citations and open the graph. Inventory success alone does
not measure answer quality. Use [retrieval evaluation](retrieval-rag.md#validation-and-troubleshooting)
with cases matching the corpus; compare passage, claim and summary modes.
Without an API key, use keyword/source excerpts and **Documents in corpus**.

## Failures and reruns

Stop dependent stages on failure, partial output or a pending required review.
Preserve findings and frozen files; rebuild affected outputs into fresh directories.
Never change judgments or thresholds to manufacture a pass.

Interrupted claim steps support `--resume`; finalized partial generation supports
[`--retry-failed-from`](claim-generation-and-classification.md#optional-retry-failed-generation-clusters).
Structural builds and summaries require fresh outputs; retain their caches.
Parsing/chunking changes invalidate dependent builds and their reviews. RAG imports
are repeatable; changed projections/vectors create a new snapshot to publish.
