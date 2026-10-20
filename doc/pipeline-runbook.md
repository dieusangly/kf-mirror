# Pipeline commands and gates

Run one command at a time from the repository root. Continue only after its gate
passes. Install [dependencies](ingestion.md) and load `OPENAI_API_KEY` for embedding
and LLM steps; an already authorized direnv setup can prefix them with `direnv exec .`.
Agent reviews below are chat tasks, not shell commands.

## Inputs and run IDs

For new documents, update the source manifest and select their IDs in a pipeline
configuration. Match OCR languages and HTML selectors to the sources. Use supported
PDF, HTML, Markdown or DOCX inputs and a topic taxonomy appropriate to the corpus.
The checked-in configuration/taxonomy select the AML pilot; they are defaults,
not required document identities. Source-specific reference fixtures are optional
and must match the selected sources. No pilot-page review is a pipeline stage.

```sh
RUN_ID="your-new-run-id"
INPUT_RUN_ID="$RUN_ID"  # use an earlier ID only for valid reviewed inputs
PIPELINE_CONFIG="config/pipeline.json"  # replace for another corpus
MANIFEST_PATH="data/manifest.json"      # match the pipeline configuration
TAXONOMY_PATH="config/topics-v1.json"   # replace for another topic domain
PARSED_DIR="artifacts/parsed-${INPUT_RUN_ID}"
CHUNKED_DIR="artifacts/chunked-${INPUT_RUN_ID}"
CONTEXT_DIR="artifacts/structural-chunks-and-semantic-clusters-${RUN_ID}"
CANDIDATES_DIR="artifacts/generated-claims-${RUN_ID}"
CLASSIFIED_DIR="artifacts/classified-claims-${RUN_ID}"
SUMMARY_DIR="artifacts/summaries-${RUN_ID}"
```

Use fresh output directories. Existing parsed/chunked runs can be reused when
source hashes, build identity and review gates still match; then start at step 4.
Override input directories separately if their IDs differ.

## 1. Inventory, parse and evaluate

```sh
.venv/bin/python scripts/ingest.py --config "$PIPELINE_CONFIG"
```

**Gate:** exit 0; saved sources match the manifest. Then:

```sh
.venv/bin/python scripts/parse.py --config "$PIPELINE_CONFIG" --output-dir "$PARSED_DIR"
```

**Gate:** exit 0. Then evaluate:

```sh
.venv/bin/python scripts/evaluate_parsing.py --input-dir "$PARSED_DIR" \
  --output "$PARSED_DIR/parsing-evaluation-report.json"
```

**Gate:** exit 0 and `gate: pass`. See [parsing evaluation](parsing-evaluation.md)
for source-checked references and partial-output handling.

## 2. Build original chunks

```sh
.venv/bin/python scripts/chunk.py --config "$PIPELINE_CONFIG" \
  --input-dir "$PARSED_DIR" --output-dir "$CHUNKED_DIR"
```

**Gate:** exit 0. Original chunks remain unchanged in all later stages.

## 3. Agent chunking review

Ask an agent to follow
[`agent_prompt_chunking_reviewer`](../src/knowledge_foundry/prompts/agent_prompt_chunking_reviewer)
with the input run ID, exact parsed/chunked directories and `MANIFEST_PATH`.
It initializes/reuses the sample, inspects saved sources and evaluates judgments.

**Gate:** automatic, review and overall gates pass in
`chunking-evaluation-report.json`. A pending or automatic-only pass is insufficient.
Repeat this review when the original build changes; reuse its valid pass otherwise.

## 4. Structural chunks and semantic clusters

See [structural chunk and semantic cluster build](structural-chunks-and-semantic-clusters.md) for settings,
artifacts and rebuild rules. Both claims and summaries consume this output.

```sh
.venv/bin/python scripts/build_structural_chunks_and_semantic_clusters.py \
  --chunked-dir "$CHUNKED_DIR" --parsed-dir "$PARSED_DIR" \
  --output-dir "$CONTEXT_DIR" --cache-dir artifacts/embeddings-and-token-counts
```

**Gate:** exit 0 and complete, validated build. The command checks hierarchy,
lineage, unique membership and request budgets. Oversized semantic clusters are
partitioned without splitting structural chunks or truncating evidence.
There is no separate structural agent-review stage. Both output branches start here.

## 5. Generate and classify claims

```sh
.venv/bin/python scripts/generate_claims.py \
  --context-dir "$CONTEXT_DIR" --output-dir "$CANDIDATES_DIR"
```

**Gate:** exit 0, complete generation, no failed chunks. Then:

```sh
.venv/bin/python scripts/classify_claims.py --taxonomy "$TAXONOMY_PATH" \
  --candidates-dir "$CANDIDATES_DIR" --output-dir "$CLASSIFIED_DIR"
```

**Gate:** exit 0, complete classification, no request errors. A null topic is valid.

## 6. Agent claim review

Ask an agent to follow
[`agent_prompt_claim_reviewer`](../src/knowledge_foundry/prompts/agent_prompt_claim_reviewer)
with the claim run ID, candidate/classified directories, `MANIFEST_PATH` and
`TAXONOMY_PATH`. It reviews grounding, classification, completeness and topics.

**Gate:** final evaluator exit 0; automatic, review and overall gates pass in
`compilation-evaluation-report.json`. Keep findings: policy can pass despite
non-critical defects. Successful execution alone does not establish claim quality.

## 7. Summaries — independent branch

```sh
.venv/bin/python scripts/generate_summaries.py \
  --context-dir "$CONTEXT_DIR" --output-dir "$SUMMARY_DIR" \
  --config config/summaries.json --cache-dir artifacts/summary-cache
```

**Gate:** exit 0 and successful offline validation. This branch needs step 4,
not claims or claim review. Summaries remain drafts; see [models and stopping
conditions](summaries.md).

## 8. Combined provenance graph

```sh
.venv/bin/python scripts/render_knowledge_graph.py \
  --bundle "$CLASSIFIED_DIR/artifact-bundle.json" \
  --claim-generation-dir "$CANDIDATES_DIR" --summary-dir "$SUMMARY_DIR" \
  --output "$CLASSIFIED_DIR/knowledge-provenance-graph.html"
```

**Gate:** exit 0; open the HTML and inspect both provenance paths.

## Failures and reruns

Stop dependent steps on failure, partial output or a pending/failed required review.
Preserve attempts and findings, fix the cause, test and rebuild affected outputs.
Do not edit frozen artifacts or thresholds to manufacture a pass.

Interrupted claims/classification support `--resume`; finalized partial generation
uses `--retry-failed-from` into a fresh directory. See [claim reruns](claim-generation-and-classification.md).
The build and summaries need fresh output directories; retain their caches.
