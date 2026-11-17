# AML/CFT reasoning assistant evaluation

## Status and purpose

Version **1.0.0** contains **288 prompts / 144 bilingual
pairs**. It tests new customer fact configurations under accepted pilot policies.
Stage 1 generates questions and separate engine-checked proposed references.
Preparation and offline runner checks do not establish real-model performance.
References remain provisional until independent review.

Evidence lives under
[`artifacts/aml-cft-reasoning-assistant-evaluation/`](../artifacts/aml-cft-reasoning-assistant-evaluation/):

- [`01-dataset-run-001/questions-v1.jsonl`](../artifacts/aml-cft-reasoning-assistant-evaluation/01-dataset-run-001/questions-v1.jsonl): submitted prompts.
- [`01-dataset-run-001/dataset-manifest-v1.json`](../artifacts/aml-cft-reasoning-assistant-evaluation/01-dataset-run-001/dataset-manifest-v1.json): versions, hashes, source mappings and variant links.
- [`01-dataset-run-001/proposed-references-v1.jsonl`](../artifacts/aml-cft-reasoning-assistant-evaluation/01-dataset-run-001/proposed-references-v1.jsonl): expected routes, outcomes and source evidence; accompanying `reference-encodings-v1.jsonl`, `reference-queries-v1.jsonl` and `reference-results-v1.jsonl` contain symbolic inputs and checked certificates.
- [`01-dataset-run-001/preparation-checks.json`](../artifacts/aml-cft-reasoning-assistant-evaluation/01-dataset-run-001/preparation-checks.json) and [`run.json`](../artifacts/aml-cft-reasoning-assistant-evaluation/01-dataset-run-001/run.json): Stage 1 checks and handoff.
- [`02-offline-run-001/report.json`](../artifacts/aml-cft-reasoning-assistant-evaluation/02-offline-run-001/report.json): offline verification and reproduction commands.

Next: agree effective providers/models, API budget, reservations and a verified
published document snapshot before live execution. No paid calls or independent
review have been performed. The shared repair counter starts at zero.

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
[`config/reasoning-assistant-evaluation-cases.jsonl`](../config/reasoning-assistant-evaluation-cases.jsonl).
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
The renaming screen compares predicate multiplicities and query shapes; it is not a
semantic novelty oracle. Independent review must check relevance, novelty, natural
phrasing, translation, and faithful fact/query encoding. The same engine generates
references and executes assistant cases, so these references assess the assistant
pipeline, not independently establish the engine algorithm's correctness.

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

The default runner performs an offline inventory. `--dataset` selects another
compatible frozen dataset directory. Reference generation and preparation are
separate from assistant execution.

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
engine/checker, explanation, document evidence or infrastructure. Reviewers report
findings and stop. Separate author sessions make versioned corrections, followed by
fresh independent rechecks. Never change an expectation merely to fit a response.

Allow at most **two shared repair rounds** across all layers. Keep initial and
repaired results separate, preserve attempts, and rerun affected cases; broad shared
changes may require the full batch. At the limit, record `completed-with-failures`.
Tuning against this set makes subsequent scores regression evidence; generalization
claims then require newly held-out cases.

Review outputs belong in a fresh review directory: `independent-judgments.jsonl`,
`per-question-verdicts.jsonl`, `findings.json`, `scores.json` and
`evaluation-status.json`, with artifact hashes and the repair counter.

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

Proposed live-run thresholds, to confirm before execution: at least 95% fully correct
prompts overall, 90% in each type/family/actual-category/language slice, and zero
critical errors. Critical errors include invented customer premises, fictional rules
presented as law, invalid proofs accepted, or unknown/conflict presented as supported
conclusions. Aggregate scores cannot override invalid references or critical errors.

Each session performs only its authorized stage under `.agents/AGENTS.md`. This
protocol does not authorize paid execution or modification of pilot acceptance.
