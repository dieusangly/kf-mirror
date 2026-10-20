# Claim generation and classification

`prepared semantic clusters → candidate claims → topics`

Generation extracts draft, evidence-linked candidate claims. Classification assigns
curated topics or explicit null outcomes without changing candidate text or evidence.
Start with the [structural chunk and semantic cluster build](structural-chunks-and-semantic-clusters.md).
Its semantic clusters also feed an independent [recursive summary path](summaries.md).
Summaries do not consume claims, and do not replace claim generation, classification
or its grounding/classification review statuses.

## Normal case

Load `OPENAI_API_KEY`; see [setup](ingestion.md). Use the completed structural
chunk and semantic cluster build outputs and a fresh claim run ID. Continue
only after each command succeeds.

```sh
CONTEXT_RUN_ID="your-completed-build-run-id"
RUN_ID="your-new-claim-run-id"
CONTEXT_DIR="artifacts/structural-chunks-and-semantic-clusters-${CONTEXT_RUN_ID}"
CANDIDATES_DIR="artifacts/generated-claims-${RUN_ID}"
CLASSIFIED_DIR="artifacts/classified-claims-${RUN_ID}"
printf 'RUN_ID=%s\n' "$RUN_ID"

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
For prospective RAG and training consumers, see [downstream uses](downstream-uses.md).

The build can be shared across full/subset runs. Generation accepts optional
`--subset-clusters N` (length-stratified) or `--cluster-numbers N ...` (explicit,
one-based). Subsets still require a full-input build and do not guarantee
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

[Configuration](../config/claim-generation-and-classification.json) owns claim
generation, classification and the shared pricing catalogue. Its
`structural_build_config` reference selects the separate
[structural chunk and semantic cluster build config](../config/structural-chunks-and-semantic-clusters.json).
Paths resolve relative to the config file. The build's `request_sizing.claim_config`
must point back to this claim config so cluster sizing uses matching requests.

Each model step has its own settings. Pricing-only changes preserve embedding
caches and generation checkpoints; classification-only changes preserve build outputs.

| Setting | Default |
| --- | --- |
| Generation/classification | `gpt-6-luna`, medium reasoning |
| Generation input budget | `min(1,050,000 − 32,768 − 4,096, 32,000) = 32,000` |

Generation input counts include instructions, schema, metadata, text, evidence
and retry feedback; output/reasoning reserves are separate. Evidence is never
truncated to fit. See [structural chunk and semantic cluster build](structural-chunks-and-semantic-clusters.md)
for embedding limits, cluster refinement, caches and rebuild rules.

## Structure, evidence and completeness

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

Generation adds candidate JSONL, cluster mappings and evidence accounting to
the frozen structural chunk and semantic cluster build outputs. Classification
exports bundle/JSONL and topic views. Compatibility names, including
`compilation_scope` and `compilation-review.json`, remain unchanged.

Reports separate generation and classification usage. `duration_seconds` covers
the current invocation; cache hits are not new API calls and unknown costs stay
null. Subset projections include whole-input embedding cost. Preserve frozen
structural chunks, semantic clusters and checkpoints for resume. See
[cache and rebuild rules](structural-chunks-and-semantic-clusters.md#outputs-and-reruns).

Before a full run, test affected subsets and controls across documents/layouts.
Use cluster numbers from the new build, compare common original chunk IDs,
and assess source fidelity, omissions, topic choices and costs. Fixture tests
validate mechanics; source review assesses model quality. Keep old runs/reviews
as baselines; never copy judgments to a different snapshot.
