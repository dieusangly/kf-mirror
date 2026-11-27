# First source-backed KYC batch

**Accepted after one repair round.** Batch `real-claims-kyc-v1-batch-001`
covers **16 cases (32 FR/EN versions)** on identification, identity verification
and representative authority at ordinary account opening by covered French
credit institutions on 6 July 2026. The accepted
package is registered as `source_backed` for the assistant and contributor
catalogue. Offline checks cover case construction, proofs and the bilingual
catalogue. Live routing and interpretation require separate evaluation.
[Accepted cases and sources](aml-cft-reasoning-pilot.md#find-accepted-cases-and-sources) · [Runtime scope and validation](aml-cft-reasoning-assistant.md#source-backed-kyc)

## Launch instructions

The launch prompt authorized a KYC-specific exception to the pilot's session
rule. One author session runs **Stage 5 → Stage 2 → Stage 3 → Stage 4 Execute**
with the existing stage gates. The independent Stage 4 reviewer, any repairing
author and each independent recheck use separate sessions. Follow the
[project instructions](../.agents/AGENTS.md) and the
[pilot guide](aml-cft-reasoning-pilot.md#stage-handoffs).

Verify the accepted synthetic KYC baseline (12 cases, 24 French/English
versions) and preserve the accepted BO batch as a second regression baseline.
Propose a local budget of one initial execution and one fresh replay, with
zero paid API calls. Confirm the actual Codex model and effort, budget and
minimal file plan before drafting. [Pilot status and accepted runs](aml-cft-reasoning-pilot.md#progress-and-next-action)

## Author session

### Stage 5 — Select rules and design cases

Use `KYC21` and retained customer due diligence claims to locate relevant
passages. Verify the original text, assessment date, actor, applicability,
conditions, exceptions and statutory cross-references. Start with Article
L. 561-5 and the relevant parts of Articles R. 561-5 to R. 561-6, including
representative authority under R. 561-5-4. Consider L. 561-8 if a case expressly
establishes inability to meet an obligation. Pin any additional official text
as a batch source with its edition, URL, locator, exact excerpt and hash.
Distinguish statutory requirements from ACPR guidance. Keep corpus ingestion
and publication outside this batch.
[Corpus manifest](../data/manifest.json) · [Retained due diligence claims](../artifacts/20261028-docling-aml-cft-01/claim-classification/topics/customer-due-diligence.json) · [ACPR KYC21](../data/KYC21-20220404_lignes_directrices_revisees_relatives_identification_verification_connaissance.pdf) · [L. 561-5](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000033517742/2026-09-30) · [R. 561-5 to R. 561-6](https://www.legifrance.gouv.fr/codes/section_lc/LEGITEXT000006072026/LEGISCTA000021019145/2026-08-25/) · [L. 561-8](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000041577826/2026-10-28)

List the distinct rules in scope. Group equivalent formulations and preserve
differences in actors, premises, exceptions and versions. Map each selected
rule or branch to its source, case IDs and intended behavior. Cover each
category with at least two structurally distinct bilingual cases.

| Category | Coverage to propose |
| --- | --- |
| Direct lookup | Retrieve an asserted document or recorded verification fact, with zero rule applications. |
| One-hop deduction | Exercise distinct identification or verification conditions for a client or representative. |
| Multi-hop deduction | Trace two genuinely different source-supported chains through explicit intermediate conclusions. Limit claims to the obligations the sources and facts establish. |
| Missing premise | Vary a missing identity-verification premise and a missing authority premise. Expect `unknown` for a conclusion that depends on the missing premise. |
| Exception | Exercise the conditions for deferred verification and the applicable deadline. Include a control that removes or contradicts a required condition. |
| Conflict | Exercise explicit incompatible findings within the same actor, dossier and assessment scope. Check that only affected conclusions are withheld. |

Add a case for any important uncovered branch in the agreed scope before
freezing the selection. If the sources cannot support a planned deduction,
record the gap and resolve the selection before formalization. Keep document
submission, identity verification and authority verification distinct. Bind
client, representative, institution and date consistently in each case.

Save stable bilingual IDs, the coverage matrix, versioned source index and
manifest, readable inputs and questions. Save new provisional reference
answers and proofs in separate files. Identify synthetic assumptions and
link each rule and proof premise to its source passage or stipulated fact.

### Stages 2, 3 and 4 Execute — Verify, formalize and run

Dataset `aml-cft-real-claims-kyc` is version `1.0.0`. The recorded run
directories below are relative to `artifacts/aml-cft-reasoning-pilot/`. Verify
run numbers before allocating directories for a new batch.

| Stage | Run directory | Gate to continue |
| --- | --- | --- |
| 5 | `05-real-claims-run-002/` | Freeze the source-backed dataset, exact FR/EN selection, matrix and hashes. |
| 2 | `02-engine-setup-run-003/` | Create the KYC execution manifest. Verify the pinned engine, schema, adapter, proof checker and split-reference compatibility. Reuse the existing setup if compatible. Record verified commands and obtain a scope decision if an operation is missing. |
| 3 | `03-formalization-run-003/` | Generate encodings and separate queries using only the selected readable inputs, questions, applicable source IDs and schema. Keep proposed answers, proofs and case-specific evaluation hints out of the formalizer inputs. Save validation, failures, hashes and deterministic replay evidence. |
| 4 Execute | `04-reasoning-run-003/` | Initialize `evaluation-status.json`, verify the pinned inputs, run the engine and checker, and save results, certificates, proof checks, provisional comparisons and failures. Keep the agreed denominator. Set `pending-review` and stop with the exact independent review prompt. |

At each gate, record the selection, versions, hashes, validation, failures and
next action. Keep `run.json`, session evidence, `commands.jsonl` and replayable
`commands.sh` as required by the pilot, using fresh output paths and preserving
prior artifacts. [Execution records](aml-cft-reasoning-pilot.md#agent-responsibilities-and-reproducible-commands)

## Independent review

Solve all selected FR/EN versions from the original sources, readable cases
and questions. Save and hash the judgments before opening references,
encodings, queries or execution evidence. Apply the pilot's criteria for
reference accuracy, encoding fidelity and checked proofs, including its fixed
denominator and two-round repair limit.
[Review policy](aml-cft-reasoning-pilot.md#review-policy-and-repair-budget) · [KYC acceptance after independent recheck](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-003/recheck-001/acceptance.json)

The [archived launch prompt and approved scope](../artifacts/aml-cft-reasoning-pilot/05-real-claims-run-002/prompts/request.txt)
record the selection, execution budget and session exception.
