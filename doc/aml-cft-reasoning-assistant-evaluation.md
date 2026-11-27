# AML/CFT reasoning assistant evaluation

## Status and purpose

Version **1.0.0** contains **288 prompts / 144 bilingual
pairs**. It tests new customer fact configurations under accepted pilot policies.
Stages 1–4 are complete. The final independent decision on 2026-11-18 is
**not accepted — `completed-with-failures`**. Both shared repair rounds are
exhausted. Dataset validity and reference fidelity passed independent review for
all prompts; assistant quality and construction compatibility did not meet every
acceptance requirement.

The reviewed candidate uses
[`01-dataset-run-005`](../artifacts/aml-cft-reasoning-assistant-evaluation/01-dataset-run-005/dataset-manifest-v1.json)
and [`03-live-run-004`](../artifacts/aml-cft-reasoning-assistant-evaluation/03-live-run-004/run.json).
The authoritative final review is
[`04-review-run-003`](../artifacts/aml-cft-reasoning-assistant-evaluation/04-review-run-003/evaluation-status.json):
[report](../artifacts/aml-cft-reasoning-assistant-evaluation/04-review-run-003/report.md),
[scores and slices](../artifacts/aml-cft-reasoning-assistant-evaluation/04-review-run-003/scores.json),
[findings](../artifacts/aml-cft-reasoning-assistant-evaluation/04-review-run-003/findings.json).

| Measure | Final result | Required |
| --- | ---: | ---: |
| Fully correct prompts overall | **264/288 (91.67%)** | 260/288 |
| New / variant / routing prompts | 90/96 · 94/96 · **80/96** | 82/96 each |
| English / French prompts | 134/144 · 130/144 | 123/144 each |
| Ambiguous / unsupported / document prompts | **25/32** · 32/32 · **23/32** | 28/32 each |
| Passing bilingual pairs | 124/144 | 123/144 |
| Passing base/variant groups | 41/48 | 39/48 |
| Critical errors | 0 | 0 |
| Construction answer/status/proof fidelity | **191/192** | 192/192 |
| Construction full response fidelity | **190/192** | 192/192 |

All 288 prompts remain in the denominators: 11 execution failures, 13 completed
incorrect answers and no unexecuted prompts. The execution failures comprise nine
confirmed interpretation input-token guard rejections and two incomplete model
outputs (`max_output_tokens`). No unresolved dataset/reference defects were found.

The failed checks concerned routing and compatibility with the construction set.

- **Routing.** The routing group fell two passes short. Ambiguous requests fell
  three short and document questions five short. All 32 unsupported requests
  received an appropriate clarification. Both languages, every family and every
  actual reasoning category met their floors.
- **Construction compatibility.** `BO-MH-02/fr` returned clarification for a
  supported deduction. `RISK-MP-01/en` correctly returned unknown but described
  the customer as a complex product.

New cases and variants scored 184/192 (95.83%) on supplied synthetic facts.
Completed failures included rejected interpretations, incorrect missing-premise
explanations, unanswered or wrong-provision document questions, and a language
error. Review covers the full response, including wording and citations. These
cases were exposed during tuning. Generalization requires held-out cases.

The final report retains validation, provenance, latency and
incomplete cost estimates, including its historical **92 passed / 4 failed** test
result caused by stale default-dataset source pins. That result is not rewritten
by subsequent repository maintenance.

The frozen criteria were selected **after the original Stage 3 execution**, before
Stage 4 correctness review; thresholds and denominators were unchanged through
repairs. The original execution remains preserved in
[`03-live-run-001`](../artifacts/aml-cft-reasoning-assistant-evaluation/03-live-run-001/run.json):
287 completed requests and one failure, under
[`stage3-agreement.json`](../artifacts/aml-cft-reasoning-assistant-evaluation/stage3-agreement.json).
Its $8.64 reservation is not a billed-cost total.

## Retained runs

All paths below are relative to `artifacts/aml-cft-reasoning-assistant-evaluation/`.

| Runs | Purpose |
| --- | --- |
| `01-dataset-run-005`, `03-live-run-004`, `04-review-run-003` | Reviewed candidate and authoritative final decision. |
| `01-dataset-run-007` | Current offline default. Pins the accepted queries for the synthetic, BO and KYC batches. Uses the existing 288 prompts and provisional references. |
| `01-dataset-run-006` | Earlier offline preparation with stale source hashes. |
| `01-dataset-run-002` through `004` | Historical source pins referenced by the initial reviews, repairs and final provenance audit. |
| `03-live-run-001` through `003`, `stage3-agreement.json` | Original and repair attempts, including the aborted run and spending reconciliation. |
| `04-review-run-001`, `04-review-run-002` | Initial findings, repair records, construction regressions, frozen inputs and cumulative budget evidence. |

