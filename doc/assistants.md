# Assistant

Assistant combines original document passages with checked accepted knowledge.
Users ask questions in French or English without selecting an engine. The brief
**Experimental POC** notice reflects limited coverage; some answers remain partial,
unsupported or withheld. This is not a production-readiness claim.

The [design](assistant-poc-design.md) defines orchestration and failure boundaries.
The [exact configuration](../config/assistant-poc.json) pins two published snapshots:

| Capability | Pinned knowledge |
| --- | --- |
| Document retrieval | Full `aml-cft-pilot-13` snapshot, 13 source documents, 13,735 navigation records and 13,893 evidence spans |
| Accepted knowledge | Already active `reasoning-poc-01` snapshot, all 2,164 accepted mappings and 3,506 projected spans across nine documents |

The original POC reference still resolves to the frozen `reasoning-release-01`
export. There is no automatic corpus selection, production release activation or
three-mapping substitution. A recovered accepted heading remains governing
context only; it is explicitly distinguished from the 3,505 overlapping spans.
Document versions, source identities and original text are checked at admission
and citations are checked again per turn. All filters apply to both capabilities.

Definitions and explanations use full-document retrieval without requiring a
mapping. Explicit accepted-fact checks and logical consequences use the existing
reasoning interpreter, audits, engine, complete proof evidence and checker.
Source assertions are distinguished from deductions. A deduction requires a
passed certificate under its stated premises and rules. Model assessments remain
fallible and certificates do not establish source truth or current applicability.

Multipart questions preserve independently supported parts. An explicit coverage
gap permits fresh, independently validated documentary background with the
checked conclusion identified as unestablished. Citation, certificate, integrity,
scope and audit failures never authorize fallback or reuse of a rejected draft.

## Conversations

Use **New conversation** and **Conversations** to create, reopen and switch.
Each conversation belongs to its private URL token and keeps its original
knowledge binding. Keep that URL private. Filters are conversation-local UI
state and are recorded on each turn. Earlier user questions resolve references;
assistant prose never becomes evidence or a logical premise. Without a language
provider, standalone questions show exact excerpts and follow-ups ask clarification.

One submission saves exactly one user and one final assistant message atomically.
Submission UUIDs prevent duplicate saved pairs. Reopening history makes no model
calls. **Previous conversations** opens legacy histories read-only with their
original provenance; they are never adopted or merged into new conversations.
**Documents in corpus** retains the filtered snapshot inventory. Clickable
citations lead to original passages; **Diagnostics** contains the route, both
bindings, checks, usage and timings. Checked results also expose proof details.
New proof artifacts default to `artifacts/assistant-poc-runs`; `KF_REASONING_OUTPUT`
can select another POC directory. Existing proof/history artifacts are retained.

## Local checks and activation status

The recorded approved relaunch used <http://127.0.0.1:8501> in
**provider-off mode**. Source excerpts, conversation management and
the 13-document inventory work; referential follow-ups request clarification.
Generated answers and language-mediated checked reasoning require a separately
approved provider scope. The [relaunch report](../artifacts/20261028-docling-aml-cft-01/20261112-unified-assistant-poc/poc-application-relaunch-report.md)
records actual application/browser checks and the final three saved smoke pairs
across two conversations. The app remains gated by
`KF_ASSISTANT_POC_ENABLED`; no migration runs on launch. Backup/restore and rollback
records are under
[`20261112-unified-assistant-poc`](../artifacts/20261028-docling-aml-cft-01/20261112-unified-assistant-poc).

```sh
# From the repository root; no API key is needed for these offline checks.
unset OPENAI_API_KEY
export KF_TEST_DATABASE_URL=postgresql://127.0.0.1:55484/kf_reasoning_poc
export KF_ASSISTANT_POC_TEST_DATABASE_URL=postgresql://127.0.0.1:55484/kf_unified_assistant_poc
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src:.:tests:tests/assistant
.venv/bin/python -m pytest -q tests/assistant tests/storage tests/retrieval tests/reasoning
```

Language fixtures and audit booleans in offline/browser tests are explicitly
mocked. They establish application paths and enforcement, not live model quality.
Do not run checker suites concurrently: upstream protocol files have fixed names.
The browser launcher verifies the isolated database before starting:

```sh
.venv/bin/python -m streamlit run tests/assistant/poc_browser_app.py \
  --server.address=127.0.0.1 --server.port=18517 --server.headless=true \
  --browser.gatherUsageStats=false
# Another terminal:
.venv/bin/python tests/assistant/poc_browser_check.py
```

