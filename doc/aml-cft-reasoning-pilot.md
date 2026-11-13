# AML/CFT reasoning pilot

Updated: 2026-11-13. **Execution not started.** This guide defines the workflow;
no dataset, engine setup, formalization or reasoning results exist under this plan.

Keep this guide in `doc/` for version control; run outputs belong in
`artifacts/aml-cft-reasoning-pilot/`. Repository paths in this guide start at the
repository root; run paths in the folder layout and status table start at that
artifact directory.

## Progress and next action

| Stage | Status | Required before starting |
| --- | --- | --- |
| 1 | Not started | Agree generation scope/model/budget for all eight families; create `01-dataset-run-001/run.json`, draft 96 bilingual cases and version inputs/questions/proposed references; stop for 2 |
| 2 | Not started | Versioned dataset; agree selected families and exact case IDs for an execution batch; configure/reuse support for their declared semantics |
| 3 | Not started | Selected batch's readable inputs/questions and pinned Stage 2 setup; generate only selected cases and stop for 4 |
| 4 Execute | Not started | Generated encodings/queries or recorded generation failures, proposed references and pinned engine/checker; always hand off for review |
| 4 Review and repair | Not started | Mandatory initial review of readable cases, encodings and execution evidence; optional repair and independent recheck within the shared budget |
| 5 | Not started | Accepted synthetic suite for the agreed scope and selected original source passages |

At every handoff, update this table with the dataset version, selected families,
batch/run, artifact/status links, acceptance evidence or blockers, remaining repair
allowance and one exact next stage/role. Completion alone does not mean acceptance.

## Objective and boundaries

Prove real one-hop and multi-hop deductions on a small readable synthetic dataset,
then repeat with selected corpus claims and synthetic customer facts.

Workflow: **1 draft → 2 setup → 3 generate → 4 Execute → 4 Review and repair**.
Stage 1 output is accepted for downstream preparation; its answers/proofs remain
provisional until the independent Stage 4 review. Stage 4 owns the single review
and repair loop for readable references, encoding fidelity and executable reasoning.
Generate all eight families once in Stage 1; stages 2–4 can process one, several
or all families from that versioned dataset. Acceptance covers only selected cases.

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

Regulatory documents contain facts and rules but often lack customer-specific
facts. Diagnose missing inputs separately from wrong rules, faulty formalization
and engine defects. French/English pairs test language effects without assuming
French causes failures.

**Terms:** a fact states something; evidence supports it; a rule connects premises
(required statements/conditions) to a conclusion. Fictional example: “A is high
risk” + “High-risk customers require enhanced review” ⇒ “A requires enhanced review.”
A fact may be `(A, riskCategory, HighRisk)`; a rule is an implication between patterns,
not an isolated triple.

## Run layout and records

Keep saved prompts and evidence with their runs:

```text
01-dataset-run-001/        # All eight families: inputs, questions, proposed references
02-engine-setup-run-001/   # Selected-batch setup, schema, adapter and proof checker
03-formalization-run-001/  # Generated encodings, queries and provenance
04-reasoning-run-001/      # Reference/fidelity reviews, execution, repairs and status
05-real-claims-run-001/    # Source claims, synthetic facts, new references and results
```

These folders and records are planned outputs. Create them when their run starts,
initializing the authoritative evaluation-status file when Stage 4 starts.

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

Run one requested stage/role per session and stop at its handoff. Before starting,
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

Stage 4 has two grouped roles: Execute, then Review and repair. Within Review and
repair, use separate sessions for the initial independent review, author repair
and independent recheck. The reviewer does not repair its findings; the author
does not approve its repairs. Stage 2 must document install, validation and
engine/checker commands before later agents execute them. No reasoning-pilot CLI
exists yet; the [existing pipeline commands](aml-cft-pipeline.md) serve the document
pipeline, not this reasoning pilot. Do not advance automatically.

## Agent responsibilities and reproducible commands

Agent instructions assign responsibility; commands make computational work
reproducible. Record both the agent session and each pipeline command it runs.
Independent judgments remain agent work, recorded separately from automatic checks.