The superseded `02-offline-run-002`, `02-offline-run-003` and
`02-offline-run-003-runtime-smoke` folders were removed. Retained Stage 2 evidence
lives under `04-review-run-002/evidence/` (regression tests, runtime smoke and
inventory), with independent checks in `04-review-run-003`. Older retained runs
remain historical evidence even when their source pins differ from today's code.

## Dataset design

| Type | Display name | Semantic cases | Prompts |
| --- | --- | ---: | ---: |
| `new` | New cases | 48 | 96 |
| `variant` | Variants of new cases | 48 | 96 |
| `routing` | Routing and sources | 48 | 96 |

New cases cover every combination of KYC, BO, PEP, RISK, MON, TFS, PAY and CORR
with DL (direct lookup), OH (one hop), MH (multiple hops), MP (missing premise),
EX (exception/authorisation) and CF (conflict). Each has English and French text.

Each new case uses accepted policies and newly authored synthetic facts. Multiple
entities, complete and incomplete chains, multiple answer bindings, scoped
exceptions and independent conflicts replace the pilot's single-target questions.
Renaming entities or appending irrelevant facts alone does not qualify as novelty.

Each variant names its `base_case_id`, retains the policy and question, and adds
or removes exactly one stated fact. Its authoring record specifies the expected
answer relationship. `coverage_category` identifies the base matrix cell;
`category` describes actual reasoning behavior. For example, supplying a missing
premise can change MP to MH. IDs retain the base cell for stable linkage, such as
`RAE1-variant-KYC-MP-01-en`; consumers must use `category` for actual behavior.

Routing has two cases per family for each of `ambiguous`, `unsupported` and
`documents`. Its reasoning category is null. Missing customer premises under an
established policy require reasoning with an unknown outcome. Unspecified policy
applicability, unsupported scoring or unresolved temporal semantics require
clarification. Document questions use pinned original passages and saved editions;
source-topic novelty and current legal applicability are not claimed.

Question text contains facts, a natural-language question and enough policy context
to distinguish the accepted rules. The shared reasoning semantics come from the
runtime's retrieved accepted policy scope and engine configuration. The repeated
pilot scope block, expected status labels, internal case IDs and reference answers
are absent from submitted text. Context still describes relevant fictional domain
conditions; this is a known-policy test, not an unseen-policy formalization test.

## Stage 1 — Generate questions and proposed references

The committed authoring source is
[`config/aml-cft-reasoning-assistant-evaluation-cases.jsonl`](../config/aml-cft-reasoning-assistant-evaluation-cases.jsonl).
It contains bilingual contexts, atomic fact statements, questions, reference fact
atoms/queries, intended statuses, novelty rationales, variant operations and document
evidence. Generated outputs belong in the ignored `artifacts/` directory. The
generator reproduces those authored records deterministically without
model or database calls. To author another version, deliberately change and review
these inputs; rerunning the generator does not invent new cases.

For each reasoning prompt:

1. Select the accepted policy and copy only its rules, incompatibilities and constants.
2. Build a fresh reference encoding from the authored facts and query, without pilot
   customer assertions or saved answers.
3. Run the reasoning engine and proof checker. Reject disagreement with the authored
   status or variant relationship.
4. Save the expected bindings, supported atoms, missing/conflicting-premise evidence
   and certificates separately from the prompt.

For routing, save a source-linked explanation of the expected route. Document
references include the original file hash, extracted passage and locator, and a
proposed substantive answer. Source integrity checks do not establish interpretation
correctness or published-service availability.

Preparation verifies schemas, hashes, source links, counts, bilingual symbolic
agreement, actual proof depths/categories, exactly one fact change, answer
relationships, deterministic reproduction and a conservative pilot-renaming screen.
The renaming screen compares predicate counts and query shapes against the accepted
encodings and queries for 121 pilot cases in both languages. The evaluation's
reasoning prompts still use fictional policies. Measuring live performance on
source-backed BO and KYC requires separately authored and independently reviewed
prompts. Independent review checks relevance, novelty, natural phrasing, translation
and fact/query fidelity. The same engine generates references and executes
assistant cases, so the references assess the assistant pipeline.

```sh
.venv/bin/python scripts/generate_reasoning_assistant_evaluation.py --output /tmp/assistant-evaluation-v1-NEW
.venv/bin/python scripts/validate_reasoning_assistant_evaluation.py --dataset /tmp/assistant-evaluation-v1-NEW
```

Use a fresh output directory. The manifest pins authoring inputs, accepted sources,
engine/checker configuration, generator/validator code and development fixtures.
Later changes require a new version/run and updated hashes. Do not silently amend
references after observing assistant answers.

## Stage 2 — Prepare and verify the runner

