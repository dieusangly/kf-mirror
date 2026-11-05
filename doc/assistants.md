# Document and Reasoning assistants

Run the app with `.venv/bin/python -m streamlit run apps/chat/app.py`. The sidebar defaults
to **Document assistant**, which preserves retrieval, source filters, budgets,
source excerpts and optional generated answers. **Reasoning assistant** offers a
closed question catalogue backed by reviewed facts/rules and checked proofs.
It makes no model calls. Free text must match a catalogue question exactly.

Both modes save separate PostgreSQL histories under the private `chat_token` URL.
Keep that URL private. Switching modes or reloading retains the histories;
reset creates a new conversation in the selected mode. Existing conversations
remain Document assistant conversations.

## Setup

Use the existing PostgreSQL/pgvector database settings in
[the pipeline runbook](aml-cft-pipeline.md#7-rebuild-the-postgresql-search-projection).
Before applying the additive history migration, make and verify a fresh backup:

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

Keep all backups. The archive checks read its directory and data stream; they do
not establish restore success. Test restores and migrations in an isolated
database/schema, never by restoring over the application database.

Install the pinned reasoning dependencies and build the matching `oo-horn`
checker using [reasoning setup](reasoning-engine.md#setup-and-example). Retain it
in the project so the normal Streamlit command finds it after restarts:

```sh
.venv/bin/python scripts/setup_reasoning_assistant.py install-checker --source-dir /tmp/kf-open-ontologies
.venv/bin/python -m streamlit run apps/chat/app.py
```

The installer checks the pinned clean source and records the binary hash under
`artifacts/reasoning-checker/`. Keep that directory with the artifact backups.
An explicit `OO_HORN` overrides the installation, including an invalid path;
unset a stale override. A missing/rejecting checker withholds reasoning answers
and shows a setup warning; Document assistant remains available.

Validate and import only the accepted subset, without embeddings or model calls:

```sh
.venv/bin/python scripts/setup_reasoning_assistant.py validate
.venv/bin/python scripts/setup_reasoning_assistant.py import
```

The second command creates/reuses an immutable **ready** snapshot in
`aml-cft-reviewed-assistant`; it does not migrate or publish. Review the returned
snapshot ID before any separately authorized publication. Reasoning can also use
an existing published pilot snapshot if every original proof span and its source
metadata match exactly. Merely importing a subset does not publish it or approve
the rest of a corpus. The normal document-pipeline importer retains its strict
classification and manifest validation.

Optional settings:

| Variable | Default / purpose |
| --- | --- |
| `KF_REASONING_DATASET` | `examples/reasoning-assistant/accepted`; validated accepted exports |
| `KF_REASONING_OUTPUT` | `artifacts/reasoning-assistant-runs`; append-only proof runs |
| `OO_HORN` | Optional checker override; otherwise use the project installation, then PATH |

## Supported pilot questions

The catalogue copies these development questions from the
[123-question fixture](../tests/fixtures/retrieval-evaluation-123q.json). Its
original `synthetic_unreviewed` evaluation labels are unchanged. Separate review
accepts only the bounded source descriptions, not the evaluation dataset.

| ID | Question / original source |
| --- | --- |
| V2-009 | Does filing a COSI remove the need to submit a suspicious transaction report or exempt the institution from liability? ACPR–Tracfin, `2025-04`, PDF p49 §§194–195; French citations. |
| V2-023 | Under the Basel guidance, does customer due diligence extend beyond the account customer to representatives and beneficial owners? Basel, `2014-01-revised-2020-07`, PDF p12 (printed p8) §35 and notes 22–23; English citations. |
| V2-013 | Dans le cas de Madame Z et de sa SCI, quels documents accompagnent la demande de prêt pour acquérir un nouveau bien immobilier ? Tracfin, `2025-09-29-tome-3`, PDF p18, cas n°3 étape 2; French citations. |

Answers use the question's language and keep original-language passages. Each
question names one fixed goal. Reviewed conjunction rules join two explicit
source assertions: no reporting/liability exemption; representatives and owners
within recommended CDD scope; the estimate and latest IFI return in the reported
loan application. These are genuine bounded Horn deductions, not application of
legal duties to new cases. No customer assumptions are supplied.

The one-day execution scope is **2026-10-28**, with FR or BCBS identifying the
source jurisdiction. It is a source-description snapshot boundary, **not a legal
effective date**. COSI does not establish automatic DS or actual liability; Basel
recommendations do not decide national exemptions; Madame Z is a source-described
case, not a verified customer or completed purchase. The accepted catalogue
contains exactly these three supported questions. The original five-entry review
input is archived unchanged as
[reviewed-catalogue.json](../examples/reasoning-assistant/proposals/reviewed-catalogue.json).
Its two rejected questions remain in the review history. Runtime validation binds
each retained entry to its independent review hash. Other questions are withheld.

## Review and proof boundaries

[Grounding judgments](../examples/reasoning-assistant/review/grounding.json) and
[formalization judgments/findings](../examples/reasoning-assistant/review/formalization.json)
come from separate review agents that inspected original PDF text and layout.
This separates review contexts; it does not establish model-level independence
or human/legal approval. Proposals, review hashes, original draft claims and
accepted copies are retained under
[examples/reasoning-assistant](../examples/reasoning-assistant/).

The accepted export changes only copied grounding statuses and mechanical
bundle/claim bindings after both reviews pass. Frozen pilot drafts, dispositions,
prior findings and corpus acceptance remain untouched. Changed semantic content,
answers, goals or scope need fresh independent review. There is no corpus loader
or automatic prose-to-rule/goal conversion.

The answer displays conclusion, premises, applied rules, jurisdiction/date,
original passages and certificate status/verdict. Source assertions, supplied
rule assumptions, deductions and reviewed explanatory text are distinguished;
no LLM explanation is used. Saved history shows the original run's certificate
status, not a newly checked answer.

A closure is reused for unchanged dataset bytes, snapshot, scope and checker.
Every new supported answer rechecks certificate hashes and logical steps, then
requires the entire proof evidence inside the selected snapshot, filters and
budgets. Headings remain mandatory governing context, never substantive citation
labels. Missing evidence means unknown. Contradictions, unchecked proofs, altered
certificates and unsupported questions withhold answers without switching modes.

## Validation

```sh
# Tests create and remove unique schemas; do not point at an untrusted database.
export KF_TEST_DATABASE_URL="$KF_DATABASE_URL"
PYTHONPATH=src:.:tests .venv/bin/python -m pytest tests/reasoning tests/storage tests/retrieval -q
```

Without `KF_TEST_DATABASE_URL`, PostgreSQL/UI integration tests skip. Certificate
tests require the real checker. Synthetic missing-premise/conflict mutations are
explicitly identified in tests and never enter the accepted export.

Validated 5 November 2026: full regression **905 passed, 6 skipped, 133 subtests**,
with one existing Docling warning. PostgreSQL checks used isolated schemas;
the real pinned checker accepted deductions and rejected certificate mutations.
Latest cleanup validation: **90 reasoning/storage/UI regressions** and **7
catalogue-integrity checks** passed. All three questions also returned checked
answers in a browser launched with `OO_HORN` unset; unused questions were absent.
Playwright/Chromium exercised both modes, French/English answers, reload history,
the supported catalogue and source-filter refusal at desktop size; a fresh mobile
viewport also rendered correctly. No paid model calls were made. Live generated
answers, a database restore and other browsers were not verified.
