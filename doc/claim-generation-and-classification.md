# Claim generation and classification

`semantic clusters → candidate claims → topics`

Generation extracts draft evidence-linked claims; classification assigns topics
or null without changing text/evidence. Start with the [structural build](structural-chunks-and-semantic-clusters.md).
[Summaries](summaries.md) use the same clusters independently and do not replace claim review.

## Normal case

Load `OPENAI_API_KEY`; see [setup](ingestion.md). Use the completed structural
chunk and semantic cluster build outputs and a fresh claim run ID. Continue
only after each command succeeds.

```sh
CONTEXT_RUN_ID="your-completed-build-run-id"
RUN_ID="your-new-claim-run-id"
CONTEXT_DIR="artifacts/${CONTEXT_RUN_ID}/structural-chunks-and-semantic-clusters"
CANDIDATES_DIR="artifacts/${RUN_ID}/claim-generation"
CLASSIFIED_DIR="artifacts/${RUN_ID}/claim-classification"
CLAIM_CONFIG="config/claim-generation-and-classification.json"
TAXONOMY_PATH="config/topics-v1.json"

direnv exec . .venv/bin/python scripts/generate_claims.py --config "$CLAIM_CONFIG" \
  --context-dir "$CONTEXT_DIR" --output-dir "$CANDIDATES_DIR"

direnv exec . .venv/bin/python scripts/classify_claims.py --config "$CLAIM_CONFIG" --taxonomy "$TAXONOMY_PATH" \
  --candidates-dir "$CANDIDATES_DIR" --output-dir "$CLASSIFIED_DIR"
```

Generation/classification exit **0** when complete, **1** for finalized partial
output, and **2** for invalid input or an unhandled failure. Classification rejects
partial generation before making model calls. A complete subset is classifiable.

Invoke the [agent reviewer](../src/knowledge_foundry/prompts/agent_prompt_claim_review)
with `RUN_ID`, or use [manual evaluation](claim-evaluation.md). Then inspect the
[graph](knowledge-provenance-graph.md) and follow the [indexing runbook](pipeline-runbook.md#7-import-vectors-into-an-unpublished-snapshot).

The build can be shared across full/subset runs. Generation accepts optional
`--subset-clusters N` (length-stratified) or `--cluster-numbers N ...` (explicit,
one-based). Subsets still require a full-input build and do not guarantee
source/topic coverage. `--subset-batches`/`--batch-numbers` remain aliases.

## Optional: retry failed generation clusters

Use only for a finalized partial generation run. Preserve it and recover into a
fresh directory; successful clusters, including zero-claim results, are reused.
Only failed clusters make new generation calls.

```sh
direnv exec . .venv/bin/python scripts/generate_claims.py --config "$CLAIM_CONFIG" \
  --context-dir "$CONTEXT_DIR" \
  --retry-failed-from "$CANDIDATES_DIR" \
  --output-dir "${CANDIDATES_DIR}-recovered"
```

Recovery requires matching input, frozen context, configuration, prompt and scope.
Its usage/cost includes prior attempts; duration covers the current invocation.
If still partial, recover that result into another fresh directory.

After success, classify normally using the recovered directories:

```sh
direnv exec . .venv/bin/python scripts/classify_claims.py --config "$CLAIM_CONFIG" --taxonomy "$TAXONOMY_PATH" \
  --candidates-dir "${CANDIDATES_DIR}-recovered" \
  --output-dir "${CLASSIFIED_DIR}-recovered"
```

Or reuse compatible classifications below.

## Optional: reuse prior classifications

If compatible prior classifications exist, supply both their directory and the
candidate run they consumed. For example, after generation recovery:

```sh
direnv exec . .venv/bin/python scripts/classify_claims.py --config "$CLAIM_CONFIG" --taxonomy "$TAXONOMY_PATH" \
  --candidates-dir "${CANDIDATES_DIR}-recovered" \
  --reuse-classification-from "$CLASSIFIED_DIR" \
  --reuse-candidates-dir "$CANDIDATES_DIR" \
  --output-dir "${CLASSIFIED_DIR}-recovered"
```

Reuse validates hashes, taxonomy, model/settings, prompt, candidates and request
context. Matching assignments/nulls are retained; new/changed candidates and prior
errors make new calls. Historical partial runs may supply outcomes, but target
generation must be complete. New claim IDs/views require fresh source review.

`classification_reuse` records source provenance and reused/new counts. Telemetry
includes calls for the new output, including resumed calls, and excludes historical
reuse-source costs.

## Optional: resume an interrupted run

Rerun the same command with `--resume`, preserving its output directory and any
retry/reuse options. Examples for normal runs:

```sh
direnv exec . .venv/bin/python scripts/generate_claims.py --config "$CLAIM_CONFIG" \
  --context-dir "$CONTEXT_DIR" --output-dir "$CANDIDATES_DIR" --resume
```

```sh
direnv exec . .venv/bin/python scripts/classify_claims.py --config "$CLAIM_CONFIG" --taxonomy "$TAXONOMY_PATH" \
  --candidates-dir "$CANDIDATES_DIR" --output-dir "$CLASSIFIED_DIR" --resume
```

Inputs, settings, prompts, schemas, scope, reuse manifests and classification
fixtures must match. Checkpoints retain results, nulls and usage; errors are retried.
Existing classification output requires `--resume`; finalized runs require fresh
outputs. Older runs without checkpoints cannot resume.

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

Retries repeat the same unit with validation feedback after provider/response
failure. They add cost and never replace source review.

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

Before full runs, test changed subsets across documents/layouts using the new
cluster numbers. Compare shared original chunk IDs, fidelity, omissions, topics
and cost. Fixtures test mechanics; source review assesses quality. Preserve old
baselines without transferring judgments to another snapshot.