| Stage | Agent responsibility | Command responsibility |
| --- | --- | --- |
| 1 / 5 | Draft cases, select sources and record assumptions; Stage 1 covers all eight families | Validate saved structure, family/category coverage, IDs and hashes |
| 2 | Record the agreed family/case selection and configure/reuse its execution support | Validate the selection against the dataset manifest; install/configure dependencies and validate setup |
| 3 | Implement/configure and launch the formalizer/query pipeline for selected cases | Generate encodings/queries for manifest case IDs and validate outputs; generation may call an LLM |
| 4 Execute | Launch execution, inspect command results and record failures | Execute reasoning, check proof certificates and save comparisons with provisional references |
| 4 Review and repair | Independently check references, encoding fidelity and execution; an author fixes evidenced defects, then an independent reviewer rechecks | Persist supplied judgments, regenerate affected artifacts and rerun affected engine/checker commands for recheck |

As each computational step is implemented, expose a reusable, non-interactive
command with explicit input/output/configuration paths, machine-readable results
and documented exit codes. Put reusable code in the repository; avoid unsaved
snippets or chat-only execution logic. Review judgments remain separate supplied
artifacts; a command must not manufacture agent approval. Record agent-only steps
as such: they remain explicit dependencies for future automation.

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

**No engine is required for Stage 1.** The author (GPT-6.1 Sol High) drafts fictional,
self-contained French/English policy and fact versions, paired questions and
proposed reference answers/proofs. Version and hash the draft; accept it for
downstream preparation and stop for Stage 2. References remain provisional until
Stage 4 independently checks them. Model agreement does not establish correctness.

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

Wording, naming, optional realism, extra examples and broader coverage are
non-blocking preferences. **PASS with zero findings is valid.** Accept immediately
when all required checks pass; do not add critic passes or new requirements.

1. After Execute, perform one complete independent review of all agreed cases,
   even if every engine result matches the proposed references. Derive
   reference outcomes independently from the readable policies/facts before
   comparing proposed answers or engine results. Review encoding fidelity and
   verify saved execution/proof evidence in that review session.
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

A completed experiment can have failures. Acceptance applies to the selected
batch; real-claim transfer and assistant integration retain their passing criteria.
Record defect origins and rework effort; reconsider earlier checks only if larger
runs show that late discovery causes substantial avoidable work.

### Status file and ownership

A **batch** is the agreed family/case selection from a versioned dataset, including
both language versions. The full Stage 1 dataset can supply multiple batches.
It has one authoritative status file. For the first batch, use
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

A repair round starts when a new fix begins after a completed evaluation; its
regeneration and recheck use that same round. For example, fixing a missing rule,
regenerating, reviewing fidelity and rerunning reasoning consume one round.
Another fix after that recheck consumes round two. Increment actions once when
they start and mark them active; resume interruptions without incrementing again.

| State | Meaning / next role |
| --- | --- |
| `pending-execution` | Generated artifacts/failure records are ready for Stage 4 Execute |
| `pending-review` | Execute finished; mandatory independent review is next, regardless of provisional comparisons |
| `needs-repair` | Evidenced correctness defects remain and repair budget is available |
| `pending-recheck` | A recorded repair round is ready for independent recheck and affected engine/checker reruns |
| `accepted` | Stage 4 passed all required reference, fidelity and execution checks |
| `completed-with-failures` | Evaluation finished with unresolved failures at the repair limit; further work needs a user decision |
| `blocked-needs-decision` | Required inputs, scope, execution budget or unsupported semantics need a user decision before work can continue |

Workflow states are separate from case outcomes such as unknown or conflict.
Unresolved test failures at the repair limit produce a completed report, rather
than an endless review loop. Later batches record their own manifest/status paths
in Progress. Resuming or renaming an existing batch never creates fresh counters.
Stage 5 creates a new batch while preserving synthetic results.

## Stage 2 — Set up the engine

Read the versioned Stage 1 or Stage 5 dataset. Before setup, record the agreed
families, exact case IDs and both language versions in the execution manifest.
Use only those cases' declared semantics; correctness remains provisional.
Select/configure an engine, implement its minimal adapter and proof checks, and
document the representation/query schema, versions, configuration and unsupported
behavior. Support the declared readable semantics; never change them or weaken
expectations merely to fit engine limitations.

Record runnable local commands and setup evidence before handoff. Reuse compatible
setup with exact versions/hashes. Schema changes require affected encodings/queries
to be regenerated and checked in Stage 4; engine/adapter/checker changes require
affected Stage 4 results to be re-evaluated. Setup completion alone does not
establish reasoning correctness; Stage 4 tests it using the generated Stage 3 output.

