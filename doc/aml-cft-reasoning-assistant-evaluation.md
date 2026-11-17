# AML/CFT reasoning assistant evaluation protocol

## Status and scope

Stage 1 generated **v1.0.0: 288 prompts / 144 bilingual pairs** with full matrix
coverage. The generated version is frozen for runner preparation; preparation
checks passed. References remain **provisional** until Stage 4. The user-facts
assistant and natural-language runner are implemented with offline checks. Real-model assistant execution and independent review have not started;
the shared repair counter is zero.

Evidence: [questions](../artifacts/aml-cft-reasoning-assistant-evaluation/01-dataset-run-001/questions-v1.jsonl),
[proposed references](../artifacts/aml-cft-reasoning-assistant-evaluation/01-dataset-run-001/proposed-references-v1.jsonl),
[manifest and source mappings](../artifacts/aml-cft-reasoning-assistant-evaluation/01-dataset-run-001/dataset-manifest-v1.json),
[preparation checks](../artifacts/aml-cft-reasoning-assistant-evaluation/01-dataset-run-001/preparation-checks.json),
[run record and handoff](../artifacts/aml-cft-reasoning-assistant-evaluation/01-dataset-run-001/run.json).

Next action: agree effective provider/model configuration, API budget and per-prompt
reservations, plus a verified published document snapshot, before live smoke execution.
Offline evidence: [run report](../artifacts/aml-cft-reasoning-assistant-evaluation/02-user-facts-offline-run-001/report.json)
and [reproduction commands](../artifacts/aml-cft-reasoning-assistant-evaluation/02-user-facts-offline-run-001/commands.sh).
A later [starter-citation correction](../artifacts/aml-cft-reasoning-assistant-evaluation/02-starter-citation-fix-offline-run-001/report.json)
adds an exact UI-starter regression; it does not add or change evaluation prompts.
The frozen authoring snapshot has no verified published snapshot ID. No document
answers or real-model quality scores are established by offline checks.

Evaluate each unchanged question's `text` through `Assistant.ask(text, history=[])`.
Runtime retrieves reusable accepted policies, interprets supplied facts/entities and
the question, and builds a fresh case. Neither source-case mappings, reference
answers nor saved structured queries enter submissions. Keep evaluation records
outside the reasoning registry.

