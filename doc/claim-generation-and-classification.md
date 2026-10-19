# Claim generation and classification

`original chunks → structural chunks → semantic clusters → candidate claims → topics`

Generation extracts draft, evidence-linked candidate claims. Classification assigns
curated topics or explicit null outcomes without changing candidate text or evidence.

## Normal case

Load `OPENAI_API_KEY`; see [setup](ingestion.md). From the repository root, replace
the placeholders with a reviewed chunking run ID and a fresh claim run ID.
Run each command only after the previous one succeeds.

```sh
INPUT_RUN_ID="your-reviewed-chunking-run-id"
RUN_ID="your-new-claim-run-id"
CHUNKED_DIR="artifacts/chunked-${INPUT_RUN_ID}"
CONTEXT_DIR="artifacts/structural-chunks-and-semantic-clusters-${RUN_ID}"
CANDIDATES_DIR="artifacts/generated-claims-${RUN_ID}"
CLASSIFIED_DIR="artifacts/classified-claims-${RUN_ID}"
printf 'RUN_ID=%s\n' "$RUN_ID"

.venv/bin/python scripts/build_structural_chunks_and_semantic_clusters.py \
  --chunked-dir "$CHUNKED_DIR" --output-dir "$CONTEXT_DIR" \
  --cache-dir artifacts/embeddings-and-token-counts

.venv/bin/python scripts/generate_claims.py \
  --context-dir "$CONTEXT_DIR" --output-dir "$CANDIDATES_DIR"

.venv/bin/python scripts/classify_claims.py \
  --candidates-dir "$CANDIDATES_DIR" --output-dir "$CLASSIFIED_DIR"
```

Generation/classification exit **0** when complete, **1** for finalized partial
output, and **2** for invalid input or an unhandled failure. Classification rejects
partial generation before making model calls. A complete subset is classifiable.

Then invoke the [agent reviewer](../src/knowledge_foundry/prompts/agent_prompt_claim_reviewer)
with this `RUN_ID`: it initializes or reuses the review, inspects saved sources and
runs final evaluation. For manual review commands, see [claim evaluation](claim-evaluation.md).
Then create the [knowledge provenance graph](knowledge-provenance-graph.md) for inspection.

Preparation can be shared across full/subset runs. Generation accepts optional
`--subset-clusters N` (length-stratified) or `--cluster-numbers N ...` (explicit,
one-based). Subsets still require full-input preparation and do not guarantee
source/topic coverage. `--subset-batches`/`--batch-numbers` remain aliases.

## Optional: retry failed generation clusters

Use only for a finalized partial generation run. Preserve it and recover into a
fresh directory; successful clusters, including zero-claim results, are reused.
Only failed clusters make new generation calls.

```sh
.venv/bin/python scripts/generate_claims.py \
  --context-dir "$CONTEXT_DIR" \
  --retry-failed-from "$CANDIDATES_DIR" \
  --output-dir "${CANDIDATES_DIR}-recovered"
```

Recovery requires matching input, frozen context, configuration, prompt and scope.
Its usage/cost includes prior attempts; duration covers the current invocation.
If still partial, recover that result into another fresh directory.

After success, classify normally using the recovered directories:

```sh
.venv/bin/python scripts/classify_claims.py \
  --candidates-dir "${CANDIDATES_DIR}-recovered" \
  --output-dir "${CLASSIFIED_DIR}-recovered"
```

Alternatively, choose reuse below instead of this full-classification command.
Neither retry nor reuse is required for a normal successful run.

## Optional: reuse prior classifications

If compatible prior classifications exist, supply both their directory and the
candidate run they consumed. For example, after generation recovery:

```sh
.venv/bin/python scripts/classify_claims.py \
  --candidates-dir "${CANDIDATES_DIR}-recovered" \
  --reuse-classification-from "$CLASSIFIED_DIR" \
  --reuse-candidates-dir "$CANDIDATES_DIR" \
  --output-dir "${CLASSIFIED_DIR}-recovered"
```

Reuse validates artifact hashes, taxonomy, model/settings, prompt, candidate and
request context. Matching topic assignments and explicit nulls are retained;
new/changed candidates and prior classification errors make new calls. Historical
classifications from partial generation may supply outcomes, but target generation
must be complete. A fresh combined build gets new claim IDs/topic views and needs
its own source review; previous review judgments stay with their old snapshot.

`classification_reuse` records source provenance and reused/new counts. Telemetry
includes calls for the new output, including resumed calls, and excludes historical
reuse-source costs.

## Optional: resume an interrupted run

Rerun the same command with `--resume`, preserving its output directory and any
retry/reuse options. Examples for normal runs:

```sh
.venv/bin/python scripts/generate_claims.py \
  --context-dir "$CONTEXT_DIR" --output-dir "$CANDIDATES_DIR" --resume
```

```sh
.venv/bin/python scripts/classify_claims.py \
  --candidates-dir "$CANDIDATES_DIR" --output-dir "$CLASSIFIED_DIR" --resume
```

