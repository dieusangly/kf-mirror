# First beneficial ownership batch

## Objective and starting point

Evaluate deductions about the **beneficial owners of a French company** using rules grounded in verified sources and synthetic customer facts. Run **Stage 5 → Stage 2 or verified setup reuse → Stage 3 → Stage 4 Execute → Stage 4 Review**. Work on one stage or role per session.

From the repository root, read [the project instructions](../.agents/AGENTS.md), the [pilot status](aml-cft-reasoning-pilot.md#progress-and-next-action), and the [synthetic pilot handoff](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-001/next-session-prompt.txt).

Verify the acceptance status and hashes for `synthetic-v1-batch-001`, which includes the beneficial ownership family (`BO`). Use that batch as the regression baseline. Confirm the proposed scope before drafting the new batch.

## Stage 5 — Select sources and prepare the batch

**First session.** Select the source passages and propose the case coverage. Confirm the scope, proposed cases, model, budget, and file plan under the repository rules.

- Start with **Article L. 561-2-2**, already in the [corpus manifest](../data/manifest.json). Propose **Article R. 561-1** as a versioned regulatory source for the Stage 5 batch. Record a copy, its official URL, the version identifier, the hash, and the exact passages selected. Use the 13-document graph to locate existing claims and passages. Ingesting R. 561-1 into the graph requires a separate document build. [L. 561-2-2](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000033517537/2026-08-22) · [R. 561-1](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000036824564)
- Inspect claims tagged `topic:beneficial-ownership` in the retained run's `claim-classification/` directory and trace them to their passages in `document-structuring/`. The ACPR customer due diligence guidelines can inform the cases. Verify each selected claim against the original text, its cross-references, scope, applicable version, conditions, and exceptions. For cases that depend on control by other means, include the relevant provisions of Article L. 233-3 of the French Commercial Code. [Retained document run](../artifacts/20261028-docling-aml-cft-01/)
- Propose a small French/English case set covering direct lookup, one-hop and multi-hop deduction, missing premises, exceptions, and conflicts. Identify the source that supports each deduction. Record any source or engine limitation that affects the proposed scope. Distinguish cases where exceeding a threshold is stipulated from cases where the engine must compare a raw percentage with the threshold.

**After approval.** Create `artifacts/aml-cft-reasoning-pilot/05-real-claims-run-001/`. Save a versioned manifest, stable identifiers, source passages, readable rules, case contexts, synthetic customer facts, and questions. Save **new proposed reference answers and proofs in separate files**. Link rules to their source passages and factual premises to facts stipulated in the case. Record the coverage limits and hand off the manifest and hashes to Stage 2.

## Stage 2 — Verify engine compatibility

Create the `BO` execution manifest with the exact case IDs and both language versions. Check the schema, engine, adapter, and proof checker. Record the exact versions and hashes of any reused setup. If the batch needs a new operation, document its semantics, representation, and proof checks. Version the extension and identify affected regression cases. Hand off verified validation and execution commands to Stage 3.

## Stage 3 — Formalize the cases

Encode the **source-backed rules**, **synthetic customer facts**, scope, and incompatibilities. Translate questions into separate queries. Give the formalizer the readable inputs, source references, and schema. Reserve reference answers and proofs for evaluation. Save validated encodings, queries, hashes, and any failures in a new `03-formalization-run-…` directory.

## Stage 4 Execute — Run the engine and check proofs

Initialize the batch status in a new `04-reasoning-run-…` directory. Verify case selection and hashes. Run the engine and proof checker on all agreed cases. Save answers, certificates, proof checks, provisional comparisons, and failures. Set the status to `pending-review` and hand off to an independent review session.

## Stage 4 Review — Decide acceptance

First, solve each case from the original passages and stipulated facts. Save those judgments before reading the proposed references or engine results. Then verify reference correctness, fidelity of encoded rules, facts, and queries, and the engine's answers and certificates. Check that the French and English versions preserve the same meaning.

Acceptance requires **100% of the agreed cases and required coverage**, with a justified status and proof for each case. An expected unknown or conflict can pass. Keep failures in the denominator. Assign repairs to a separate author session and recheck independently. The pilot allows up to two shared repair rounds per batch. Record `completed-with-failures` if defects remain at the limit.

## Handoff and traceability

- Give the batch its own run directories under `artifacts/aml-cft-reasoning-pilot/` and one authoritative status file. Use the accepted synthetic runs as the baseline. Carry the same batch identifier and counters through repairs.
- At each handoff, record the next role and exact action, batch ID and version, case selection, input and output paths and hashes, status, checks, and open findings. Record added or changed rules, predicates, or operations and the cases rerun.
- Update the run records required by the pilot guide and provide the exact prompt for the next session. Registering accepted rules in the assistant is a separate step that includes applicability and source rendering checks. The batch review establishes reasoning quality for the agreed cases and scope.
