# AML/CFT reasoning pilot

Updated: 2026-11-13. **Execution not started.** This guide defines the workflow;
no dataset, engine setup, formalization or reasoning results exist under this plan.
The next step is to agree the Stage 1 scope, model and execution budget, then draft
the initial 12-case KYC dataset. Stages 1–2 do not require an engine.

Keep this guide in `doc/` for version control; run outputs belong in
`artifacts/aml-cft-reasoning-pilot/`. Repository paths in this guide start at the
repository root; run paths in the folder layout and status table start at that
artifact directory.

## Progress and next action

| Stage | Status | Required before starting |
| --- | --- | --- |
| 1. Dataset | Not started | Agree 12-case KYC scope/model/budget; create `01-dataset-run-001/run.json`, draft inputs/questions/proposed references, then stop for 2a |
| 2. Readable reference review | Not started | Stage 1 drafts; freeze the rubric before reviewing |
| 3. Engine/schema setup | Not started | Accepted Stage 2 semantics |
| 4a. Formalization | Not started | Frozen Stage 2 inputs/questions and versioned Stage 3 setup |
| 4b. Encoding review | Not started | Generated encodings/queries and frozen Stage 2 references |
| 5. Engine evaluation | Not started | Stage 4b-reviewed artifacts and pinned engine/checker |
| 6. Real-claim transfer | Not started | Accepted synthetic suite for the agreed scope and selected original source passages |

At every handoff, update this table with the batch/run, artifact and authoritative
status-file links, acceptance evidence or blockers, remaining repair allowance and
one exact next stage/role. Completion alone does not mean acceptance.

## Objective and boundaries

Prove real one-hop and multi-hop deductions on a small readable synthetic dataset,
then repeat with selected corpus claims and synthetic customer facts.

- Preserve the working Document Assistant and application database. Assistant
  integration requires the pilot to pass, a later decision and regression checks.
- Planning reference: branch `dsly_add_ontologies`, revision `f411185`
  (`Broad cleanup before adding ontologies`); earlier baseline:
  `50d5a3d5430bd32b5efb2c2016ddd4a717af36f6`. Inspect the current checkout and
  preserve existing work; these references are not checkout/reset instructions.
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

Keep saved prompts and evidence with their runs under
`artifacts/aml-cft-reasoning-pilot/`:

```text
01-dataset-run-001/        # Readable inputs, questions and proposed references
02-review-run-001/         # Reviews, repairs and frozen readable dataset
03-engine-setup-run-001/   # Engine, schema, adapter and proof-checker setup
04-formalization-run-001/  # Generated encodings, queries, reviews and provenance
05-reasoning-run-001/      # Queries, results, proofs and evaluation
06-real-claims-run-001/    # Source claims, synthetic facts, new references and results
```

These folders and records are planned outputs. Create them when their run starts,
including the shared status-file parent directory when Stage 4a begins.

Keep inputs separate from reference solutions. Preserve revisions with filenames
such as `review-round-01.json` and `cases-revision-02.jsonl`. Each run writes a
`run.json` listing its batch ID, role, input/output paths and hashes, actual
models/settings (or “unverified”), prompts, code revision, usage and reused
setup/schema/formalizer versions. Record the exact status-file paths defined below.
Append session and command evidence under the execution-record rules below;
never overwrite earlier sessions when another role resumes the same run.
Never copy credentials. New experiments use new run numbers; repairs retain the
same batch and status records even if their artifacts need a new folder.

## Stage handoffs