The runner reads the versioned manifest and dataset counts. It submits exactly
`Assistant.ask(question['text'], history=[])`. Reference files are hash-checked but
never parsed for submission. Policies remain available through normal retrieval;
source-case mappings, reference queries, expected answers and variant links are
never injected. Keep evaluation cases outside the reasoning registry.

Runtime unit tests and mocked smoke use dedicated development fixtures, separate
from the evaluation prompts. Controlled interpretations and embeddings establish
orchestration behavior only. The pilot's saved-query regression command is
`scripts/replay_reasoning_pilot.py`; it is not a natural-language quality score.

```sh
PYTHONPATH=src:tests .venv/bin/python -m pytest tests/test_reasoning_assistant.py tests/test_reasoning_assistant_search.py tests/test_reasoning_assistant_inputs.py tests/test_reasoning_assistant_ui.py tests/test_reasoning_assistant_evaluation_dataset.py -q
PYTHONPATH=src:tests .venv/bin/python tests/record_reasoning_input_smoke.py --output /tmp/assistant-runtime-smoke-NEW
.venv/bin/python scripts/evaluate_reasoning_assistant_inputs.py --output /tmp/assistant-evaluation-inventory-NEW
```

The generator, validator and runner default to `01-dataset-run-007`. With the
accepted pilot artifacts available, run the generator without `--output` to
prepare that directory. Each generation requires a fresh path. The evaluation
dataset tests generate their own temporary package. The runner defaults to offline
inventory and accepts `--dataset` to select another compatible frozen dataset.

## Stage 3 — Execute

Agree provider/model configuration, smoke selection, cost reservations, published
snapshot and acceptance thresholds before paid calls. The agreement JSON requires:

- `config_sha256`: SHA-256 of `json.dumps(load_config(), sort_keys=True).encode()`;
  use the effective `--config` when selecting an alternate configuration.
- Positive `api_budget_usd` and `reserve_usd_per_prompt`, with
  `cost_reservation_acknowledged: true`. Include initial embeddings and document
  calls. Reservations bound attempted prompts, not provider billing.
- With document services enabled, `snapshot_id` and `retrieval_config_sha256`
  pin the published snapshot and configured retrieval JSON.

```sh
.venv/bin/python scripts/evaluate_reasoning_assistant_inputs.py --execute --agreement /path/to/agreed-run.json --max-prompts 4 --output /tmp/assistant-evaluation-live-NEW
.venv/bin/python scripts/evaluate_reasoning_assistant_inputs.py --execute --agreement /path/to/agreed-run.json --output /tmp/assistant-evaluation-live-NEW --resume
```

An omitted `--max-prompts` allows the remaining dataset. Resume requires identical
question, manifest, code, accepted inputs, dependencies, configuration and agreement
identities. Every attempt is checkpointed, including errors. Resume skips recorded
failures; deliberate retries use separate runs with explicit attempt accounting.
Preserve smoke attempts and identify which attempts enter the full score.

Save full response traces, routes, candidates, interpreted facts/queries, engine
results, proof checks, provenance, citations, latency and available usage. Check
the pinned snapshot before and after requests and stop on change. Missing services
or evidence cannot count as supported document answers. Failed and unexecuted
prompts remain in totals. Cost estimates may be incomplete after document failures;
do not claim a complete billed total. Exit codes: 0 completed requests/inventory,
1 failures or unexecuted prompts, 2 input/configuration error.

Execution ends `pending-review` (or `partial`); it cannot accept references or
establish quality from completion counts.

## Stage 4 — Independent review and bounded repair

