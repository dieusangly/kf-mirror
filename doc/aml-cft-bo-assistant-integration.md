# Integrate the first BO batch into the reasoning assistant

## Session objective

Integrate the accepted beneficial ownership (BO) batch as a `source_backed` policy package. Verify that a new French or English question about an in-scope case can produce an engine-checked deduction and display the regulatory passages it used. One session can prepare the diff, implement it after approval, and run local validation. The independent BO pilot review is complete. A new full assistant evaluation is a separate task.

From the repository root, read the [project instructions](../.agents/AGENTS.md), the [implementation guide](../.agents/skills/implementation.md), the [BO handoff](aml-cft-bo-batch-handoff.md), and [Register another accepted dataset](aml-cft-reasoning-assistant.md#register-another-accepted-dataset) in the assistant guide. Check the working tree and the evidence below. Present the minimal diff plan with the affected paths and obtain approval under the repository rules before editing.

## Authoritative inputs

The `real-claims-bo-v1-batch-001` batch, dataset v1.0.0, was accepted after one repair round. It covers **9 cases, 18 French and English versions, and six categories**. The batch covers the pinned editions and cases assessed as of **6 July 2026**, for unlisted French companies in the context of an obliged financial institution. Check the exact scope of each policy in the dataset. [Batch status](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-002/evaluation-status.json) · [Independent acceptance](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-002/recheck-001/acceptance.json)

- Use the paths and hashes under `accepted_artifacts` for the manifest, readable inputs, source index, engine, schema, and configuration. The accepted encodings are in `recheck-001/replay-001/encodings-v2.jsonl`. Use the accepted queries and results for offline replay.
- Use the synthetic batch as the regression baseline and preserve the accepted BO review artifacts. This integration covers the assistant and its registry. The document corpus and its publication follow their existing workflow.

## Implementation plan

### 1. Load both acceptance formats

Compare `src/knowledge_foundry/assistant/datasets.py` with the synthetic and BO acceptance records. The current loader expects `decision` and `exact_sha256`. BO supplies `status` and `accepted_artifacts`. Support both formats through the same internal checks for decision, selection, and hashes.

Verify that the acceptance and status records agree and that the batch identity, case IDs, languages, and exact file hashes match. For BO, include the source index in those checks. Fail explicitly on an inconsistent format, mismatched hash, or unresolved passage. Register the batch in `config/aml-cft-reasoning-datasets.json` with `context_kind` set to `source_backed`. Keep saved references, queries, and answers in the evaluation and replay paths.

### 2. Prepare BO policies for new cases

Compare the predicates in the accepted encodings with `config/aml-cft-reasoning-vocabulary.json`. The initial inspection found that **all 27 predicates need entries**. Define their meanings, argument roles, and types from the encoded rules and facts. Check signatures when loading the batch.

Inspect `cases.py`, the routing logic, and the interpretation prompt. Select a BO policy when the question and supplied facts match its actor, case type, date, and subject. Route ambiguous or out-of-scope requests to clarification or document answering as appropriate. A missing premise under an applicable policy must be able to produce `unknown`. Derive new case facts from the user's assertions and retain their exact quotations.

**Resolve before enabling all policies.** The accepted rules for `BO-RC-EX-01` and `BO-RC-EX-02` contain `client_company` and `2026-07-06`. Identify which constants belong to the rule and which come from the pilot case. Test with a newly named company. The premises and conclusion must refer to that same company at a compatible date, and the rule must still check that the first-paragraph assessment was completed. Cover any adaptation with a test that preserves the accepted meaning. If the engine contract cannot support reuse, report the blocker and obtain a scope decision before enabling those policies.

### 3. Render source evidence in the deduction

Resolve clause `passage_ids` through the accepted source index. Each regulatory deduction must link to the source title or article, version, locator, exact excerpt, and official link when available. Fact steps must point to the user’s quoted assertions. Reuse the proof flow and source panel in `service.py` and `apps/assistant/app.py`. Keep the selected scope and engine-checked status visible if generated wording fails.

### 4. Validate locally and report the results

Start with deterministic tests using repository fixtures. Extend relevant tests for BO regressions. The current `scripts/replay_reasoning_pilot.py` reads `exact_sha256` and v1 filenames. Check its compatibility with the new registry. If necessary, support both acceptance formats in the replay script, or select the synthetic registry explicitly for the synthetic replay and replay BO separately from its accepted files.

```sh
PYTHONPATH=src:tests .venv/bin/python -m pytest -q tests/test_reasoning_assistant.py tests/test_reasoning_assistant_search.py tests/test_reasoning_assistant_ui.py tests/test_reasoning_assistant_inputs.py
```

List the required files in the diff plan. Consider the registry, vocabulary, loader, case construction and routing, source rendering, replay, and tests. Update the assistant guide if its integration status changes. Any broader engine or schema change needs an observed compatibility gap and a scope decision.

## Acceptance criteria

- The registry loads both batches and verifies their selections and hashes. A test confirms that it rejects a modified artifact or inconsistent acceptance.
- The synthetic replay reproduces its **192 accepted versions**. The BO replay reproduces its **18 accepted versions**, including the expected answers and certificates.
- Fresh French and English cases cover a supported deduction, a missing premise, a conflict, and an ambiguous or incompatible date or actor. Test the fallback rule for a newly named company both with and without the assessment-completion premise. Report any policy that remains blocked, with its cause and impact.
- The reasoning flow shows the checked status and exact passages. Conversation inputs exclude pilot customer facts and reference answers. The document route and synthetic policies pass their regression checks.
- The report lists changed files, commands, results, limitations, and the policies enabled. Distinguish fixture-based results from anything verified with a live model. Live tests follow the agreed budget and authorization. The known failures from the full assistant evaluation remain recorded with that evaluation.