Run one requested stage/role per session and stop at its handoff. Before starting,
read [Progress](#progress-and-next-action), the selected run manifest and its status
record, if present. Confirm the requested scope, model and execution budget;
verify prerequisite hashes and acceptance evidence. If an input is missing, stale or unaccepted, record the blocker
and stop. This document specifies work; it does not itself launch any stage.

| Stage / role | Required inputs | Required output / handoff gate |
| --- | --- | --- |
| 1 | Agreed KYC scope and generation budget | Separate readable inputs, bilingual questions, proposed references and category coverage; hand off to 2a |
| 2a / 2b / 2c | Stage 1 or Stage 6 draft, or latest repair; freeze rubric and initialize status at 2a, then reuse both | Findings, case verdicts and status; only the independent reviewer freezes accepted inputs/questions/references with hashes |
| 3 | Accepted Stage 2 semantics | Versioned engine, schema, adapter/checker, configuration and runnable local commands; hand off to 4a |
| 4a generate / repair | Frozen Stage 2 inputs/questions and Stage 3 setup | Saved generator requests, generated encodings, separate queries and validation report, including failures; hand off to 4b |
| 4b review / recheck | Stage 4a artifacts and frozen Stage 2 inputs/questions/references | Independent fidelity review and accepted encoding/query hashes, or evidenced blockers; accepted artifacts proceed to 5 |
| 5 evaluate | Reviewed Stage 4 artifacts, pinned setup and Stage 2 references | Engine results, proof checks, failure diagnoses and status; all required cases must pass for acceptance |
| 6 draft | Accepted synthetic suite for the agreed scope and selected source passages | Source-backed inputs, synthetic facts, questions and new proposed references; hand off to 2a, not directly to formalization |

Preparation/repair, independent review and evaluation are separate sessions.
Stage 4b acceptance establishes encoding fidelity; Stage 5 establishes executable
reasoning. Stage 3 must document exact install, validation and engine/checker
commands before later agents execute them. No reasoning-pilot CLI exists yet;
the [existing pipeline commands](aml-cft-pipeline.md) serve the document pipeline,
not this reasoning pilot. No second reference encoding of the dataset is required.
Do not advance automatically.

## Agent responsibilities and reproducible commands

Agent instructions assign responsibility; commands make computational work
reproducible. Record both the agent session and each pipeline command it runs.
Commands validate dataset structure/coverage and hashes, install/check setup,
generate encodings/queries, execute reasoning, check proofs and compare results.
Independent judgments remain agent work, recorded separately from automatic checks.

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

**No engine is required for stages 1–2.** An author LLM drafts fictional,
self-contained French/English policy and fact versions, paired questions and
proposed reference answers/proofs. These references are untrusted until reviewed.

The initial scope is **12 KYC semantic cases: two structurally distinct cases per
category below**, each in French and English (24 language versions).
Complete stages 2–5 and pass every KYC check before expanding to the seven other
families. Expanded coverage uses the same 12-case, six-category design per family:
96 semantic cases and 192 language versions in total. Retain KYC as regression
coverage. Translations/paraphrases do not count as independent cases.

Agree each expansion's scope and generation budget before execution. Stage 6
requires the synthetic suite for its agreed scope to pass; a KYC-only pass does
not establish coverage of the other seven families.

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

Each case must contain stable IDs, policy clauses, scenario facts, scope, question,
expected status/conclusions and proof steps linked to clause/fact IDs. Store policy
clauses, facts, scope and questions separately from proposed answers/proofs. Keep
stable semantic IDs across equivalent French/English versions. Label customer facts
as synthetic assumptions. Include negative controls such as removing a premise or
changing customer identity; do not mix facts between customers/cases.

**Logic scope:** symbolic facts and conjunctive rules; no arithmetic, aggregates or
implicit legal knowledge. Supply assessed risk categories as facts. Define exception
and conflict behavior explicitly, using positive clearance where required. Missing
facts imply neither falsity nor exception clearance. Later choose an engine/adapter
supporting these semantics; do not install an engine or generate encodings in Stage 1.

## Stage 2 — Review and freeze readable references

Use separate sessions: **2a review → 2b repair (only if blocked) → 2c recheck**.
Select one model per session. The reviewer writes findings/status but does not repair
the dataset; the author repairs but does not approve their own changes. Each session
stops at the handoff, keeping the same batch and gate records. If review passes,
freeze and skip repair/recheck. Otherwise, repeat only 2b–2c within the two-round
allowance for the readable-reference gate.

A separate reviewer derives results from the original policies/facts **before**
comparing draft answers. Check every proof step, condition, exception, source link
and French/English equivalence. Record corrections and freeze accepted readable
inputs, questions, answers and proofs with hashes. No engine encoding/query is needed.
Every case must have an unambiguous result and traceable proof or justified
unknown/conflict outcome.

Independence means references do not come from the implementation's output.
A fresh session/different model can reduce shared errors; agreement is not proof.

## Review gates and repair limits

Freeze the rubric before review. Only evidenced correctness defects block:
unsupported/wrong conclusions or proofs, ambiguity changing the answer, lost
conditions/exceptions, unequal French/English meaning, missing required coverage,
or unusable required schema/provenance. Every blocker must name the case, violated
criterion, evidence/counterexample and minimum correction.

Wording, naming, optional realism, extra examples and broader coverage are
non-blocking preferences. **PASS with zero findings is valid.**

1. Perform one complete review. If all required checks pass, accept/freeze immediately;
   do not add another critic pass, even if optional suggestions remain.
2. The author repairs only evidenced blockers; the independent reviewer rechecks
   changed cases and affected dependencies. Retain unchanged passing judgments;
   reopen closed findings only with new evidence.
3. Allow **at most two repair rounds**, with an initial check and at most two
   rechecks per review/evaluation step, per gate/batch, not per defect. Stages 4a–5
   share the same repair rounds. Sessions, models, effort, new findings, run names
   and folders cannot reset limits. No parallel reviewer chains or automatic restarts.
4. At the limit, stop as `accepted` or `blocked-needs-decision`. Report unresolved
   cases, evidence and proposed decision. Do not drop failures, weaken expectations
   or claim complete coverage. Another attempt/scope change needs an explicit user
   decision. Bounded work does not guarantee a passing result.

### Status files and ownership

A **batch** is the agreed set of semantic cases and their French/English versions.
It has two gates, each with its own repair allowance:

| Gate | Authoritative status file for the first batch | Who updates it |
| --- | --- | --- |
| Readable-reference correctness — Stage 2 | `02-review-run-001/review-status.json` | Reviewer records passes/verdicts; author records repair rounds |
| Encoding fidelity and executable reasoning — stages 4a–5 | `05-reasoning-run-001/evaluation-status.json` | Stage 4b reviewer records encoding verdicts; Stage 5 evaluator records execution verdicts; repairing agent records repair rounds |

**Stages 4a, 4b and 5 share one status file and at most two repair rounds.**
Initial generation is not a repair. A round can fix failures from encoding review
or execution; regeneration, encoding recheck and engine re-evaluation belong to
that same round. Stage 4b and Stage 5 each allow one initial pass and at most two
rechecks; doing both is not two repair rounds. Accepted, unchanged judgments are
reused. Do not launch a repair or another pass after acceptance without new evidence.
Each new fix after a completed review/evaluation consumes the next repair round;
handoffs and regeneration within an already recorded round do not increment it again.
For example, repairing a Stage 4b defect, regenerating, passing 4b recheck and running
Stage 5 uses one repair round. A new fix after that evaluation uses round two.

Both files record `batch_id`, `gate`, `status`, `repair_rounds`, the frozen rubric,
artifact hashes, per-case verdicts, open/closed blockers, evidence links and the
next stage/role. Stage 2 counts `review_passes`; the shared file separately counts
`encoding_review_passes` and `evaluation_passes`, and records which encoding/query
hashes Stage 4b accepted. Reports link here rather than owning duplicate counters.

Use the following gate states; these describe workflow, not case outcomes:

| State | Meaning / next role |
| --- | --- |
| `pending-review` | Await initial readable-reference or encoding review |
| `needs-repair` | Evidenced blockers require an author/pipeline repair |
| `pending-recheck` | Repair is ready for the applicable review/evaluation recheck |
| `pending-evaluation` | Stage 4b accepted the current encoding/query hashes; await Stage 5 |
| `accepted` | Stage 2 passed its gate, or Stage 5 passed the combined Stage 4–5 gate |
| `blocked-needs-decision` | Repair allowance exhausted with blockers remaining, or a defect in frozen references needs a decision |

Only Stage 5 can accept the combined gate. Initialize counters at zero only for a
new gate/batch. Increment once when an action starts and record it as active;
resume interruptions without incrementing again. After the second repair, finish
its permitted rechecks; accept if all pass, otherwise stop as `blocked-needs-decision`.

Later batches select their own run paths and record them in `run.json` and Progress.
Resuming or repairing an existing batch always follows its recorded status paths;
renaming/copying a folder never creates fresh counters. Stage 6 uses new batch IDs
and Stage 2/4–5 status records for its new cases, while preserving synthetic results.

## Stage 3 — Set up the engine

Select/configure an engine, implement its minimal adapter and proof checks, and
document the representation/query schema, versions, configuration and unsupported
behavior. Support the agreed semantics; never weaken frozen expected results to
fit engine limitations.

Record runnable local commands and setup evidence before handoff. Reuse compatible
setup with exact versions/hashes. Schema changes require affected encodings/queries
to be regenerated and reviewed; engine/adapter/checker changes require affected
Stage 5 results to be re-evaluated. Setup completion alone does not establish
reasoning correctness; Stage 5 tests it using the reviewed Stage 4 output.

## Stage 4 — Generate and review the encoding

**4a — Generate:** build/run the formalizer using Stage 3's schema. Give it only
readable rules/facts, scope, source IDs, target schema and general encoding instructions.
**Exclude expected answers, reference proofs and case-specific evaluation hints.**
The formalizer produces representations, not answers.

Generate entities, facts, rules, guards/scope and source links. Validate syntax,
references and variable binding; save outputs and failures without silent retries.
Prepare structured queries separately from frozen questions, without inserting
expected answers. Questions/queries go to the engine, not the facts/rules generator.
Natural-language query interpretation and assistant UI/routing remain later work.

**4b — Independent review:** compare the actual generated encoding and queries
with Stage 2's frozen cases. Check entity identity, scope, every condition/exception,
source links, question meaning, French/English equivalence and absence of invented
premises. Syntax validity alone is insufficient. Judge meaning rather than exact
IRIs or one preferred encoding; do not create a second encoding or reopen accepted
readable cases merely for another critique. Record findings and accepted hashes in
the shared status file. Pass directly to Stage 5 if no blockers remain.

**Repair the producing pipeline, not saved outputs.** For evidenced encoding/query
defects, fix the responsible code, prompts, configuration or mappings, regenerate
affected artifacts from frozen inputs, then hand off for independent recheck.
Recheck previously passing cases affected by shared changes. Preserve original
outputs, findings, changed versions and regenerated outputs; report initial success
separately from success after repair. Do not feed case-specific answers or reference
proofs into generator requests. Use the shared two-round allowance.

```text
Frozen readable rules/facts → 4a formalizer → generated facts/rules
Generated encoding + separate queries → 4b fidelity review
Reviewed facts/rules + queries → 5 engine/checker → results + proofs
Results/proofs + Stage 2 references → evaluator → verdict + diagnosis
```

## Stage 5 — Execute and evaluate

Run the Stage 4b-reviewed encodings and queries through the actual engine/checker.
Verify their hashes and compare results/proofs with Stage 2's frozen expectations.
Retain every agreed case; execution errors are failures, never omitted cases.

Acceptance requires:

- Every expected status and conclusion matches; no unsupported conclusions.
- Real one-hop/multi-hop derivations and accepted proof certificates. Stored facts,
  empty certificates and LLM proof narratives do not count as deductions.
- Missing-premise, exception and conflict controls behave as specified. Unknown
  identifies the documented missing premise; it is not a negative conclusion.
- Equivalent French/English policies and facts yield equivalent outcomes for the
  reviewed structured queries. Paired question translations are reviewed in Stage 2;
  automatic natural-language question interpretation requires a separate later test.

Check actual premise–rule–conclusion support; allow alternative valid derivations
and wording. Distinguish assertions from inferences. The engine/checker execute code;
no LLM is needed to derive results. Optional prose must add no new conclusions.

Report initial and post-repair outcomes, case counts, failures, proof checks,
latency and provider usage separately. Classify failures as dataset/reference,
formalization, engine/adapter, evidence, checker or language interpretation errors.
Never change reference answers to make execution pass. A newly evidenced defect in frozen references blocks downstream
acceptance; report it for a decision rather than silently reopening/resetting a
completed review. Diagnose before repair: fix the formalizer/query pipeline for
encoding defects, or the engine/adapter/checker for execution defects. Changed
encodings/queries return to Stage 4b before execution; unchanged reviewed artifacts
can go directly to Stage 5 re-evaluation. Both use the remaining shared allowance.

## Stage 6 — Repeat with real claims and synthetic facts

Select a small, varied claim subset and verify it against exact original passages.
Preserve actor, jurisdiction, edition, applicability, conditions, exceptions, governing
context and cross-references. Claims may contain facts as well as rules; prior
extraction does not establish correctness.

The same author LLM may draft synthetic customer facts, questions and proposed
answers/source-linked proofs. Positive cases satisfy rule conditions; negative
controls omit or contradict premises. Keep synthetic assumptions separate from
regulatory evidence: rules must not manufacture customer facts or their applicability.

Create **new references** through stages 2–5: a separate reviewer checks original
sources and stipulated facts, then freezes accepted references before formalization.
Unresolved source ambiguity blocks the case. Reuse compatible Stage 3 setup;
generate and review the new cases' encodings through 4a–4b. Neither the generator
nor engine supplies/edits answers; do not inherit synthetic-dataset answers or
accept them just because models agree.

Cover direct assertions, source-supported one-hop/multi-hop deductions and
missing-premise/exception/conflict controls. If a genuine multi-hop chain is absent,
report the gap and select suitable sources; never invent regulatory rules.
Every agreed case must pass with traceable sources and actual deductions. Report
scope limits: subset success does not establish whole-corpus coverage or current
legal applicability.

## Session prompts and reuse

Use one row per session, with one model/effort setting. The stage sections own
the requirements; these prompts select the role and handoff. Use this prefix
followed by the selected prompt:

> Read `.agents/AGENTS.md` and `doc/aml-cft-reasoning-pilot.md`.
> Inspect the current checkout, progress and artifacts. Work only on the requested
> run/role under its stage instructions, acceptance criteria and bounded repair policy.
> Resolve the selected batch, run manifest and authoritative status paths before
> work; do not assume run-001 or initialize over an existing batch's counters.
> Preserve prior results and Document Assistant behavior; follow applicable approval
> rules. Record the agent session, commands and outcomes under the execution-record
> rules. Update the run/status records and Progress, then stop at the handoff.

| Stage / role | Model / effort | Prompt |
| --- | --- | --- |
| 1 — Draft | GPT-6.1 Sol High | Draft the 12 bilingual KYC cases under Stage 1; separate inputs from proposed references. Stop for 2a. |
| 2a — Review | GPT-6 Astra High | Perform the initial independent Stage 2 review. Freeze accepted references or hand off blockers to 2b; do not repair. |
| 2b — Repair | GPT-6.1 Sol High | Record/resume an allowed repair round for Stage 2 blockers. Preserve revisions and changed-case/dependency records; stop for 2c without self-approval. |
| 2c — Recheck | GPT-6 Astra High | Recheck repaired cases and affected dependencies under Stage 2; retain unaffected verdicts. Freeze if all pass; otherwise hand off blockers or stop at the limit. |
| 3 — Setup | GPT-6.1 Sol High | Configure the engine/schema/adapter/checker under Stage 3. Record versions, evidence and runnable commands; stop for 4a. |
| 4a — Generate | GPT-6.1 Sol High | Generate encodings and separate queries under Stage 4a, excluding reference solutions from generator requests. Save validation failures; stop for 4b. |
| 4b — Review/recheck | GPT-6 Astra High | Independently review fidelity under Stage 4b; record findings and accepted hashes. Hand off to 5, repair or a decision at the limit; do not repair. |
| 4a — Repair/regenerate | GPT-6.1 Sol High | Record/resume an allowed shared repair round. Fix the producing pipeline for encoding/query blockers, regenerate affected cases and stop for 4b. |
| 5 — Evaluate | GPT-6.1 Sol Medium | Execute reviewed artifacts and check all Stage 5 criteria. Record verdicts and failure layers; accept only if all pass, otherwise hand off blockers. Do not repair. |
| 5 — Repair | GPT-6.1 Sol High | Record/resume an allowed shared repair round. Fix the responsible layer; return changed encodings/queries to 4b, or unchanged reviewed artifacts to 5. |
| 6 — Draft real-claim cases | GPT-6.1 Sol High | Draft source-backed cases and new proposed references under Stage 6. Record coverage limits; stop for 2a. |

After the Stage 6 draft, use the Stage 2–5 rows for that dataset, reusing compatible
setup. Follow the new batch's recorded status paths; all handoffs within that batch
retain its existing counters.

Model choices are starting settings recorded in this plan on 2026-11-13, not pilot
benchmark results. Check availability when selecting a session; see
[OpenAI model guidance](https://developers.openai.com/api/docs/guides/model-selection)
and [agent model documentation](https://learn.chatgpt.com/docs/models).
For tighter allowance, replace Astra with **Sol High in a fresh review session**.
Do not add an Astra pass after accepted Sol review or launch model comparisons
automatically. Avoid Extra high/Max/Ultra by default.

Select model/effort in agent settings; prompt text does not configure them. Script/API
model calls need separate configuration and execution budgets. Availability/allowance
depend on account and task; API prices do not determine subscription session counts.