Before correctness review, record the thresholds below in
`config/aml-cft-reasoning-assistant-evaluation-criteria.json` and freeze an identical
copy as `04-review-run-001/acceptance-criteria.json`. Record their SHA-256 and the
dataset, reference and response hashes in each review manifest and score file.
Keep the criteria unchanged through repairs; their timing is recorded in
[Status and purpose](#status-and-purpose).

Use a fresh reviewer session. Before opening references, reference encodings,
engine results or assistant traces, derive judgments from the submitted prompts,
readable accepted policies, stipulated facts and original document evidence. Save
these independent judgments first. A review-only export should expose those inputs
without the authoring file's symbolic targets or intended statuses.

Then check:

1. Dataset validity: meaning, novelty, applicable policy, actual category, bilingual
   equivalence and controlled base/variant relationship.
2. Reference fidelity: fact/query encodings match the readable meaning and expected
   bindings; engine certificates are valid under the pinned inputs.
3. Assistant interpretation: retrieval, routing, entities, fact provenance, query
   meaning and policy applicability. Matching status alone is insufficient.
4. Responses: all supported bindings, proof steps, missing/conflicting premises,
   explanations, fictional-policy labels and document citation support.

Allow equivalent policies, symbol names and proof paths when meaning is preserved.
Classify failures as dataset/reference, retrieval, routing, interpretation,
engine/checker, explanation, document evidence or infrastructure. Session A saves
its initial judgments, verdicts and findings before switching to an author role
for one repair round and rerun. Its post-repair checks are provisional; it cannot
independently accept its own repairs. Session B, a fresh reviewer session, repeats
the independent review of the candidate results and reports acceptance or findings
without repairing. Preserve all initial findings and attempts. Never change an
expectation merely to fit a response or special-case evaluation IDs.

The synthetic pilot is the construction set. Every repair must preserve its
accepted answers/statuses and valid proofs across all 96 cases / 192 language
versions, using `scripts/replay_reasoning_pilot.py` and relevant regression tests.
The replay also checks the 18 BO and 32 KYC versions.
Retrieval, interpretation or wording repairs also require live natural-language
regression on affected construction cases in both languages; broad shared changes
require all 192 versions. Mocked fixtures alone do not establish this compatibility.
Record coverage and results. Do not overwrite accepted baselines or acceptance
pins to force a pass; genuine construction-set corrections need versioned,
independent review. Unresolved construction regressions block acceptance.

Regenerate/version Stage 1 artifacts when questions, references or pinned sources
change. Repeat relevant Stage 2 checks after implementation changes; update the
runner only if it is defective. Rerun Stage 3 in a fresh directory when assistant
behavior or supplied questions change; shared runtime changes require all 288
prompts. A reference-only correction may reuse responses if inputs and runtime are
unchanged. Refresh run identities and agreements, and record one budget ledger
across all new paid validation calls. Additional paid execution requires explicit
budget authorization. Then hand off to Session B with candidate paths/hashes,
construction regression evidence, changes, attempts and the repair counter;
keep answer-free reviewer inputs separate from findings and expected answers.

Allow at most **two shared repair rounds** across all layers. Keep initial and
repaired results separate. At the limit, record `completed-with-failures`.

Review outputs belong in a fresh review directory: `independent-judgments.jsonl`,
`per-question-verdicts.jsonl`, `findings.json`, `scores.json` and
`evaluation-status.json`, with artifact hashes and the repair counter. Session A
also saves a handoff manifest identifying the candidate and filtered review inputs;
Session B uses a separate review directory and the same frozen criteria.

## Scores and decisions

Keep **288 prompts / 144 bilingual pairs** as fixed denominators. Invalid references,
errors, unresolved judgments and unexecuted prompts remain visible and never count
as passes. Report dataset validity separately from assistant performance, and show
the denominator of any valid-reference-only diagnostic score.

Report by type, family, actual category and language: routing and query accuracy,
full answer-binding correctness, proof validity, explanation/provenance fidelity,
document support, latency and available costs. Candidate recall accepts semantically
equivalent policies. Mark inapplicable metrics explicitly; routing cases have no
deduction category. Document routing success and evidence-supported answering are
separate measures.

A bilingual pair passes only when both prompts pass. A base/variant group passes
only when both bilingual pairs pass and the answer relationship is correct. There
are 48 linked groups of four prompts and 48 routing pairs; translations and variants
are correlated observations, not independent sample replications.

The frozen criteria require:

| Measure | Minimum | Required passes |
| --- | ---: | ---: |
| Fully correct prompts overall | 90% | 260/288 |
| Fully correct prompts in each question type | 85% | 82/96 per type |
| Fully correct prompts in each language | 85% | 123/144 per language |
| Fully correct prompts in each family | 80% | 29/36 per family |
| Fully correct prompts in each actual reasoning category | 80% | DL/OH/EX: 26/32; MH: 52/64; MP/CF: 13/16 |
| Fully correct prompts in each routing kind | 85% | 28/32 each for ambiguous, unsupported and documents |
| Passing bilingual pairs | 85% | 123/144 |
| Passing base/variant groups, including the answer relationship | 80% | 39/48 |
| Critical errors | Zero | 0 |

All gates must pass. Apply slice floors separately by dimension, not to small
intersections such as family × category × language. Round required pass counts
upward, retaining the fixed denominators and failure accounting above.

A fully correct response uses an appropriate route, preserves the supplied facts,
and provides the complete supported answer and explanation required for its type.
For clarification, assess the unresolved issue and useful follow-up; for document
answers, assess source support and citations. Equivalent wording, symbol names and
proof paths are acceptable. Record minor stylistic issues separately without
failing an otherwise correct response. Report latency and costs without a hard
acceptance threshold for this first live run.

Critical errors include invented customer premises, fictional rules presented as
law, invalid proofs accepted, or unknown/conflict presented as supported
conclusions. Unresolved dataset/reference validity findings block acceptance and
must be reported separately from assistant errors. Aggregate scores cannot
override invalid references or critical errors.
