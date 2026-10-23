# Source-to-chatbot runbook

Run one stage at a time from the repository root. Prerequisites:
[Python/direnv](ingestion.md), [Tesseract French/English](parsing.md#setup), Poppler,
API credentials for model steps and [PostgreSQL/pgvector](retrieval-rag.md#1-create-the-local-database-once).
For older saved outputs, use [RAG setup](retrieval-rag.md).

## Inputs and paths

Save originals and update manifest paths, hashes, sizes and editions. Configure
selection, parsers, taxonomy, models and retrieval in `config/workflow.json` and
its referenced configs. Defaults select the [17-file pilot](french-aml-cft-pilot-sources-and-evaluation.md).
Both structural/claim configs must [reference each other](structural-chunks-and-semantic-clusters.md#configuration-and-budgets).

```sh
RUN_ID="YYYYMMDD-manual-workflow"  # replace YYYYMMDD with your run date
RUN_DIR="artifacts/${RUN_ID}"
WORKFLOW_CONFIG="config/workflow.json"
PARSED_DIR="$RUN_DIR/parsing"
CHUNKED_DIR="$RUN_DIR/chunking"
CONTEXT_DIR="$RUN_DIR/structural-chunks-and-semantic-clusters"
CANDIDATES_DIR="$RUN_DIR/claim-generation"
CLASSIFIED_DIR="$RUN_DIR/claim-classification"
SUMMARY_DIR="$RUN_DIR/summary-generation"

stage() {
  direnv exec . .venv/bin/python scripts/workflow.py step \
    --run-id "$RUN_ID" --config "$WORKFLOW_CONFIG" \
    --max-seconds 14400 --max-paid-stages 6 --max-review-sessions 4 \
    --max-stage-attempts 3 "$@"
}
```

Manual runs keep latest outputs under `<stage>/`, with bundle files directly inside.
`state.json` and `report.json` record commands, upstream hashes, gates and measured
costs. Builder logs live in `execution/`; prior saved layouts remain unchanged.
No step runs prerequisites automatically or falls back to history. Budgets
are cumulative across steps/rebuilds, not dollar caps. Completed steps are reused.
Direct low-level scripts remain available in stage guides; they do not manage history.

## 1. Ingest, parse and review

```sh
stage --stage ingestion
stage --stage parsing
stage --stage parsing-review
```

Ingestion saves `ingestion/report.json`. Parsing must be fully `parsed`.
`parsing-review` runs the automatic audit and saves its required checkpoint under
`parsing/automatic-review/`; no separate audit command is needed. Automatic failures
block inspection. Review initialization returns nonzero while pending: inspect
`parsing-review/report.json`, then give a fresh independent agent
[`agent_prompt_parsing_review`](../src/knowledge_foundry/prompts/agent_prompt_parsing_review)
with these exact paths, `parsing-review/review.json` and workflow sample/policy settings.
After source inspection, rerun `stage --stage parsing-review` to record the gate.
[Coverage and limitations](parsing-evaluation.md#independent-parsing-review).

For any of the three review stages, the controller can instead launch only that
stage's configured independent reviewer by adding `--agent-review --allow-paid`.
External agents must be launched explicitly with `reviewers` from `config/workflow.json`
settings; prompts cannot select their running model. External inspection usage
may remain unknown. Controller publication requires its verifiable session evidence.

## 2. Build and review original chunks

```sh
stage --stage chunking
stage --stage chunking-review
```

Give a fresh agent [`agent_prompt_chunking_review`](../src/knowledge_foundry/prompts/agent_prompt_chunking_review)
with the exact build paths and `chunking-review/review.json`. After inspection,
rerun `stage --stage chunking-review`. **All three gates must pass** before continuing.
Pending/failed reviews block downstream steps, regardless of process exit code.

## 3. Structural chunk and semantic cluster build

```sh
stage --stage structural-chunks-and-semantic-clusters --allow-paid
```

Require a complete validated build. Claims and summaries consume it independently;
there is no separate structural-review stage. [Settings/rebuild rules](structural-chunks-and-semantic-clusters.md).

## 4. Generate, classify and review claims

```sh
stage --stage claim-generation --allow-paid
stage --stage claim-classification --allow-paid
stage --stage claim-review
```

Generation/classification must be complete without errors. Give a fresh agent
[`agent_prompt_claim_review`](../src/knowledge_foundry/prompts/agent_prompt_claim_review)
with the exact builds and `claim-review/review.json`; then rerun `stage --stage claim-review`.
Automatic, review and overall gates must pass. Claims remain drafts; recorded
non-critical defects can remain within the unchanged policy thresholds.

## 5. Generate summaries independently

```sh
stage --stage summary-generation --allow-paid
```

Requires the structural build, not claims. Summaries remain drafts without an
independent fidelity gate. Indexing fills missing final-summary vectors. [Details](summaries.md).

## 6. Build the combined provenance graph

```sh
stage --stage provenance-graph
```

Inspect `$RUN_DIR/provenance-graph/graph.html`. Register the exact build directories
and this graph path under your corpus/run in `config/retrieval-corpora.json`
([template](retrieval-rag.md#other-corpora-and-commands)). The chatbot embeds matching
local HTML without another server.

## 7. Import vectors into an unpublished snapshot

```sh
direnv exec . .venv/bin/python scripts/index_artifacts.py migrate
stage --stage indexing --allow-paid
```

Require `vectors == records` for every kind in `indexing/stdout.log`. Import reuses
compatible vectors/cache and fills gaps without changing upstream artifacts.
Its saved snapshot ID is used automatically for retrieval and explicit publication.

## 8. Evaluate before publication

Select matching cases and acceptance evidence in the workflow config before the
run. Development cases are diagnostic, not approved labels.

```sh
stage --stage retrieval-evaluation --allow-paid
```

Every case must complete; each mode's combined hit rate must reach 0.875. Inspect
semantic relevance against originals; page overlap/anchors alone cannot approve.
The current 22 cases are unreviewed development data and `retrieval_acceptance`
is null, so publication remains blocked.

## 9. Explicitly publish, query and start chat

```sh
stage --stage publication
```

Publication requires all fresh checks, source-checked acceptance and independent
session evidence. External manual judgments alone cannot satisfy controller session
verification; use `--agent-review` for managed reviews. The low-level
`index_artifacts.py publish` command remains an operator-controlled alternative
requiring the same checks; it does not update controller state. Prior snapshots remain.
For queries/chat, follow [RAG setup](retrieval-rag.md) with this corpus/snapshot and
`config/retrieval-multilingual.json`.

## Failures and reruns

Correct the underlying defect before rebuilding; retain findings and thresholds.
For an unchanged-code/config rebuild:

```sh
stage --stage parsing --rebuild \
  --repair-note "Corrected the parsing prerequisite; rebuild and request fresh review"
```

Previous parsing outputs/audit/review move automatically to
`history/parsing/<execution-start-UTC>/`, with `outputs/`, `automatic-review/`,
`review/` and relocation metadata. Affected downstream stages are also archived;
only rebuilt, passing current outputs can be consumed. UTC names include fractional
seconds to avoid collisions; retry counts remain in state. Archived bytes/JSON are
unchanged, so embedded original paths are historical; use the saved relocation map.

Code/config/source/model changes require repair from ingestion or a fresh run:

```sh
stage --stage ingestion --rebuild \
  --repair-note "Corrected parser implementation; invalidate affected outputs and reviews"
```

Then execute required stages again explicitly. Interrupted generation/classification
reuse checkpoints; pending reviews retain judgments. Other interrupted stages
archive before restarting. Repeated lack of progress exhausts bounded retries and
needs escalation. Shared caches remain outside run directories. Existing standalone
outputs are not moved/imported automatically; use exact paths in their original
review process or start a managed run. [Automated workflow](resumable-pilot-workflow.md).
