# AML/CFT reasoning pilot

Use this guide to find accepted cases or run a new reasoning batch. Run directories
are relative to `artifacts/aml-cft-reasoning-pilot/`. For runtime integration,
see the [assistant guide](aml-cft-reasoning-assistant.md).

## Progress and next action

All three batches at dataset version 1.0.0 passed independent Stage 4 review,
covering 121 cases in French and English (242 language versions).

### Find accepted cases and sources

Each JSONL file has one record per `case_id` and `language`. The input
files hold the scope, `scenario_facts` and `policy_clauses`. The acceptance
records pin the files used by the [dataset registry](../config/aml-cft-reasoning-datasets.json).

| Dataset | Cases / FR and EN versions | Inputs and questions | Reference answers and proofs |
| --- | --- | --- | --- |
| Synthetic | 96 / 192 | [Inputs](../artifacts/aml-cft-reasoning-pilot/01-dataset-run-001/inputs-v1.jsonl), [questions](../artifacts/aml-cft-reasoning-pilot/01-dataset-run-001/questions-v1.jsonl) | [Accepted references](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-001/review-001/accepted-references-v1.jsonl) with answers and proofs together |
| BO | 9 / 18 | [Inputs](../artifacts/aml-cft-reasoning-pilot/05-real-claims-run-001/inputs-v1.jsonl), [questions](../artifacts/aml-cft-reasoning-pilot/05-real-claims-run-001/questions-v1.jsonl) | [Answers](../artifacts/aml-cft-reasoning-pilot/05-real-claims-run-001/proposed-answers-v1.jsonl), [proofs](../artifacts/aml-cft-reasoning-pilot/05-real-claims-run-001/proposed-proofs-v1.jsonl) |
| KYC | 16 / 32 | [Inputs](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-003/recheck-001/replay-001/pipeline/readable/inputs-v1.jsonl), [questions](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-003/recheck-001/replay-001/pipeline/readable/questions-v1.jsonl) | [Answers](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-003/recheck-001/replay-001/pipeline/readable/proposed-answers-v1.jsonl), [proofs](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-003/recheck-001/replay-001/pipeline/readable/proposed-proofs-v1.jsonl) |

