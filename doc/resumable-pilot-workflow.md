# Resumable AML/CFT workflow

The [17-file scope](french-aml-cft-pilot-sources-and-evaluation.md) shares one
pipeline, including the accepted historical PPE limitation. See [manual commands](pipeline-runbook.md).

Prerequisites: Python environment, Tesseract with French/English data, Poppler,
authenticated Codex CLI, explicit reviewer model/reasoning settings, API credentials,
and migrated PostgreSQL/pgvector.

## Commands and budgets

```sh
RUN_ID="YYYYMMDD-automated-workflow"  # replace YYYYMMDD with your run date
# Offline preflight only.
direnv exec . .venv/bin/python scripts/workflow.py run \
  --run-id "$RUN_ID" --stop-after ingest --max-seconds 300 \
  --max-paid-stages 0 --max-review-sessions 0

direnv exec . .venv/bin/python scripts/workflow.py status --run-id "$RUN_ID"

# Example limits; execute only within the authorized budget.
direnv exec . .venv/bin/python scripts/workflow.py resume \
  --run-id "$RUN_ID" --allow-paid --max-seconds 14400 \
  --max-paid-stages 6 --max-review-sessions 4 --max-stage-attempts 3

direnv exec . .venv/bin/python scripts/workflow.py report --run-id "$RUN_ID"
```

Limits are cumulative across resume/repair: time, paid-stage starts, sessions and
attempts, not dollar/token caps. Allow at least three reviews plus continuations
or external sessions. Execute only with authorization/budgets; escalate ambiguity,
inaccessible evidence or repeated lack of progress.

## Stages and reviews

Ingestion → parsing → automated parsing audit → independent parsing review →
chunking → independent chunk review → structural chunks/semantic clusters →
summaries and claim generation/classification → independent claim review →
provenance graph → indexing → retrieval evaluation → explicit publication.

For new runs, parsing review initialization performs the automatic audit and records
its required checkpoint; no separate audit process or manual command is needed.

Reviews use fresh read-only Codex sessions, saved originals and PDF page images.
`auto_review_{parsing,chunking,claims}.py` audits artifacts/scores judgments;
`agent_prompt_*_review` directs inspection. See [parsing coverage](parsing-evaluation.md#independent-parsing-review).
Initialization can exit zero while pending: automatic, review and overall gates
must pass. Fixtures, uninspected judgments and substituted models cannot approve.

Configure `reviewers.parsing_review`, `chunk_review` and `claim_review` in
`config/workflow.json`: `model`, `model_reasoning_effort`, optional `model_provider`
and `service_tier`. Workflow values override explicit system/user/ancestor-project
Codex TOML settings. Missing model/effort, unknown keys and unsupported profiles/catalogs
fail closed; there is no model fallback. Manual sessions must be launched with
these settings explicitly; the prompts cannot select their own running model.

Settings, configuration hashes, thread IDs and usage are pinned in state/report
and session measurements. Disposable private runtime settings/authentication are
removed afterward. Parsing retains the workflow run ID.

Pending continuations carry only unchanged judgments with verified prior responses,
measurements, threads, settings and identities. Attribution remains original;
missing/stale history or revised judgments require reinspection. Preserve findings.

## Repair and publication

Inspect `repair-task.json`. Resolve pending evidence/inspection; after failure,
correct the defect, rebuild affected outputs and request fresh review. Use
`--repair-from chunk` (or affected builder) with a concrete `--repair-note`.
Code/config/source/reviewer changes require repair from `ingest` or a new run;
review-only resets after failure are rejected. Downstream outputs invalidate;
caches, prior attempts and snapshots remain. Generation/classification can resume checkpoints.

Retrieval uses multilingual embeddings and PostgreSQL `simple` search. Each mode's
combined hit-rate must reach 0.875; baselines are diagnostic. Publication requires
fresh reviews and a source-checked acceptance record bound to manifest/case hashes.
`retrieval_acceptance` is currently `null`, blocking publication; development/fixture
labels cannot approve. After all checks:

```sh
direnv exec . .venv/bin/python scripts/workflow.py publish \
  --run-id "$RUN_ID" --max-seconds 14400
```

## Saved files and costs

`artifacts/<run-id>/` holds state, consolidated `report.json`, a lock and
per-stage `attempt-N` commands, logs, bundles, gate reports and sessions. PDF bundles
retain layout JSON/figure PNGs. Failed attempts remain; diagnostic/staging replays
are not approved stages. `artifacts/` is ignored by Git.
Stage folders use the names in the [manual runbook](pipeline-runbook.md), e.g.
`ingestion/attempt-N/report.json`, `parsing/attempt-N/` and
`parsing/automatic-review/attempt-N/report.json`. Automated runs retain immutable
attempts referenced by state. Bundle files sit directly in each stage/attempt;
execution logs are attached under `execution/` after a builder finishes. Existing `artifacts/workflow/<run-id>/` runs remain
readable in place, including their existing `build/` paths; duplicate run IDs fail closed.

Manual `workflow.py step --stage ...` runs use stable stage folders and automatically
archive replaced/invalidated results under timestamped `history/`. They execute only
the selected stage. Use distinct IDs such as `YYYYMMDD-manual-workflow` and
`YYYYMMDD-automated-workflow`; mode mixing is rejected. Resume/checkpoints, stale
checks, budgets and publication requirements apply to both modes.

Retain referenced evidence/frozen outputs. Retiring a run requires resolving its
references and retaining evidence or explicitly retiring approvals. Deletion or
fresh parsing never transfers approval or resets legitimate budgets.

The report updates at checkpoints/completion/failure/interruption; hard kills leave
the last checkpoint. `report` regenerates under lock without approving; `status`
checks staleness. Stage reports stay with bundles. `cost.known_subtotal_usd` sums
saved model/API estimates; `estimated_total_usd` stays null while costs are unknown.
Usage is deduplicated, including external sessions and resume; recovery carryovers,
copied costs and projections are excluded. Saved rate cards price summaries.
Missing/malformed usage stays visible. Estimates exclude hardware/subscriptions
and are neither invoices nor forecasts.