The sequence is **Generate → Prepare runner → Execute → Review and repair**.
References remain provisional until the final independent review, following the
[pilot's approach](aml-cft-reasoning-pilot.md#review-and-repair). Each action uses
a separate Codex session. Read `.agents/AGENTS.md` and obtain the applicable
file-change or experiment approval before acting. This protocol does not authorize
paid calls, application changes or changes to pilot acceptance records.

## Coverage

| Question type | Families | Categories | Languages | Prompts |
| --- | ---: | ---: | ---: | ---: |
| Reworded existing questions | 8 | 6 | 2 | 96 |
| New answerable questions | 8 | 6 | 2 | 96 |
| Boundary questions | 8 | 6 | 2 | 96 |
| Total | | | | 288 |

Use KYC, BO, PEP, RISK, MON, TFS, PAY and CORR; categories DL, OH, MH, MP, EX
and CF; languages French and English. Each type/family/category cell contains one
semantic question in both languages: **144 bilingual pairs / 288 prompts**.
Translations are not independent semantic questions.

- **Reworded:** preserve an existing question's meaning and scenario in fresh,
  natural wording.
- **New answerable:** ask a genuinely different question using an existing
  scenario's facts, rules and vocabulary. Preserve the assigned category's behavior;
  `unknown` and `conflict` can be correct outcomes. A changed word order or variable
  name alone does not create a new target.
- **Boundary:** test ambiguous applicability, changed facts/unsupported logic, or
  actual regulatory requirements. Expect clarification or document routing. The
  category identifies the originating scenario, not a deduction to execute. Cover
  all three boundary behaviors and record their distribution in the manifest.

Reasoning prompts must establish enough facts and fictional policy context to
identify the intended assessment without internal case IDs. For deductions, supply
premises rather than assert the requested conclusion; direct lookup may supply the
queried fact. Document equivalent acceptable scenarios; if plausible scenarios yield
different answers and the prompt does not distinguish them, clarification is required.
A missing premise in an established scenario is reasoning → `unknown`, not a
boundary-routing failure.

Check novelty against the original benchmark and development examples in
`tests/fixtures/reasoning-routing-v1.json`. Exclude exact/normalized duplicates and
review semantic novelty. If a meaningful new target cannot be found for a cell,
record the coverage gap and seek a scope decision; do not invent rules or relabel
a paraphrase.

## Artifacts and handoffs

Use fresh run directories under `artifacts/aml-cft-reasoning-assistant-evaluation/`.
Stage 1 artifacts exist at the linked run above; later-stage outputs remain planned:

| Directory | Required artifacts |
| --- | --- |
| `01-dataset-run-001/` | `questions-v1.jsonl`, `proposed-references-v1.jsonl`, `dataset-manifest-v1.json`, `preparation-checks.json`, `run.json` |
| `02-execution-run-001/` | `run.json`, checkpointed `responses.jsonl`, `provisional-comparisons.json` |
| `03-review-run-001/` | `independent-judgments.jsonl`, `per-question-verdicts.jsonl`, `findings.json`, `scores.json`, `evaluation-status.json` |

Question records contain `question_id`, `pair_id`, `question_type`, `family_id`,
`category`, `language` and `text`. Keep source case/question mappings in the manifest
and expectations in the reference file. Submit only `text` to the assistant.

Each reference identifies its question, expected route, readable response
requirements and source-linked justification. Reasoning references also specify
acceptable scenarios, query meaning/target, answer bindings, status, and proof or
missing/conflicting-premise expectations. Boundary references explain why deduction
is inappropriate. Document references identify supporting original passages or an
expected evidence limitation under the pinned snapshot. Reference prose is not an
exact-string answer template.

The manifest records version, coverage, pairs, source mappings and file hashes.
Each `run.json` records input hashes, code revision/working-tree changes,
configuration/model versions, commands, outputs, usage/cost and one next action.
The review status records current hashes, findings, next action and a shared repair
counter starting at zero. Preserve original artifacts; use versioned corrections
and separate repair/recheck directories. Update this document's status and evidence
links at each handoff.

## Stage 1 — Generate

Draft all questions and separate proposed references from accepted readable
scenarios, without assistant execution results. Preserve scope, entity relationships,
conditions, exceptions and bilingual equivalence. Use original corpus passages to
propose substantive document answers; fictional rules cannot establish real duties.

Run preparation checks only: schema/IDs, source links, counts, bilingual pairs,
coverage, duplicate detection and hashes. Freeze the generated version for execution.
Preparation checks do not accept references. Do not add a separate semantic review
before execution. Stop for runner preparation.

## Stage 2 — Prepare the backend runner

Implement a separate batch runner with focused offline tests. The existing
`scripts/evaluate_reasoning_assistant.py` replays saved symbolic queries; it does
not measure live retrieval, routing, interpretation or explanation quality.

The new runner must:

- Call `Assistant.ask(question_text, history=[])` for each prompt with real
  configured embeddings/models and document services. Never inject expectations,
  force candidates or carry conversation context between questions.
- Checkpoint completed responses and failures. Resume only with matching question,
  configuration, code, dataset and snapshot identities. Preserve attempts and count
  deliberate retries; do not silently retry for a better score.
- Save answers, retrieved candidates, selected routes/scenarios, interpreted queries,
  engine results, proof checks, citations, diagnostics, latency, usage and estimated
  costs using available traces. Do not log credentials.
- Capture accepted dataset hashes and model/embedding settings. Enforce and verify
  a fixed published document snapshot for previews and full answers, or stop if it
  changes. Any necessary service adaptation needs its own approved diff.
- Separate execution from comparison: references are absent from question submission;
  later automatic comparisons remain provisional.
- Support a smoke subset, bounded execution, an agreed API budget and resumption.
  Include embedding/document calls in accounting and state cost-estimation limits.

Test input isolation, empty history, failures retained in totals, resume identity
checks and trace/score accounting offline. Mocked tests validate the runner, not
model quality. Run relevant assistant regressions for any service changes. Record
implemented commands and checks at handoff. The CLI below is implemented; its
real-provider mode remains unexecuted.

### Implemented commands and offline scope

```sh
PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_reasoning_assistant.py tests/test_reasoning_assistant_search.py tests/test_reasoning_assistant_inputs.py tests/test_reasoning_assistant_ui.py -q
PYTHONPATH=src:tests .venv/bin/python tests/record_reasoning_input_smoke.py --output /tmp/assistant-input-smoke-NEW
.venv/bin/python scripts/evaluate_reasoning_assistant_inputs.py --output /tmp/assistant-input-inventory-NEW
```

The recorded smoke uses seven unchanged frozen prompts, controlled embeddings and
manually supplied model outputs. It saves runtime encodings, queries, provenance and
proof checks. It does not establish real-model fact formalization.

The inventory verifies frozen file hashes and records all 288 prompts as
unexecuted, without model, embedding or database calls. Focused tests additionally
check case isolation, invalid interpretations, failed proofs and snapshot changes.

After a separate configuration/budget agreement, real natural-language execution is:

```sh
.venv/bin/python scripts/evaluate_reasoning_assistant_inputs.py --execute --agreement /path/to/agreed-run.json --max-prompts 4 --output /tmp/assistant-input-live-NEW
# Resume with identical code, inputs, configuration, agreement and snapshot:
.venv/bin/python scripts/evaluate_reasoning_assistant_inputs.py --execute --agreement /path/to/agreed-run.json --max-prompts 288 --output /tmp/assistant-input-live-NEW --resume
```

The agreement JSON must contain `config_sha256` (SHA-256 of
`json.dumps(load_config(), sort_keys=True).encode()`), `api_budget_usd`,
`reserve_usd_per_prompt` and `cost_reservation_acknowledged: true`. Include
`snapshot_id` and `retrieval_config_sha256` (hash of the configured retrieval JSON) when document
services are enabled. Use `--config` to pin an alternate effective
configuration; defaults preserve existing runtime model choices. Agree the reservation
amount explicitly, including initial policy embeddings and possible document calls.
Reservations stop new prompts when exhausted; they are estimates, not a provider
billing cap. LLM call costs and embedding usage are recorded where available;
document failures can leave costs unknown. A complete billed total is not claimed.

The runner checkpoints every attempt, including errors; resume does not retry failed
prompts. Code/config/input/dependency/snapshot identity changes reject resume. Both
previews and full document answers use the pinned published active snapshot, checked
before/after requests. Snapshot changes stop execution. Outputs retain interpretation,
encoding, query, engine result, proof checks, source provenance, diagnostics and available
usage/latency. `summary.json` retains failures and all unexecuted IDs in the denominator;
completion is not correctness or acceptance. Exit codes: 0 inventory/completed requests,
1 failures/unexecuted prompts, 2 invalid input/configuration.

### Provisional expectations requiring separate review

Keep the 192 reasoning, 64 clarification and 32 document expectations frozen.
The following former-workflow assumptions cannot be compared literally to the revised
workflow and are **not reference corrections or accepted judgments**:

- Saved scenario/case identities and pilot customer fact/proof IDs are no longer
  runtime case provenance. Compare applicable policy semantics and user-text spans;
  a generated `user-…` case and `user-fact-…` source IDs are expected.
- Automatically clarifying every changed customer fact is obsolete. Changed facts
  are supported when representable under the selected policy; unsupported logic,
  revoked/temporal ambiguity or requests to reuse a saved decision can still clarify.
- Canonical pilot entity symbols are not mandatory identity mappings. For example,
  `RAE1-reworded-KYC-DL-fr` supplies *passeport*, while its proposed binding uses
  `passport`. A faithful literal entity can differ; review semantic identity rather
  than demanding the saved token. Do not inject that token from the reference.

Review these separately from model/engine failures. No frozen text, reference, manifest
or pilot acceptance record was changed for this implementation.

## Stage 3 — Execute

Agree provider/models, budget, smoke subset, snapshot and performance thresholds
before paid execution; record them in `run.json`. Run the smoke subset to check
plumbing, then the frozen full batch. Preserve smoke attempts and identify which
attempts enter full-run scores. Infrastructure failures remain recorded; unavailable
services do not count as successful document answers.

Save responses and provisional comparisons, set `pending-review` and stop for a
fresh reviewer. Execution cannot accept references or declare benchmark success.

## Stage 4 — Independent review and repair

Review all prompts in both languages in this order:

1. **Derive independent judgments.** Use readable questions, policies, facts and
   original document evidence. Save expected routes/conclusions and source-linked
   justifications before opening proposed references or assistant responses/traces.
2. **Check references.** Compare proposed expectations with independent judgments;
   assess question validity, novelty, coverage/category assignment, conditions,
   missing premises, conflicts and bilingual equivalence.
3. **Check interpretation.** Inspect retrieval, scenario selection, routing and query
   meaning. Allow semantically equivalent queries and valid alternative scenarios;
   matching status alone does not establish fidelity.
4. **Check responses.** Assess conclusions/bindings, actual proof steps and checker
   evidence, explanations, fictional-policy labels, document source support/citations
   and clarification usefulness. An LLM narrative is not a proof.

Attribute failures to dataset/reference, retrieval, routing, interpretation,
engine/checker, document evidence, explanation or infrastructure. Reviewers record
findings and stop; they do not repair. A separate author session makes versioned,
evidence-backed corrections, followed by a fresh independent recheck of changes
and affected cases. Retain unaffected valid judgments.

Allow **at most two shared repair rounds total**, across all layers. Re-execution
records new attempts under pinned inputs; broad code/prompt changes may require
rerunning the full batch. Never change a reference merely to fit an assistant
answer. Do not automatically replace questions or expand scope. Stop at acceptance
or the repair limit; at the limit record `completed-with-failures`, final scores and
remaining findings. No automatic additional loop is allowed.

Report initial and repaired results separately. Performance after tuning on these
questions is regression performance, not fresh held-out evidence.

## Scores and decisions

Keep denominators fixed: **288 prompts / 144 bilingual pairs**. A pair fully passes
only when both languages pass reference, interpretation and response checks.
Invalid references/questions, unresolved judgments, errors and unexecuted prompts
remain visible and cannot count as passes. Distinguish dataset failures from
assistant errors. Any diagnostic score restricted to valid references must show its
denominator alongside the full-set score.

Report overall and per-type/family/category/language scores, bilingual consistency,
candidate recall at the configured limit, routing accuracy, semantic query accuracy,
outcome correctness, proof validity, explanation/source fidelity, latency and costs.
Mark inapplicable metrics explicitly: boundary categories measure routing, not
deductions. Report document routing and document answer support separately.

Record two decisions: test-set validity and assistant performance. Validity requires
all coverage and references to pass independent review. Proposed assistant targets,
to confirm before Execute: at least 95% fully correct prompts overall, at least 90%
in each type/family/category/language slice, and zero critical errors. Critical
errors include invented customer premises, fictional policies presented as real
obligations, invalid proofs accepted, or unknown/conflict presented as supported
conclusions. Report pair scores separately. Aggregate scores cannot override invalid
references or critical errors; model agreement is not proof of correctness.

## Session prompts

Use one prompt per fresh session. Each requests only its named stage and does not
bypass repository approval rules or authorize later stages.

- **Generate:** Read `.agents/AGENTS.md` and
  `doc/aml-cft-reasoning-assistant-evaluation.md`. Perform Stage 1 for 288 prompts. Propose
  the minimal file-change plan, generate after approval, run preparation checks,
  save provisional references and update hashes/coverage/handoff. Stop before Stage 2.
- **Prepare runner:** Read the protocol and Stage 1 handoff. Perform Stage 2:
  propose the runner/test file-change plan, implement after approval, validate
  offline and document runnable commands. Stop before paid execution.
- **Execute:** Read the protocol and preparation handoffs. Perform Stage 3 after
  confirming experiment scope, budget, snapshot and criteria. Run the smoke subset
  and frozen batch, save checkpointed responses, mark pending review and stop.
- **Review:** Read the protocol and run manifests. Perform Stage 4's initial review
  in a fresh session. Save independent judgments before opening references or
  responses. Review all layers, report initial scores/findings and record acceptance
  or the next repair action. Do not repair.
- **Repair:** Read the latest review status/findings. Check the shared allowance,
  propose the narrow correction plan and implement after approval. Preserve original
  expectations, rerun affected checks within an authorized budget, increment the
  counter once for this round and stop for independent recheck.
- **Recheck:** Read status, findings and repair evidence in a fresh session. Recheck
  changes and affected cases independently; for revised questions, save new judgments
  before opening their references/results. Retain unaffected judgments, report scores
  and record acceptance, remaining allowance or completed-with-failures. Do not
  reset the counter or repair findings.