## Stage 3 — Generate the encoding and queries

Build/run the formalizer using Stage 2's schema and only the readable cases named
in the execution manifest. Verify dataset/revision hashes and selection before
generation. Give it only readable rules/facts, scope, source IDs, target schema
and general encoding instructions.
**Exclude expected answers, reference proofs and case-specific evaluation hints.**
The formalizer produces representations, not answers.

Generate entities, facts, rules, guards/scope and source links. Validate syntax,
references and variable binding; save outputs and failures without silent retries.
Prepare structured queries separately from versioned questions, without inserting
expected answers. Questions/queries go to the engine, not the facts/rules generator.
Natural-language query interpretation and assistant UI/routing remain later work.
Version/hash all outputs and stop for Stage 4 Execute. Generation and structural
validation do not establish reference correctness, encoding fidelity or successful
reasoning.

**Repair the producing pipeline, not saved outputs.** For evidenced encoding/query
defects, fix the responsible code, prompts, configuration or mappings, regenerate
affected artifacts from versioned readable inputs, then hand off for an independent
recheck under Stage 4 Review and repair. Recheck previously passing cases affected
by shared changes. Preserve original outputs, findings, changed versions and
regenerated outputs; report initial success separately from success after repair.
All repairs use Stage 4's shared budget.

```text
Versioned readable rules/facts → 3 formalizer → generated facts/rules
Generated facts/rules + queries → 4 Execute → results + proofs + provisional comparisons
Readable cases + encoding + execution evidence → 4 Review and repair → verdict + diagnosis
Evidenced defects → author repair → independent recheck + affected engine/checker reruns
```

## Stage 4 — Execute, then Review and repair

### Execute

Verify selected case IDs, versioned inputs, encoding/query hashes and pinned setup.
Run Stage 2's engine/checker commands on the generated encodings and queries.
Keep every agreed case, including generation/validation errors; record
non-executable cases as failures and continue executable cases for diagnostic evidence.

Save commands/logs, results, proofs, proof-check outcomes and comparisons with
proposed reference answers. These comparisons are provisional. Set `pending-review`
and stop for a fresh Review and repair session, even if all comparisons match.
Execute cannot declare acceptance or skip independent review.

### Review and repair

This role includes **mandatory initial review → optional author repair →
independent recheck**, with separate sessions for each action. Use the status
record to select the next action; reviewers do not repair and repairing authors
do not approve their work. Initial review and permitted rechecks assess:

1. **Reference correctness:** independently derive outcomes from original readable
   policies/facts and questions before inspecting proposed answers/proofs or saved
   engine results. Check every proof step, condition, exception, source link and
   French/English equivalence. Each case needs an unambiguous result and traceable
   proof or justified unknown/conflict. For real claims, check exact original
   passages and stipulated synthetic facts. Save independent judgments separately
   from the author's proposed references; model agreement is not proof.
2. **Encoding/query fidelity:** compare actual generated artifacts with the readable
   cases. Check entity identity, scope, every condition/exception, source links,
   question meaning, language equivalence and invented premises. Syntax alone is
   insufficient. Judge meaning rather than preferred identifiers; no second
   reference encoding is required. Record verdicts and the hashes checked.
3. **Executable reasoning:** verify saved engine/checker evidence against current
   artifact/setup hashes and independently checked references. Rerun affected
   commands after repairs, saving new results and proofs. Record all errors and
   non-executable cases as failures. A reference/fidelity defect prevents a case
   from passing, even if engine output matches the draft.

Acceptance requires:

- Every expected status and conclusion matches; no unsupported conclusions.
- Real one-hop/multi-hop derivations and accepted proof certificates. Stored facts,
  empty certificates and LLM proof narratives do not count as deductions.
- Missing-premise, exception and conflict controls behave as specified. Unknown
  identifies the documented missing premise; it is not a negative conclusion.
- Equivalent French/English policies and facts yield equivalent outcomes for the
  checked structured queries. Review question translations here; automatic
  natural-language question interpretation requires a separate later test.
- All reference, fidelity and execution checks pass for every agreed case.

Check actual premise–rule–conclusion support; allow alternative valid derivations
and wording. Distinguish assertions from inferences. The engine/checker execute code;
no LLM is needed to derive results. Optional prose must add no new conclusions.
Only the independent reviewer freezes accepted references and results with hashes.

