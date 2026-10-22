# Resumable AML/CFT workflow

All 17 saved files use one ingestion pipeline. The [source inventory and evaluation
notes](french-aml-cft-pilot-sources-and-evaluation.md) list the corpus and ingestion
commands; `data/manifest.json` holds source metadata and coverage
decisions. All saved filenames use their manifest ID prefix.

Prerequisites: repository Python environment, Poppler (`pdfinfo`, `pdftotext`,
`pdftoppm`), authenticated Codex CLI with writable runtime storage and explicit
reviewer model/reasoning settings, existing API credentials, PostgreSQL/pgvector
and existing migrations. The [underlying runbook](pipeline-runbook.md) documents
individual stage commands.

```sh
# Offline ingestion only; no paid stages or reviewer sessions.
direnv exec . .venv/bin/python scripts/workflow.py run \
  --run-id aml-cft-pilot --stop-after ingest --max-seconds 300 \
  --max-paid-stages 0 --max-review-sessions 0

direnv exec . .venv/bin/python scripts/workflow.py status --run-id aml-cft-pilot

# Continue only when paid execution is authorized.
direnv exec . .venv/bin/python scripts/workflow.py resume \
  --run-id aml-cft-pilot --allow-paid --max-seconds 14400 \
  --max-paid-stages 6 --max-review-sessions 4 --max-stage-attempts 3
```

Stages: ingestion → parsing → parsing evaluation → chunking → independent chunk
review → structural chunks/semantic clusters → summaries and claim generation /
classification → independent claim review → provenance graph → indexing → retrieval
evaluation. Existing scripts, prompts, thresholds and compatible caches are reused.

Both reviews use fresh independent read-only Codex sessions. Reviewers inspect saved
originals and rendered PDF pages, and can request additional pages while pending.
Initialization can exit zero while pending; automatic, review and overall gate fields
control progress. Uninspected judgments, stale reviews, discarded findings and model
substitutions are rejected. Explicit TOML configuration currently resolves to
`gpt-6.1-sol`, reasoning `high`, service tier `default`; each session resolves again,
pins settings and records configuration hashes, thread identity and available usage.
Unsupported configuration layering fails closed.

Failed reviews preserve findings in `repair-task.json`. The controlling agent inspects
evidence, corrects the underlying defect and resumes with `--repair-from chunk` (or
the affected builder) and a concrete `--repair-note`. Review-only resets after failure
are rejected. Code/config/source changes require `--repair-from ingest` or a new run.
Downstream outputs are invalidated; earlier attempts and snapshots remain preserved.
Interrupted resumable builders use existing checkpoints.

State in `artifacts/workflow/<run-id>/state.json` records input identities, paths,
attempts, failures and measurements. A lock prevents concurrent execution of the
same run. Budgets bound cumulative seconds, paid stage starts, review sessions and
stage attempts; resume/repair retains counters. They are not hard dollar/token caps;
unavailable cost measurements remain null. Ambiguity, inaccessible evidence, scope
expansion and repeated lack of progress require escalation, not threshold changes.

Retrieval development checks cover every saved file and both languages. PostgreSQL
`simple` text search avoids applying French stemming to English; multilingual
embeddings support cross-language matching. Keyword/vector baselines remain diagnostic.
The combined hit-rate target remains 0.875 in each passage/claim/summary mode.

Publication is explicit and local. It requires complete fresh gates, real independent
review evidence and `data/retrieval-expanded-acceptance.json` with `status: reviewed`,
a concrete reviewer and matching manifest/cases hashes. Fixtures and development
labels cannot authorize publication. After those checks:

```sh
direnv exec . .venv/bin/python scripts/workflow.py publish \
  --run-id aml-cft-pilot --max-seconds 14400
```

Earlier runs and snapshots retain their original source/input identities. Reviews
apply to their exact inputs. The PPE historical-body limitation is recorded in source
metadata; quality-review gates remain unchanged.

Source paths and manifest configuration are part of run identity. After a rename or
manifest change, start a new run or resume with `--repair-from ingest` and a concrete
`--repair-note`; earlier artifacts/reviews retain their original identities.
