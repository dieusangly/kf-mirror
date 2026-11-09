# Document and Reasoning assistants

Run `.venv/bin/python -m streamlit run apps/chat/app.py`.
**Document assistant** retrieves original passages and can generate cited answers.
**Reasoning assistant** interprets French/English questions over accepted JSON
mappings, checks their proofs and evidence, then generates and assesses an
explanation. Suggested buttons and typed questions use the same path.

Each mode has a separate PostgreSQL history under the private `chat_token` URL.
Keep that URL private. Switching modes or reloading retains history; reset creates
an empty conversation in the selected mode. Existing conversations use Document mode.

## Setup

Configure PostgreSQL/pgvector using the [pipeline runbook](aml-cft-pipeline.md#7-rebuild-the-postgresql-search-projection).
Install the dependencies and build the pinned checker using [reasoning setup](reasoning-engine.md#setup-and-example), then retain it locally:

```sh
.venv/bin/python scripts/setup_reasoning_assistant.py install-checker --source-dir /tmp/kf-open-ontologies
.venv/bin/python scripts/setup_reasoning_assistant.py validate
# Optional: import accepted evidence as a ready snapshot; does not publish it.
.venv/bin/python scripts/setup_reasoning_assistant.py import
```

The import targets `aml-cft-reviewed-assistant`. Reasoning can also use a published
pilot snapshot whose original proof spans and source metadata match exactly.
Importing evidence does not approve the rest of the corpus. The normal document
importer keeps its strict validation.

No new migration is needed for natural-language questions. If an installation
needs existing history migrations, first create and verify a fresh backup:

```sh
source .envrc
mkdir -p artifacts/db
BACKUP="artifacts/db/knowledge-foundry-$(TZ=Europe/Paris date +%Y%m%d-%H%M%S).dump"
pg_dump --dbname="$KF_DATABASE_URL" --format=custom --file="$BACKUP"
pg_restore --list "$BACKUP" > /dev/null
pg_restore --file=/dev/null "$BACKUP"
sha256sum "$BACKUP"
.venv/bin/python scripts/index_artifacts.py migrate
```

Retain every backup and `artifacts/reasoning-checker/`. Archive checks do not prove
restore success; test restores only in isolated databases/schemas.

## Configuration

| Setting | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Required for language interpretation/explanation; Document excerpts work without it. |
| `KF_REASONING_CONFIG` | Defaults to [config/reasoning-assistant.json](../config/reasoning-assistant.json). |
| `KF_REASONING_DATASET` | Defaults to `examples/reasoning-assistant/accepted`. |
| `KF_REASONING_OUTPUT` | Defaults to `artifacts/reasoning-assistant-runs`; fresh proof directories only. |
| `OO_HORN` | Explicit checker override; otherwise project installation, then PATH. Invalid overrides do not fall back. |

Interpretation uses `gpt-6-luna` / `medium`; explanation uses `gpt-6-luna` / `low`.
Both are supported by the [official model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna).
A successful turn makes four requests: interpretation, interpretation assessment,
explanation and explanation assessment. Assessments use interpretation settings;
Document answer preferences do not change these settings. Technical details retain
phase usage, estimated cost, latency and failures. Requests use `store=false` and
have no automatic retries.

Model output is bounded by the smaller of its phase limit and retrieval output
budget. Complete prompts, evidence counts, query work/results, inference iterations
and checker time have explicit limits. Missing credentials, timeouts, incomplete
responses and failed checks withhold reasoning answers; Document mode stays usable.

## Questions and proof boundaries

Queries check facts/deductions or list finite, connected matches over known terms.
There is no generated code/SQL/SPARQL, general negation, aggregation, numerical
comparison, rule creation or extraction of scenario assumptions. Missing facts
remain unknown. Definitions need mappings; document text alone is insufficient.

Examples over the three accepted mappings:

| Question | Supported result |
| --- | --- |
| La demande de Madame Z contient-elle sa dernière déclaration d’IFI ? | Included in the source-described application, not a verified customer fact. |
| What does Basel recommend for people acting on a customer’s behalf? | Inclusion in the represented recommended CDD scope. |
| Does a COSI provide an exemption from liability? | The source explicitly states non-exemption; actual liability is not determined. |

Lists of Basel targets and the existing two-premise conjunction rules are also
supported. These examples are not an allowlist. The accepted FR/BCBS interval,
**2026-10-28** only, is a source-description snapshot boundary, not a legal effective
date. Dates require `YYYY-MM-DD`; malformed interpretations get a retry message.
Dates are never silently changed, and saved failures remain unchanged after retry.

Accepted mappings and their [separate reviews](../examples/reasoning-assistant/review/)
are immutable; semantic changes require fresh independent review. Loading uses
local JSON; PostgreSQL supplies snapshot evidence and conversation history.

Cached reasoning is reused, but each new answer rechecks certificate bytes/logical
steps and requires complete proof evidence matching source versions, scope, filters
and budgets. Contradictions, unchecked proofs and missing evidence withhold answers
without switching modes. Headings supply context, never substantive citations.
See [review and proof boundaries](reasoning-engine.md#workflow-and-review-gates).

Answers show a direct response, **Pourquoi cette réponse ?** / **Why this answer?**
steps and original excerpts. Technical details are collapsed. Certificates check
deductions under supplied assumptions, not source truth, legal applicability or
wording. Model assessments of interpretation/prose are fallible. Saved history
records its original run rather than rechecking old answers.

## Experimental drafts and testing

For this run, start with the [revised formalization corpus](../artifacts/20261028-docling-aml-cft-01/formalization-v2/README.md).
Only `formalization-v1` (unchanged frozen baseline) and `formalization-v2`
(revised corpus and archived provenance) remain. The initial full review
covered all 8,491 claim candidates and all 2,262 original mappings. Review after
repair covered 1,081 claim/disposition records and 273 mappings, followed by
further repairs. The other mappings were not ignored.

The separate final acceptance decision covers four exact mapping revisions.
The other 2,164 active mappings retain their review/repair history without that
final acceptance; 104 date mappings remain withheld. Earlier review judgments
do not automatically approve later revisions. See the package's
[review coverage and acceptance](../artifacts/20261028-docling-aml-cft-01/formalization-v2/README.md#acceptance-and-input-selection).
Application use stays disabled.
This does not change the separate three-mapping accepted assistant dataset above.
The package [defines claims, candidates and mappings](../artifacts/20261028-docling-aml-cft-01/formalization-v2/README.md#claims-candidates-and-mappings):
“candidate” identifies an upstream proposal; “claim” identifies its exported record.

Choose the input according to its purpose:

| Use | Input |
| --- | --- |
| Inspect the latest revised claims and their lineage | `formalization-v2/current/input-bundle.json`, with `current/coverage.jsonl` and `current/mapping-index.jsonl` for status |
| Reproduce original draft experiments | `formalization-v1`; findings resolve from the v2 archive |
| Run the Reasoning assistant | `examples/reasoning-assistant/accepted`, with its exact reviewed claims and mappings |
| Run the Document assistant | The configured published retrieval snapshot, imported independently of the formalization package |

The revised corpus includes 711 changed claim records, including evidence
and provenance changes. The three original claim records underlying the accepted
assistant are unchanged in v2. Do not point `KF_REASONING_DATASET` at v2 or silently
substitute revised claims into a reviewed export: changed claim bytes require
matching reviews, mappings and evidence before a separately authorized application
update. The name `v2` identifies a corpus snapshot, not application approval.

The explicit offline draft harness uses synthetic model responses and private
in-memory snapshots:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/setup_reasoning_assistant.py experiment \
  --draft-dir artifacts/20261028-docling-aml-cft-01/formalization-v1 \
  --output /tmp/kf-draft-questions
```

Use a fresh output directory. Optional `--case-id`, `--cases` and `--checker` select
fixtures or the checker. Results are labelled **Experimental — unreviewed formalization**.
The label reflects the frozen draft export's status; later review findings are
retained as provenance and do not grant the harness runtime approval. Original
statuses and hashes are preserved. Facts/rules remain experimental assumptions
even when a certificate passes. The report records mappings, coverage
limits and failures. There is no automatic fallback, draft promotion or production
batch importer.

When the sibling review directory is absent, the harness reads its exact findings
and review manifest from `formalization-v2/provenance/history.zip`, using
`provenance/index.json`. It verifies the package seal, index/archive hashes,
member identity and hashes, and the review's binding to the original draft.
The report retains the exact findings hash and an `archive.zip!/member` location.
Loose reviews still work when their manifest binds the draft and findings;
missing or inconsistent provenance fails loading.

Archive indexes and restoration commands are documented in the package's
[provenance section](../artifacts/20261028-docling-aml-cft-01/formalization-v2/README.md#provenance-and-restoration).
Archived helpers are data and must not be executed. Historical paths and input
pins in accepted reviews remain unchanged; they identify their original snapshots.

```sh
# Tests create/remove unique schemas; never migrate the application database.
export KF_TEST_DATABASE_URL="$KF_DATABASE_URL"
unset OPENAI_API_KEY
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:.:tests .venv/bin/python -m pytest -q
```

PostgreSQL tests skip without the test URL; certificate tests require the checker.
Automated/browser model responses and semantic assessments are mocked. Live model
quality, account access and actual API cost/latency remain unverified. Local logs,
proofs and screenshots are retained in [QA artifacts](../artifacts/reasoning-question-validation/)
and [timing artifacts](../artifacts/reasoning-speed/), which Git ignores.