Codex author sessions drafted the cases and reference solutions in Stages 1
and 5. The BO and KYC cases pair verified legal passages with synthetic customer
facts. Local Python scripts wrote the JSONL files without paid model calls.
The engine computed results and certificates from the formalized cases.
Independent review accepted the pinned reference files, including those with
the `proposed-` prefix. See [Stage 4 review](#review-and-repair).

#### Find the additional BO and KYC references

Find the dataset by `id` in the registry and open its `source_index_path`.
Under `sources`, entries with a `capture.path` starting with `sources/`
identify added captures. Resolve each path from the dataset directory, one
level above the index. Use a rule's `passage_ids` to look up excerpts,
locators and source IDs under `passages`.

| Dataset ID | Added references and where to find them |
| --- | --- |
| `real-claims-bo-v1` | The [BO source index](../artifacts/aml-cft-reasoning-pilot/05-real-claims-run-001/sources/sources-v1.json) lists `CMF-R561-1` and `CC-L233-3`. Each points to its own `.fr.txt` capture. |
| `real-claims-kyc-v1` | The [accepted KYC source index](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-003/recheck-001/replay-001/pipeline/readable/sources/sources-v1.json) points to 11 references keyed by source ID in [captures-v1.json](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-003/recheck-001/replay-001/pipeline/readable/sources/captures-v1.json). |

The contributor catalogue lists 26 sources. The 13 documents in the
[corpus manifest](../data/manifest.json) went through [document structuring, claim generation and summaries](aml-cft-pipeline.md).
The other 13 entries are versioned captures from the BO and KYC reasoning batches.

### Synthetic evidence

Batch `synthetic-v1-batch-001`.

- **Stage 1.** Preparation passed for all eight families, with 12 cases each. [Run](../artifacts/aml-cft-reasoning-pilot/01-dataset-run-001/run.json) · [dataset v1.0.0](../artifacts/aml-cft-reasoning-pilot/01-dataset-run-001/dataset-manifest-v1.json) · [acceptance evidence](../artifacts/aml-cft-reasoning-pilot/01-dataset-run-001/preparation-acceptance-v1.json)
- **Stage 2.** The pinned setup passed 27 controls and reproduced the run deterministically. [Batch](../artifacts/aml-cft-reasoning-pilot/02-engine-setup-run-001/execution-batch.json) · [pinned setup run](../artifacts/aml-cft-reasoning-pilot/02-engine-setup-run-001/run.json) · [setup evidence](../artifacts/aml-cft-reasoning-pilot/02-engine-setup-run-001/setup-validation-v1.json) · [handoff](../artifacts/aml-cft-reasoning-pilot/02-engine-setup-run-001/handoff.json)
- **Stage 3.** Encodings and queries passed structural validation for all 192 versions. All 11 controls passed and the fresh replay matched. [Run](../artifacts/aml-cft-reasoning-pilot/03-formalization-run-001/run.json) · [encodings](../artifacts/aml-cft-reasoning-pilot/03-formalization-run-001/encodings-v1.jsonl) · [queries](../artifacts/aml-cft-reasoning-pilot/03-formalization-run-001/queries-v1.jsonl) · [validation](../artifacts/aml-cft-reasoning-pilot/03-formalization-run-001/validation-v1.json) · [failures](../artifacts/aml-cft-reasoning-pilot/03-formalization-run-001/failures-v1.json)
- **Stage 4 Execute.** The engine ran all 192 versions. Every proof passed, every status matched its proposed reference and the replay reproduced the results. [Run](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-001/run.json) · [results and certificates](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-001/results-v1.json) · [proof checks](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-001/proof-checks-v1.json) · [provisional comparisons](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-001/provisional-comparisons-v1.json) · [failures](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-001/failures-v1.json)
- **Independent review.** All cases passed on the initial review, with zero findings and both repair rounds unused. [Authoritative status](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-001/evaluation-status.json) · [acceptance/hashes](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-001/review-001/acceptance.json) · [independent judgments](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-001/review-001/independent-judgments.jsonl) · [verdicts](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-001/review-001/per-case-verdicts.jsonl) · [scores](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-001/review-001/initial-scores.json)

### Beneficial ownership evidence

Batch `real-claims-bo-v1-batch-001`.

- **Stage 5.** Source bindings and bilingual structure passed validation. Answers and proofs were provisional at this stage. [Run](../artifacts/aml-cft-reasoning-pilot/05-real-claims-run-001/run.json) · [dataset](../artifacts/aml-cft-reasoning-pilot/05-real-claims-run-001/dataset-manifest-v1.json) · [validation](../artifacts/aml-cft-reasoning-pilot/05-real-claims-run-001/validation-v1.json)
- **Stage 2.** The reused setup passed 11 controls, replayed 24 accepted synthetic BO versions and supported separate answer and proof files. Stage 3 required new text and query mappings. [Batch](../artifacts/aml-cft-reasoning-pilot/02-engine-setup-run-002/execution-batch.json) · [run](../artifacts/aml-cft-reasoning-pilot/02-engine-setup-run-002/run.json) · [validation](../artifacts/aml-cft-reasoning-pilot/02-engine-setup-run-002/setup-validation-v1.json) · [handoff](../artifacts/aml-cft-reasoning-pilot/02-engine-setup-run-002/handoff.json) · [Stage 3 prompt](../artifacts/aml-cft-reasoning-pilot/02-engine-setup-run-002/next-session-prompt.txt)
- **Stage 3.** All 18 versions passed structural validation and the fresh replay matched. [Run](../artifacts/aml-cft-reasoning-pilot/03-formalization-run-002/run.json) · [validation](../artifacts/aml-cft-reasoning-pilot/03-formalization-run-002/validation-v1.json) · [failures](../artifacts/aml-cft-reasoning-pilot/03-formalization-run-002/failures-v1.json) · [Stage 4 Execute prompt](../artifacts/aml-cft-reasoning-pilot/03-formalization-run-002/next-session-prompt.txt)
- **Stage 4 Execute.** All 18 versions executed and passed proof checks. [Run](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-002/run.json) · [local replay](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-002/review-001/verification/replay-comparison.json)
- **Independent review.** Initially 7/9 cases and 14/18 versions passed. After one repair, all cases, versions and six categories passed. Both missing-completion controls passed and finding BO-S4-R1-001 closed. One repair round was unused. [Status](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-002/evaluation-status.json) · [acceptance](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-002/recheck-001/acceptance.json) · [recheck scores](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-002/recheck-001/post-repair-scores.json) · [handoff](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-002/recheck-001/handoff.json) · [Assistant integration](aml-cft-reasoning-assistant.md#source-backed-beneficial-ownership)

### KYC evidence

Batch `real-claims-kyc-v1-batch-001`.

- **Stage 5.** The author verified source bindings, record hashes and offline replay for the 6 July 2026 assessment. [Dataset](../artifacts/aml-cft-reasoning-pilot/05-real-claims-run-002/dataset-manifest-v1.json) · [coverage](../artifacts/aml-cft-reasoning-pilot/05-real-claims-run-002/coverage-matrix-v1.json) · [author run](../artifacts/aml-cft-reasoning-pilot/05-real-claims-run-002/run.json) · [model attestation](../artifacts/aml-cft-reasoning-pilot/05-real-claims-run-002/sessions/model-attestation-v1.json)
- **Stages 2–4 Execute.** Eight setup controls passed and the 24 synthetic KYC and 18 BO versions reproduced their accepted results. All 32 KYC versions joined their separate references, matched the formalization replay and passed certificate checks. The independent replay passed all 12 symbolic controls. [Execution batch](../artifacts/aml-cft-reasoning-pilot/02-engine-setup-run-003/execution-batch.json) · [setup run](../artifacts/aml-cft-reasoning-pilot/02-engine-setup-run-003/run.json) · [formalization run](../artifacts/aml-cft-reasoning-pilot/03-formalization-run-003/run.json) · [validation](../artifacts/aml-cft-reasoning-pilot/03-formalization-run-003/validation-v1.json) · [input audit](../artifacts/aml-cft-reasoning-pilot/03-formalization-run-003/input-isolation-v2.json) · [execution run](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-003/run.json) · [replay](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-003/review-001/verification/replay-comparison.json)
- **Independent review.** Initially 13/16 cases, 27/32 versions and 3/6 categories passed. After one repair all cases, versions and categories passed, both findings closed and 20 repair controls passed. One repair round was unused. [Status](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-003/evaluation-status.json) · [acceptance](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-003/recheck-001/acceptance.json) · [recheck scores](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-003/recheck-001/post-repair-scores.json) · [handoff](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-003/recheck-001/handoff.json) · [assistant and contributor integration](aml-cft-reasoning-assistant.md#source-backed-kyc)

At each handoff, update this section with the batch version, selection, evidence,
blockers, remaining repair allowance and exact next stage and role.

## Objective and boundaries

Prove real one-hop and multi-hop deductions on a small readable synthetic dataset,
then repeat with selected corpus claims and synthetic customer facts.

Workflow: **1 draft → 2 setup → 3 generate → 4 Execute → 4 Review and repair**.
Stage 1 permits downstream preparation; Stage 4 independently accepts references,
encoding fidelity and executable reasoning for the selected batch.

- Preserve the working Document Assistant and application database. Assistant
  integration requires the pilot to pass, a later decision and regression checks.
- Start fresh with the minimal implementation required here. Do not read or reuse
  historical ontology archives unless explicitly requested.
- Real sources: `artifacts/20261028-docling-aml-cft-01/`, especially
  `claim-classification/` and `document-structuring/`. Follow manifests and original
  spans. The corpus has [unresolved claim-review findings](downstream-uses.md);
  its [retention exception](../artifacts/20261028-docling-aml-cft-01/claim-review/acceptance-exception.json)
  does not waive this pilot's source-review or acceptance gates.
- Read `.agents/AGENTS.md` and the relevant workflow each session. This guide
  does not authorize paid calls, database writes or application integration.
  Agree execution scope/budget and follow applicable approval rules.

The 13-document corpus contains factual assertions (such as publication dates)
as well as rules, but often lacks customer-specific facts. Diagnose missing inputs
separately from wrong rules, faulty formalization and engine defects.
French/English pairs test language effects without assuming French causes failures.

**Terms:** a fact is a supplied assertion; evidence supports it; a rule connects
premises (required conditions) to a conclusion. Encoding a fact does not verify
its truth. In simplified notation, a fictional policy and customer fact are:

```text
Rule:  high_risk(?customer) → requires_enhanced_review(?customer)
Fact:  high_risk(Alice)
```

`?` marks a variable in rules or queries; supplied facts name particular entities.
The engine binds `?customer = Alice` and derives `requires_enhanced_review(Alice)`.
The translator encodes statements; the engine binds variables and applies rules.
A rule can be encoded without customer facts; applying it requires support for
all its premises. These examples abbreviate the structured JSON contract below.

## Run layout and records

Keep saved prompts and evidence with their runs:

```text
01-dataset-run-001/        # All eight families: inputs, questions, proposed references
02-engine-setup-run-001/   # Selected-batch setup, schema, adapter and proof checker
03-formalization-run-001/  # Generated encodings, queries and provenance
04-reasoning-run-001/      # Reference/fidelity reviews, execution, repairs and status
05-real-claims-run-001/    # Source claims, synthetic facts, new references and results
```

Create new run folders when their stage starts; initialize evaluation status at
Stage 4 as specified under [Status file and ownership](#status-file-and-ownership).

Keep inputs separate from proposed reference solutions. Preserve revisions with
filenames such as `review-round-01.json` and `cases-revision-02.jsonl`. Each run
writes a `run.json` listing its dataset ID/version, role, input/output paths and
hashes, actual models/settings (or “unverified”), prompts, code revision, usage
and reused setup/schema/formalizer versions. Execution manifests also record `batch_id`,
`selected_families`, exact `case_ids`, the source dataset manifest/path/hash and
the authoritative status-file path. Carry this selection through stages 2–4.
Append session and command evidence under the execution-record rules below;
never overwrite earlier sessions when another role resumes the same run.
New experiments use new run numbers; repairs retain the same batch and status
records even if their artifacts need a new folder.

## Stage handoffs

Run one requested stage/role per session and stop at its handoff. The first
[KYC batch](aml-cft-kyc-batch-handoff.md#launch-instructions) had an explicit
exception for the author stages. Before starting,
read [Progress](#progress-and-next-action), the selected run manifest and its status
record, if present. Confirm the requested scope, model and execution budget;
verify prerequisite hashes and setup evidence. Unexpected missing or stale inputs
require a recorded blocker and a stop. Versioned Stage 1/3 outputs proceed
provisionally; record generation/validation failures for Stage 4 to assess.
This document specifies work; it does not itself launch any stage.

| Stage / role | Required inputs | Required output / handoff gate |
| --- | --- | --- |
| 1 | Agreed eight-family generation scope and budget | All 96 semantic cases / 192 language versions, separate inputs/proposed references, stable family/case IDs and coverage; hand off to 2 |
| 2 | Versioned Stage 1 or Stage 5 dataset and agreed family/case selection | Execution-batch manifest, supported semantics, versioned engine/schema/adapter/checker and runnable commands; hand off to 3 |
| 3 | Selected batch's readable inputs/questions and Stage 2 setup | Encodings/queries and validation records for selected cases only, including failures; hand off to 4 Execute |
| 4 Execute | Stage 3 artifacts/failure records, pinned setup and proposed references | Engine results, proof-check records and provisional comparisons; always hand off to 4 Review and repair |
| 4 Review and repair | Execution evidence, readable cases/proposed references, encodings and status | Mandatory independent review; accept if all checks pass, otherwise evidenced repair followed by independent recheck, or finish with failures at the limit |
| 5 | Accepted synthetic suite for the agreed scope and selected source passages | Source-backed inputs, synthetic facts, questions and new proposed references; hand off to 2 or reuse compatible setup, then 3 and 4 |

Follow the [review policy](#review-policy-and-repair-budget) for role separation.
Stage 2 must document install, validation and engine/checker commands before later
agents execute them. The isolated
`scripts/aml_cft_reasoning.py` CLI and its
[setup runbook](../artifacts/aml-cft-reasoning-pilot/02-engine-setup-run-001/README.md)
provide those commands. Stage 3's `scripts/aml_cft_formalization.py` CLI generates
encodings and queries using local deterministic mappings. The
[document pipeline commands](aml-cft-pipeline.md) serve a separate workflow.

## Agent responsibilities and reproducible commands

Agents own drafting, source selection, scope decisions and independent semantic
judgments. Commands validate structure/coverage/hashes, generate selected encodings
and queries, execute reasoning, check certificates and persist supplied judgments.
Record agent sessions separately from automatic checks; generation may use an LLM.

Expose computational steps as repository commands with explicit input/output/configuration
paths, machine-readable results and documented exit codes. A command must not
manufacture agent approval; record agent-only steps as automation dependencies.

Each run retains these execution records, linked from `run.json`:

- **Agent sessions:** session ID (if available), stage/role, start/end times,
  actual model/effort, saved instructions/prompts, inputs, decisions/findings,
  outcome, changed files and linked command IDs; record when no command ran.
- **`commands.jsonl`:** one record per pipeline invocation, including a unique ID,
  exact executable/arguments, working directory, non-secret environment overrides,
  required credential variable names, configuration/input/output paths and hashes,
  code revision (plus snapshot/patch of uncommitted code), dependency versions,
  start/end times, exit code and stdout/stderr log paths. Link provider request,
  model/settings and usage records for LLM calls. Preserve failed attempts/retries.
- **`commands.sh`:** ordered replay commands using the recorded configuration and
  explicit paths. Document prerequisites, credential variable names, execution
  budget, overwrite/resume behavior and expected outputs. Validate successful
  commands against their recorded outputs before calling them reusable.

Keep credentials out of all records/scripts. Preserve frozen artifacts on replay;
use explicit fresh output paths for a new run. Reproduction means the same
procedure/configuration; LLM generation may vary, so retain original responses.
At handoff, identify commands as verified, failed or not implemented and link
remaining agent-only work. Later automation must preserve acceptance gates,
reviewer independence, repair limits and execution budgets.

## Stage 1 — Draft the readable dataset

Stage 1 writes the cases in readable language: what the policy says, which facts
are supplied, and which question to ask. **No engine is required.**

**Inputs:** the agreed eight-family scope, generation budget and author setting
(GPT-6.1 Sol High). The author drafts fictional, self-contained French/English
cases and separate proposed answers/proofs.

**Outputs:** a versioned dataset with stable IDs and hashes:

- [Readable inputs](../artifacts/aml-cft-reasoning-pilot/01-dataset-run-001/inputs-v1.jsonl):
  `scope`, `policy_clauses` and `scenario_facts` describe each case.
- [Questions](../artifacts/aml-cft-reasoning-pilot/01-dataset-run-001/questions-v1.jsonl):
  `question_id` and `text` identify what to answer.
- [Proposed references](../artifacts/aml-cft-reasoning-pilot/01-dataset-run-001/proposed-references-v1.jsonl):
  `expected_status`, `expected_conclusions` and source-linked `proof_steps`, stored
  separately from the inputs/questions.
- [Dataset manifest](../artifacts/aml-cft-reasoning-pilot/01-dataset-run-001/dataset-manifest-v1.json):
  dataset ID/version, family/case mappings, language versions and file/record hashes.

Every case has facts; direct lookups need no rules. Policies and facts share an
input record but remain distinct fields. Separate question files organize the
pilot and isolate query translation; the engine does not require that file split.

Accept the draft for preparation and stop for Stage 2; references remain provisional.

Generate **all eight families**, with **12 semantic cases per family: two
structurally distinct cases per category below**, each in French and English.
The dataset totals **96 semantic cases / 192 language versions**. Agree the full
generation budget before execution. Translations/paraphrases are not independent
cases. Generating the other families does not require a prior KYC pass.

| Category | Required behavior |
| --- | --- |
| Direct lookup | Return a supplied fact; zero deductions |
| One-hop | Apply one rule to supplied facts |
| Multi-hop | Apply two or three rules through intermediate conclusions |
| Missing prerequisite | Return unknown and identify the missing premise |
| Exception | Respect explicit exception/clearance conditions |
| Conflict | Detect the declared contradiction and withhold the affected conclusion |

| Family | Coverage |
| --- | --- |
| KYC — Onboarding/identity | Identity evidence, representative authority, incomplete files |
| BO — Beneficial ownership/control | Ownership chains, controlling persons, verification |
| PEP — Politically exposed persons | Relationships, approvals, wealth/funds checks |
| RISK — Risk-based/enhanced diligence | Customer/geographic risk and conditions for enhanced measures |
| MON — Monitoring/suspicious reporting | Alerts, investigation, reporting conditions, confidentiality; include terrorist financing |
| TFS — Targeted sanctions for terrorist financing | Confirmed matches, asset restrictions, explicit authorisations |
| PAY — Payment/virtual-asset transfer information | Originator/beneficiary data, missing fields, escalation |
| CORR — Correspondent banking | Respondent checks, approvals, relationship restrictions |

These are representative control families, not a statistical ranking of criminal
typologies. Topic basis: [FATF Recommendations, June 2026, recommendations 1, 6, 10, 12–13, 15–16, 19–21, 24–25](https://www.fatf-gafi.org/content/dam/fatf-gafi/recommendations/fatf-recommendations-2012.pdf).
Stage-1 policies remain fictional; an alert must not automatically mean suspicion
or a reportable event.

Each case must contain a family ID, stable case/clause/fact IDs, policy clauses,
scenario facts, scope, question, expected status/conclusions and proof steps linked
to clause/fact IDs. Store policy clauses, facts, scope and questions separately
from proposed answers/proofs. Keep
stable semantic IDs across equivalent French/English versions. Label customer facts
as synthetic assumptions. Include negative controls such as removing a premise or
changing customer identity; do not mix facts between customers/cases.
Keep cases self-contained so selected families need no premises from unselected
families. Save a dataset ID/version and a manifest mapping families to case IDs.

**Logic scope:** symbolic facts and conjunctive rules; no arithmetic, aggregates or
implicit legal knowledge. Supply assessed risk categories as facts. Define exception
and conflict behavior explicitly, using positive clearance where required. Missing
facts imply neither falsity nor exception clearance. Later choose an engine/adapter
supporting these semantics; do not install an engine or generate encodings in Stage 1.

### Family selection for stages 2–4

Before Stage 2, agree one, several or all families and record an execution batch
against the versioned dataset. Select every case and both language versions from
each chosen family. For example, KYC selects 12 semantic cases / 24 language
versions; KYC + BO + PEP selects 36 / 72; all eight selects 96 / 192.
KYC can be the first execution batch while the full dataset is already available.

Keep selected families, case IDs and score denominators fixed through execution,
review and repair. Repairs create versioned revisions linked to the original
selection. Unselected families are `not-run`; they require no formalization or
review for that batch and do not enter its pass/fail denominator. A new selection
needs an explicitly agreed batch and its own run/status paths; preserve prior
results and any reused cases' defect/repair history. Extra attempts on unresolved
cases require a user decision, rather than a reset through a different selection.

Report acceptance by selected family and dataset/revision hashes. Passing KYC
does not establish correctness for the other seven families. Other family batches
can run independently within their agreed budgets. Retain accepted compatible
KYC cases as regression coverage when shared pipeline/setup changes affect them;
record that regression scope separately from the selected-batch score denominator.
Stage 5 requires synthetic acceptance for the families in its agreed scope.

## Review policy and repair budget

Freeze a fixed correctness rubric before the first Stage 4 review. Only evidenced
correctness defects require repair: unsupported/wrong conclusions or proofs,
answer-changing ambiguity, lost conditions/exceptions, unequal French/English
meaning, missing required coverage or unusable required schema/provenance.
Every finding must name the case, violated criterion, evidence/counterexample and
minimum correction. A stronger reviewer must apply this same rubric.
Reviewers do not repair their findings; authors do not approve their own repairs.

Wording, naming, optional realism, extra examples and broader coverage are
non-blocking preferences. **PASS with zero findings is valid.** Accept immediately
when all required checks pass; do not add critic passes or new requirements.

1. After Execute, independently review all agreed cases using the
   [four review steps](#review-and-repair), even if all provisional comparisons match.
2. An author repairs only evidenced correctness defects in a separate session.
   The reviewer rechecks changed cases and affected dependencies, retaining
   unaffected judgments. Reopen closed findings only with new evidence.
3. Allow **at most two shared repair rounds per batch**, across dataset/reference,
   encoding/query and engine/checker defects. This is a pragmatic execution
   budget, not an empirically established optimum or a pass-score threshold.
   Initial drafting/generation is not a repair. Each round includes repairs,
   regeneration, affected engine/checker reruns and one independent recheck;
   allow one initial review and at most two rechecks. New findings, sessions,
   models or folders never reset the budget.
4. Stop as `accepted` when all required checks pass. After the second round's
   recheck, finish as `completed-with-failures` if defects remain. Keep every
   failure in the report and score denominator; report unresolved cases/evidence
   and the decision needed for further work. Do not drop cases, weaken criteria,
   restart automatically or run parallel reviewer chains.

Acceptance covers the selected batch; real-claim transfer and assistant integration
retain their passing criteria. Record defect origins and rework effort; reconsider
earlier checks only if larger runs reveal substantial avoidable rework.

### Status file and ownership

Each agreed selection of families and cases, including both languages, forms a
**batch**. A Stage 1 dataset can supply several batches. Each batch has one
authoritative status file. For the first batch, use
`04-reasoning-run-001/evaluation-status.json`, relative to the pilot artifact root.
Initialize it when Stage 4 Execute begins, with counters at zero only for a new
batch. Execute records command results and hands off to mandatory review; it
cannot accept the batch. The reviewer records independent reference/fidelity and
execution verdicts; the repairing author records repair rounds. Reports link
here rather than owning duplicate counters.

Record `batch_id`, dataset ID/version/manifest hash, `selected_families`, `case_ids`,
`status`, the fixed rubric, input/setup/output hashes, `repair_rounds`,
`evaluation_passes`, per-case reference/fidelity/execution
verdicts, open/closed findings, evidence, scores and next stage/role/action.
`evaluation_passes` counts independent initial review/rechecks, not Execute
invocations. Accepted results bind to the exact readable-reference, encoding/query
and setup hashes. Record corrected-reference versions and their justification
separately from initial expectations; never overwrite initial results.

Start a repair round with the first fix after a completed review or recheck.
Regeneration, fidelity checks and reasoning reruns belong to that round.
Increment each counter once when its action starts, mark it active and resume
interrupted work under the same counter.

| State | Meaning / next role |
| --- | --- |
| `pending-execution` | Generated artifacts/failure records are ready for Stage 4 Execute |
| `pending-review` | Execute finished; mandatory independent review is next, regardless of provisional comparisons |
| `needs-repair` | Evidenced correctness defects remain and repair budget is available |
| `pending-recheck` | A recorded repair round is ready for independent recheck and affected engine/checker reruns |
| `accepted` | Stage 4 passed all required reference, fidelity and execution checks |
| `completed-with-failures` | Evaluation finished with unresolved failures at the repair limit; further work needs a user decision |
| `blocked-needs-decision` | Required inputs, scope, execution budget or unsupported semantics need a user decision before work can continue |

Workflow states differ from case outcomes such as unknown or conflict. Later
batches, including Stage 5, record their own manifest/status paths in Progress
while preserving earlier results. Resuming or renaming a batch never resets counters.

## Stage 2 — Set up the engine

Stage 2 defines the structured data model and prepares the reasoning software
that will consume it. Cases inform requirements and setup tests; concrete customer
facts are not inherently required to define the contract or encode a rule.
Stage 3 performs the readable-text-to-structure mappings and declares each case's
vocabulary.

**Inputs:** the versioned Stage 1 or Stage 5 dataset and agreed family/case
selection. Before setup, record the families, exact case IDs and both language
versions in the [execution manifest](../artifacts/aml-cft-reasoning-pilot/02-engine-setup-run-001/execution-batch.json).
Use only those cases' declared semantics; correctness remains provisional.

Define and document these components:

- **Contract:** the structured data model and its rules. The
  [representation/query schema](../schemas/aml-cft-reasoning-schema-v1.json)
  defines fields such as `entities`, `facts`, `rules`, `incompatibilities` and
  separate `queries`. Document types, required fields and supported meaning,
  including missing facts, scope, exceptions and conflict behavior.
- **Engine:** the reasoning software that consumes structured facts, rules and
  queries to derive conclusions. Select/configure the engine and record its identity.
- **Adapter:** the bridge from the contract to the engine's native format when
  they differ, including structural validation and result conversion as needed.
- **Proof checker:** verifies that recorded deduction steps follow from the
  supplied premises and rules. Document what its checks establish and their limits.

Implement the minimal adapter and proof checks. Support the declared readable
semantics; never change them or weaken expectations to fit engine limitations.

**Outputs:** versioned contract, engine, adapter, checker and configuration;
the execution manifest; setup validation evidence; and runnable install,
validation, engine and checker commands. The
[setup run](../artifacts/aml-cft-reasoning-pilot/02-engine-setup-run-001/run.json)
pins component versions/hashes; the
[configuration](../artifacts/aml-cft-reasoning-pilot/02-engine-setup-run-001/engine-config-v1.json)
records settings and resource limits. Document unsupported behavior explicitly.

Save commands and setup evidence before handing off to Stage 3. Reuse compatible
setup with exact versions/hashes. Contract changes require affected encodings/queries
to be regenerated and checked in Stage 4; engine/adapter/checker changes require
affected Stage 4 results to be re-evaluated. Setup completion alone does not
establish reasoning correctness; Stage 4 tests it using the generated Stage 3 output.

## Stage 3 — Generate the encoding and queries

Stage 3 implements and runs the mappings from readable cases to the structured
representation defined in Stage 2:

- Readable policies/facts → structured entities, facts, rules, guards/scope and
  incompatibilities, preserving their source links.
- Readable questions → separate structured queries describing what to ask the engine.

**Inputs:** only the execution manifest's selected readable cases/questions and
the pinned Stage 2 contract/setup. Verify dataset/revision hashes and selection
before generation. Build/configure and run the formalizer against that contract.
Give the facts/rules generator only readable rules/facts, scope, source IDs, target
schema and general encoding instructions.
**Exclude expected answers, reference proofs and case-specific evaluation hints.**
The formalizer produces representations, not answers.

**Outputs:** versioned
[encodings](../artifacts/aml-cft-reasoning-pilot/03-formalization-run-001/encodings-v1.jsonl),
[queries](../artifacts/aml-cft-reasoning-pilot/03-formalization-run-001/queries-v1.jsonl)
and validation/failure records. In the contract, a fact has an `atom`; a rule has
premises in `body` and a conclusion in `head`; a query has a `target` and optional
answer variables in `select`. `source_ids` and `question_id` connect these to the
readable inputs and questions.

Each encoding declares one shared predicate vocabulary with argument order/types,
and its particular entities. Facts and rules both contribute predicates; a
facts-only lookup contributes vocabulary without a rule. Rules alone can supply
their predicates, but need not cover predicates used only for lookups. Translation
must preserve predicate meanings and entity identities. Validation rejects
undeclared predicates or entities; the engine does not infer synonym mappings.

For `KYC-DL-01`, “Customer A supplied a passport” becomes
`identity_document(A, passport)` with no rules. The query
`identity_document(A, ?document)` returns `?document = passport` by assertion,
without deduction. Asking about Bob must preserve Bob: an undeclared Bob fails
validation; a declared Bob with no supporting fact yields unknown, not A's answer.

Validate syntax, references and variable binding; save outputs and failures
without silent retries. Prepare queries separately from versioned questions,
without inserting expected answers. Questions go to the query pipeline, not the
facts/rules generator; the engine consumes the resulting structured queries.
The separate [assistant input path](aml-cft-reasoning-assistant.md#symbolic-reasoning-and-proof)
constructs fresh user cases from accepted policies/vocabulary and supplied text.
This does not alter these frozen pilot encodings or their acceptance.
Version/hash all outputs and stop for Stage 4 Execute. Generation and structural
validation do not establish reference correctness, encoding fidelity or
successful reasoning.

For encoding or query defects, follow [repair and independent recheck](#repair-and-independent-recheck).
Preserve the original outputs and findings alongside the corrected pipeline version
and regenerated artifacts.

```text
Versioned readable rules/facts → 3 formalizer → generated facts/rules
Generated facts/rules + queries → 4 Execute → results + proofs + provisional comparisons
Readable cases + encoding + execution evidence → 4 Review and repair → verdict + diagnosis
Evidenced defects → author repair → independent recheck + affected engine/checker reruns
```

## Stage 4 — Execute, then Review and repair

Stage 4 runs the reasoning engine on Stage 3's structured data, then independently
checks that the readable cases, encodings, queries and results agree.

**Inputs:** selected encodings/queries and generation/validation failure records,
pinned Stage 2 setup, original readable cases/questions and proposed references.

**Outputs:** engine conclusions and proof certificates, proof-check outcomes,
provisional comparisons, independent review verdicts and the authoritative batch
status. Execute produces evidence; the independent reviewer decides acceptance.

### Execute

Verify selected case IDs, versioned inputs, encoding/query hashes and pinned setup.
Run Stage 2's engine/checker commands on the generated encodings and queries.
The engine applies rules to supported premises, chaining deductions where needed,
and answers the structured queries using the configured handling of missing
premises, exceptions and conflicts. The checker verifies the saved proof steps
against the encodings and queries. Keep every agreed case in the denominator,
including generation/validation errors. Record cases that cannot execute as
failures and continue running the others to collect diagnostic evidence.

**Reading a proof certificate:** it is the engine's structured record of the facts
and rule applications supporting its conclusions. In
[`results-v1.json`](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-001/results-v1.json),
each case/language record contains `results` with per-query statuses, answer
bindings and `supported_atoms` (the conclusions answering the query).
`candidate_certificate` records support before conflicting statements are blocked;
`usable_certificate` records the facts and deductions remaining after that blocking.

The fields used in certificate steps are:

| Field | Meaning |
| --- | --- |
| `fact_id` | The encoded supplied fact supporting an assertion step. |
| `rule_id` | The encoded policy rule applied in a deduction step. |
| `binding` | The entities assigned to the rule's variables, such as `?c = A`. |
| `premises` | Statements already supported and used by that rule application. |
| `atom` | The statement established by this step, expressed as a predicate and arguments. |

Assertion steps have `fact_id` and `atom`; deduction steps have `rule_id`,
`binding`, `premises` and `atom`. In
[`encodings-v1.jsonl`](../artifacts/aml-cft-reasoning-pilot/03-formalization-run-001/encodings-v1.jsonl),
`facts[].source_ids` and `rules[].source_ids` link these encoded IDs to
`scenario_facts[].id` and `policy_clauses[].id` in
[`inputs-v1.jsonl`](../artifacts/aml-cft-reasoning-pilot/01-dataset-run-001/inputs-v1.jsonl),
where `text` contains the readable wording. For example, `KYC-OH-01` traces:
**A's identity document is verified → apply the identity-check policy → A's
identity check is complete.** The checker verifies the encoded derivation;
independent review checks that the encoding faithfully represents the readable text.

Save commands/logs, results, proofs, proof-check outcomes and comparisons with
the Stage 1 proposed reference answers. State exactly what each comparison checks:
matching statuses (`entailed`, `unknown`, `conflict`) alone does not establish that
the conclusions or proofs have the same meaning. All Execute comparisons remain
provisional. Set `pending-review` and stop for a fresh independent review session,
even if every comparison matches. Execute cannot accept the batch.

### Review and repair

The authoritative status selects the action under the
[review policy](#review-policy-and-repair-budget). Review every selected case in
both languages across Stage 1 references, Stage 3 encodings/queries and Stage 4
results/proofs:

1. **Derive answers independently from the readable cases.** Read the original
   policies, facts and questions in English/French. Derive a supported answer with
   a source-linked justification, or explain why the answer is unknown or conflicted.
   For real claims, use exact original passages and the stipulated synthetic facts.
   Save these independent judgments separately **before opening the proposed
   reference answers/proofs or saved engine results**.
2. **Review Stage 1's reference answers and proofs.** Compare them with the
   independent judgments from step 1. Check every proof step, required condition,
   exception, source link, missing premise and withheld conclusion, including
   English/French equivalence. Together, steps 1–2 assess **reference correctness**.
3. **Review Stage 3's encodings and queries against the readable cases.** Check
   that the symbolic facts, rules and queries preserve entity identity, scope,
   every condition/exception, source links and question meaning in both languages,
   without invented premises. This assesses **encoding/query fidelity**. Valid
   syntax does not establish faithful meaning. Different identifiers are acceptable
   when they preserve meaning; no second reference encoding is required.
4. **Review Stage 4's engine answers and proofs.** Compare each status, answer
   binding and supported atom with the meaning of the independently checked
   reference answer. Inspect the actual proof steps: do the applied rules and
   supported premises justify the conclusions, including missing-premise,
   exception and conflict behavior? Verify the saved engine/checker evidence
   against current artifact/setup hashes. This assesses **executable reasoning**.
   Record all errors and cases that cannot execute as failures. After repairs,
   rerun affected commands and save new results and proofs.

Automated checks compare statuses (`entailed`, `unknown`, `conflict`) and verify
symbolic certificates; the reviewer checks their meaning. Model agreement or a
matching status is insufficient. Record per-case reference, fidelity and execution
verdicts with checked artifact hashes. Wrong references or unfaithful encodings
fail even when engine output matches the draft answer.

Acceptance requires:

- Every query status and conclusion agrees with the independently checked
  reference; no unsupported conclusions.
- Cases requiring one-hop/multi-hop deductions contain actual rule applications
  and valid proof certificates. Supplied facts support direct lookups, but supplied
  facts alone, empty certificates and LLM-written narratives do not prove deductions.
- Missing-premise, exception and conflict controls behave as specified. Unknown
  identifies the documented missing premise; it is not a negative conclusion.
- Equivalent French/English policies and facts yield equivalent outcomes for the
  checked structured queries. Review question translations here; automatic
  natural-language question interpretation requires a separate later test.
- All reference, fidelity and execution checks pass for every agreed case.

Check that each applied rule and its premises support the conclusion; allow
alternative valid derivations and wording. Distinguish supplied assertions from
deductions. The engine and checker run code; no LLM is needed to derive results.
Optional prose must add no new conclusions. Only the independent reviewer records
acceptance and the hashes of the accepted references and results.

#### Scores and failure report

Report the first independent review separately from later rechecks after repair,
with the artifact/reference versions used. Execute comparisons are provisional
and do not supply independent pass scores. For each review/recheck, report:

- Overall pass percentage: 100 × fully passing semantic cases / all selected
  semantic cases in the batch manifest. Keep that denominator fixed.
  A semantic case passes only when reference, fidelity and execution checks pass in
  both languages. For a synthetic batch selecting N families, the denominator is
  12 × N semantic cases, with 24 × N language versions reported separately. Real-claim
  batches use their agreed case counts. English/French versions of the same case
  count as one semantic case.
- Counts/percentages by selected family and category, and separate
  reference/fidelity/execution and language results. Keep errors, ambiguous cases
  and unexecuted cases in the agreed totals; report missing coverage. A specified
  unknown/conflict outcome can pass.
- For each failure, identify the responsible layer: dataset/reference,
  formalization, engine/adapter, evidence, checker or language interpretation.
  Record proof checks, latency, provider usage, where the defect originated and
  the effort spent repairing it.

Batch acceptance requires 100% of selected cases and required coverage to pass.
At the repair limit, report scores and remaining failures; `completed-with-failures`
does not authorize real-claim transfer or assistant integration.

#### Repair and independent recheck

Within the [shared repair budget](#review-policy-and-repair-budget), encoding/query
defects require changes to producing code, prompts, configuration or mappings,
then regeneration from versioned readable inputs. Engine/adapter/checker defects
require versioned setup changes.
Dataset/reference defects require a source-linked counterexample and versioned
correction preserving original expectations; never fit answers to engine output.
Changes to scope or fact/rule interpretation need a user decision.

Return repairs to a fresh Stage 4 Review and repair session for independent
recheck. Review the changed references, encodings or setup and any cases affected
by shared changes, then rerun affected reasoning checks. Retain unaffected valid
judgments; report initial and repaired outcomes separately. The initial independent
review follows Execute; do not add a separate review before execution.

## Stage 5 — Repeat with real claims and synthetic facts

Agree the intended family scope and verify corresponding synthetic acceptance;
acceptance of all eight families is required only when that is the agreed scope.
Select a small, varied claim subset and verify it against exact original passages.
Preserve actor, jurisdiction, edition, applicability, conditions, exceptions, governing
context and cross-references. Claims may contain facts as well as rules; prior
extraction does not establish correctness.

The same author LLM may draft synthetic customer facts, questions and proposed
answers/source-linked proofs. Positive cases satisfy rule conditions; negative
controls omit or contradict premises. Keep synthetic assumptions separate from
regulatory evidence: rules must not manufacture customer facts or their applicability.

Draft **new proposed references** for the new batch; do not inherit synthetic
answers. Version/hash its inputs/questions/references and hand off to Stage 2,
or reuse compatible setup, then Stage 3 generation and Stage 4 Execute followed
by Review and repair. The Stage 4 reviewer checks original sources and stipulated
facts, reference answers, encoding fidelity and executable proofs. Unresolved source
ambiguity fails the affected case. Neither the formalizer nor engine supplies or
edits reference answers; model agreement alone does not establish correctness.

Cover direct assertions, source-supported one-hop/multi-hop deductions and
missing-premise/exception/conflict controls. If a genuine multi-hop chain is absent,
report the gap and select suitable sources; never invent regulatory rules.
Every agreed case must pass with traceable sources and actual deductions. Report
scope limits: subset success does not establish whole-corpus coverage or current
legal applicability.

## Session prompts and reuse

Choose one stage/role and model/effort per session using the current status.
The stage sections own requirements; the prompts below select the action. Paste
the common prefix and one row, supplying the batch and artifact/status paths from
[Progress](#progress-and-next-action).

> Read `.agents/AGENTS.md` and `doc/aml-cft-reasoning-pilot.md`.
> Inspect the current checkout, progress and artifacts. Work only on the requested
> run/role under its stage instructions, acceptance criteria and bounded repair policy.
> Resolve the dataset/run and, for stages 2–4, the selected batch and authoritative
> status paths. Create records when their stage starts; never reset existing counters.
> Preserve prior results and Document Assistant behavior; follow applicable approval
> rules. Record the agent session, commands and outcomes under the execution-record
> rules. Update the run/status records and Progress, then stop at the handoff.
> End with the exact next-session prompt, including batch and artifact/status paths,
> or the user decision needed when work cannot proceed.

| Stage / role | Model / effort | Prompt |
| --- | --- | --- |
| 1 | GPT-6.1 Sol High | Draft the complete bilingual dataset under Stage 1, saving separate inputs/references and run evidence. Stop for family selection and Stage 2. |
| 2 | GPT-6.1 Sol High | Agree the selected batch; configure or verify compatible engine/schema/adapter/checker support under Stage 2. Save validation and commands; stop for 3. |
| 3 | GPT-6.1 Sol High | Generate and validate the selected batch's encodings/queries under Stage 3, excluding reference solutions from generator requests. Stop for 4 Execute. |
| 4 Execute | GPT-6.1 Sol Medium | Verify selection/hashes and execute the pinned engine/checker under Stage 4. Save results and provisional comparisons; set pending-review and stop. |
| 4 Review and repair | GPT-6.1 Sol Medium for review/recheck; Sol High for author repair | Perform only the action selected by status under Stage 4 and the shared repair policy. Save independent verdicts or author changes, evidence and the next action. |
| 5 | GPT-6.1 Sol High | Verify synthetic acceptance and draft the agreed source-backed batch under Stage 5. Record coverage limits; stop for setup or compatible reuse. |

After the Stage 5 draft, use the Stage 2–4 rows for that dataset, reusing compatible
setup.

Model choices are starting settings recorded in this plan on 2026-11-13, not pilot
benchmark results. Check availability when selecting a session; see
[OpenAI model guidance](https://developers.openai.com/api/docs/guides/model-selection)
and [agent model documentation](https://learn.chatgpt.com/docs/models).
Use fresh review/recheck sessions independent of authorship/repair. Do not add a
stronger-model critic pass after acceptance or launch model comparisons
automatically. Avoid Extra high/Max/Ultra by default.

Select model/effort in agent settings; prompt text does not configure them. Script/API
model calls need separate configuration and execution budgets. Availability/allowance
depend on account and task; API prices do not determine subscription session counts.