#### Scores and failure report

Report initial and post-repair outcomes separately, with the artifact/reference
versions used. Label Execute comparisons as provisional; initial pass scores
come from the first independent review. For each review/recheck, report:

- Overall pass percentage: fully passing semantic cases / all selected semantic cases
  fixed in the batch manifest.
  A semantic case passes only when reference, fidelity and execution checks pass in
  both languages. For a synthetic batch selecting N families, the denominator is
  12 × N semantic cases, with 24 × N language versions reported separately. Real-claim
  batches use their agreed case counts. Translations never inflate the semantic count.
- Counts/percentages by selected family and category, and separate
  reference/fidelity/execution and language results. Keep errors, ambiguous cases
  and unexecuted cases in the agreed totals; report missing coverage. A specified
  unknown/conflict outcome can pass.
- Failures and diagnoses: dataset/reference, formalization, engine/adapter,
  evidence, checker or language interpretation. Record proof checks, latency,
  provider usage, defect origins and rework effort.

Pass scores measure progress; batch acceptance requires 100% of selected cases
and their required coverage to pass. At the repair limit, finish with scores and
remaining failures. `completed-with-failures` establishes no passing result for
that scope and does not authorize its real-claim transfer or assistant integration.

#### Repair and independent recheck

The reviewer records evidenced defects and stops. A separate repairing author
uses the remaining shared budget to fix the responsible layer. Encoding/query
defects require changes to the producing code, prompts, configuration or mappings,
followed by regeneration. Engine/adapter/checker defects require versioned setup
changes. Dataset/reference defects require a source-linked counterexample and a
versioned correction, preserving original expectations; never change an answer
merely to fit engine output. Changes to agreed scope or semantics need a user decision.

Return repairs to a fresh Stage 4 Review and repair session for independent
recheck. Recheck changed references, encodings or setup and their affected
dependencies, then rerun affected reasoning checks. Retain unchanged valid
judgments; report initial and repaired outcomes separately. There is no additional
pre-execution reviewer session.

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

Select one role and one action per session, with one model/effort setting.
Review and repair spans separate review, author repair and independent recheck
sessions; choose the action/model from the current status. The stage sections own
the requirements; these prompts select the role and handoff. Open a new agent
session and paste this prefix followed by the selected row's prompt. Start at
Stage 1; subsequent sessions use the batch and artifact/status paths in Progress.

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
| 1 | GPT-6.1 Sol High | Draft all eight families under Stage 1: 12 semantic cases each, 96 total / 192 language versions. Separate inputs from proposed references. Create/resume `01-dataset-run-001/run.json` and save the dataset version, family/case IDs and hashes. Accept the draft for preparation, keeping references provisional; stop for family selection and Stage 2. |
| 2 | GPT-6.1 Sol High | Agree one, several or all families from the versioned dataset. Record the batch, dataset manifest/hash and exact family/case/language selection. Configure the engine/schema/adapter/checker for those cases, or verify compatible reuse. Save setup evidence and runnable commands; stop for 3. |
| 3 | GPT-6.1 Sol High | Generate encodings and separate queries only for the execution manifest's selected case IDs under Stage 3, excluding reference solutions from generator requests. Verify selection and hashes, save validation failures and stop for 4 Execute. |
| 4 Execute | GPT-6.1 Sol Medium | Verify selected case IDs and hashes, then run the documented engine/checker commands for that batch only. Save command evidence, results, proofs, failures and provisional comparisons. Always set pending-review and stop for an independent Review and repair session, even if every comparison matches; do not accept the batch. |
| 4 Review and repair | GPT-6.1 Sol Medium for review/recheck; Sol High for author repair | Read status and perform only the next action: pending-review → independent reference/fidelity/execution review; needs-repair → evidenced author repair within the shared budget; pending-recheck → independent recheck and affected engine/checker reruns. Use separate sessions; reviewers do not repair and authors do not self-approve. Save evidence and the next action. The reviewer records verdicts/scores and accepts if all checks pass, or finishes with failures at the limit. |
| 5 | GPT-6.1 Sol High | Verify synthetic acceptance for the agreed scope, then draft source-backed cases and new proposed references as a new batch under Stage 5. Record coverage limits; stop for 2 or compatible setup reuse, then 3 and 4. |

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