The fixed live proposal is `poc-live-batch.json`: ten submissions, at most 100
provider requests, 12,000 input / 1,600 output tokens per request, 1,200,000 /
160,000 batch tokens, USD 1 cap, zero automatic retries. Code/config hashes bind
the proposal. Only a newly approved matching scope may execute:

```sh
# Do not execute without new paid approval.
.venv/bin/python scripts/assistant_poc.py live --authorization /path/to/approved-poc-scope.json
```

The authorization must contain `approved: true`, the exact `scope_sha256` and
`approved_spending_cap_usd: 1.0`. The runner refuses automatic replay and records
all outcomes and unexecuted cases. Independent reading of actual answers,
passages and proof kind is required after model assessments. Cost estimates use
configured pricing and conservative per-request reservations; billing is separate.

## Approved launch and rollback procedure

Migration 004 is installed and the approved corrected relaunch is complete.
`poc-application-relaunch-authorization.json`, `poc-application-relaunch-postcheck.json`
and `poc-application-launch-status.json` record authorization and current status.
Do not automatically replay completed smoke submissions.

For a local provider-off restart, stop any existing instance first and use
this configuration; no migration command is needed:

```sh
source .envrc
unset OPENAI_API_KEY
export KF_ASSISTANT_POC_ENABLED=1
export KF_ASSISTANT_POC_CONFIG="$PWD/config/assistant-poc.json"
.venv/bin/python -B -m streamlit run apps/chat/app.py \
  --server.address=127.0.0.1 --server.port=8501 --server.headless=true \
  --browser.gatherUsageStats=false --server.fileWatcherType=none
```

Full language-backed interactive usage needs a separately approved provider scope;
the completed fixed paid batch itself ran only in the isolated database. Its
approval does not authorize additional or interactive paid calls. Code, binding
or application-write scope changes require a concrete revised approval.

Rollback: stop the new process, unset `KF_ASSISTANT_POC_ENABLED` and
`KF_ASSISTANT_POC_CONFIG`, and restart the prior app from the preserved
`poc-prior-app.py` source at its original `apps/chat/app.py` path. Retain additive
POC tables and all histories. Do not withdraw the already active reasoning POC,
change corpus pointers, publish the baseline or restore the whole application
for ordinary rollback. The original checker and frozen review/history artifacts
remain required; see [reasoning internals](reasoning-engine.md).

## Retained artifacts

The runtime dependency chain is `config/assistant-poc.json` →
`reasoning-poc-01/poc-config.json` → `reasoning-release-01/manifest.json`.
Both stage directories remain required despite the unified interface.

All three stage directories below are under
[`artifacts/20261028-docling-aml-cft-01/`](../artifacts/20261028-docling-aml-cft-01):

| Directory | Retained purpose |
| --- | --- |
| `reasoning-release-01/` | Accepted dataset, frozen manifest, grounding/formalization reviews, source lineage and validation evidence. The loader checks original frozen files; historical filenames do not imply disposable content. |
| `reasoning-poc-01/` | Active corpus reference, examples, verification, activation evidence and recovery backup. |
| `20261112-unified-assistant-poc/` | Unified assistant evaluations, proofs, launch authorizations, migration receipts, backup/restore evidence and `poc-prior-app.py` for rollback. |

The release also depends on the original formalization packages and their sealed
provenance. `scripts/reasoning_poc.py` imports the retained
`reasoning-release-01/checks/final-live-runtime.py` transport helper, and
`reasoning/poc.py` includes it in verification bindings. Keep
`artifacts/reasoning-checker/` and saved conversation proof directories, including
`artifacts/reasoning-assistant-runs/`.

The [relocation record](../artifacts/20261028-docling-aml-cft-01/20261112-unified-assistant-poc/poc-relocation.json)
maps historical paths in completed records. Preserve their original bytes and
audit hashes. Backup/restore evidence records restoration into disposable
PostgreSQL databases `kf_unified_assistant_poc` and `kf_assistant_launch_fix_poc`
on port 55484; migration 004 adds conversation tables while preserving corpus
and legacy-history tables.

The [cleanup index](../artifacts/20261028-docling-aml-cft-01/reasoning-cleanup-20261112/README.md)
locates archived stage harnesses, superseded diagnostics and earlier proof runs.
Runtime dependencies and final live/offline proof dependencies remain expanded.
The archive preserves original paths and bytes; historical manifest entries for
archived files resolve through its SHA-256 inventory. Restoring an old stage for
historical inspection is separate from current application operation.

Completed release-agent prompts and superseded `reasoning-speed/` and
`reasoning-question-validation/` experiments were already absent before this
cleanup. Frozen records may mention them; they are not current runbooks.