Inputs, execution settings, prompts, schemas, scope and reuse-source manifests must
match; classification fixtures must also match. Completed checkpoints are reused;
classification checkpoints include nulls and usage, and errors are retried.
Existing classification output is rejected before calls unless resuming. Finalized
runs require fresh outputs; classification runs interrupted before checkpoint
support have no saved intermediate results.

## Configuration and context limits

[Configuration](../config/claim-generation-and-classification.json) separates
structural assembly, embeddings, clustering, generation and classification.
Each model step has its own settings; pricing is a separate provider/model snapshot.
Pricing-only changes preserve embedding caches and generation checkpoints;
classification-only settings preserve prepared context.

| Setting | Default |
| --- | --- |
| Embeddings | `text-embedding-3-small`, 1,536 dimensions, `cl100k_base` |
| Embedding limits | 8,192 tokens/input; 32 inputs and 32,000 tokens/request |
| Clustering | Every-member cosine similarity ≥0.80 |
| Generation/classification | `gpt-6-luna`, medium reasoning |
| Generation input budget | `min(1,050,000 − 32,768 − 4,096, 32,000) = 32,000` |

Generation input counts include instructions, schema, metadata, text and evidence;
output/reasoning reserves are separate. Exact provider counts check each structural
chunk before embeddings, then complete cluster requests and retry feedback.
Embedding limits are checked separately. Oversized clusters are refined/partitioned;
structural chunks remain indivisible. For an oversized singleton, inspect links or
verify a larger budget, then rebuild context. Evidence is never truncated to fit.
The operational cap is not an evaluated optimum. Verify model limits before changing
models; the configuration records documentation links and verification dates.

Progress reports assembly, completed individual/cluster checks and oversized-cluster
splits. Completed checks can include an oversized cluster awaiting refinement.

## Structure, evidence and completeness

Structural chunks join document-local fragments/context, numbered or bulleted
list introductions, table continuations and same-page note hints. Adjacent incomplete
sentences may join across a table boundary when same-page geometry supports continuity.
Heuristic links require source review; headings alone do not join chunks. Only identical evidence spans are
deduplicated; repeated wording at distinct locations remains.

Semantic clusters can span documents. Each original/structural chunk belongs to
one final cluster. Greedy grouping requires similarity to every member; it is
deterministic but order-sensitive. Similarity does not establish actor identity,
agreement, legal scope or effective dates.

Each generation request covers one cluster. Temporary E labels cite exact evidence;
C labels identify non-citable context. Independently citable passages always
retain their E text; matching C context refers to that E passage, never the reverse.
The [response contract](../schemas/compilation-model-output-v2.json)
requires `claims` and one coverage disposition per E passage: `represented`,
`non_substantive`, `insufficient_context` or `ambiguous`, with a reason. Runtime
derives claim links from citations. Missing/duplicate E labels, unknown evidence
and contradictory dispositions are rejected through bounded retries.

An attempt such as `2/3` or `3/3` repeats the same work unit after a
provider error or rejected response. Validation feedback can guide the next response.
Retries add usage/cost; a successful retry still requires source review.

`evidence-accounting.json` preserves exact references and candidate IDs; checkpoints,
classification and evaluation validate its lineage. Accounting and citations do not
prove semantic completeness. Review omitted rules, qualifications, tables and notes
against originals. Legacy fixtures are unassessed; older artifacts remain readable.

## Artifacts, reports and reruns

Preparation saves the original bundle, structural memberships/links, embeddings,
cluster plan, report and integrity manifest. Generation adds candidate JSONL,
cluster mappings and evidence accounting. Classification exports bundle/JSONL and
topic views. Existing compatibility names, including `compilation_scope` and
`compilation-review.json`, remain unchanged.

Reports separate embedding, token-counting, generation and classification usage.
`duration_seconds` measures the current invocation, rounded to milliseconds.
Distribution fields count members per group; `singletons` counts one-member
clusters. `usable_input_tokens` records the request cap;
`claim_generation_request_token_counting` records counting/cache activity, and
`oversized_cluster_splits` records refinements. Cache hits are not new API calls;
unknown costs stay null. Subset projections include whole-input embedding cost.

Embedding caches require matching text/model/settings and validate hashes. Failed
preparation leaves `failure.json`; validate saved outputs before deciding whether
a rebuild is needed. If rebuilding, use fresh output with the same cache. The cache
can be regenerated; preserve the frozen preparation needed for generation and resume.
Parser/chunker changes require new [parsing](parsing.md), [chunking](chunking.md),
source reviews and downstream runs. Generation prompt/schema/request/budget changes
require fresh preparation and generation; unchanged embeddings remain reusable.
A budget-only change does not require repeating parsing/chunking reviews.

Before a full run, test affected subsets and controls across documents/layouts.
Use cluster numbers from the new preparation, compare common original chunk IDs,
and assess source fidelity, omissions, topic choices and costs. Fixture tests
validate mechanics; source review assesses model quality. Keep old runs/reviews
as baselines; never copy judgments to a different snapshot.
