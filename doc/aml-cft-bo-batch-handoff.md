# First beneficial ownership batch

**Accepted after one repair round.** Batch `real-claims-bo-v1-batch-001` covers
9 cases (18 FR/EN versions) on beneficial ownership of French companies.
The [pilot guide](aml-cft-reasoning-pilot.md#find-accepted-cases-and-sources)
links the accepted cases and sources. The [assistant guide](aml-cft-reasoning-assistant.md#source-backed-beneficial-ownership)
gives the scope, required facts and limits of the registered package.

## Launch instructions

Use verified source passages and synthetic customer facts. Run **Stage 5 → Stage 2 or verified setup reuse → Stage 3 → Stage 4 Execute → Stage 4 Review**. Work on one stage or role per session.

From the repository root, read [the project instructions](../.agents/AGENTS.md), the [pilot status](aml-cft-reasoning-pilot.md#progress-and-next-action), and the [synthetic pilot handoff](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-001/next-session-prompt.txt).

Verify the acceptance status and hashes for `synthetic-v1-batch-001`, which includes the beneficial ownership family (`BO`). Use that batch as the regression baseline. Confirm the proposed scope before drafting the new batch.

## Stage 5 — Select sources and prepare the batch

**First session.** Select the source passages and propose the case coverage. Confirm the scope, proposed cases, model, budget, and file plan under the repository rules.

- Start with **Article L. 561-2-2**, already in the [corpus manifest](../data/manifest.json). Propose **Article R. 561-1** as a versioned regulatory source for the Stage 5 batch. Record a copy, its official URL, the version identifier, the hash, and the exact passages selected. Use the 13-document graph to locate existing claims and passages. Ingesting R. 561-1 into the graph requires a separate document build. [L. 561-2-2](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000033517537/2026-08-22) · [R. 561-1](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000036824564)
- Inspect claims tagged `topic:beneficial-ownership` in the retained run's `claim-classification/` directory and trace them to their passages in `document-structuring/`. The ACPR customer due diligence guidelines can inform the cases. Verify each selected claim against the original text, its cross-references, scope, applicable version, conditions, and exceptions. For cases that depend on control by other means, include the relevant provisions of Article L. 233-3 of the French Commercial Code. [Retained document run](../artifacts/20261028-docling-aml-cft-01/)
- Propose a small French/English case set covering direct lookup, one-hop and multi-hop deduction, missing premises, exceptions, and conflicts. Identify the source that supports each deduction. Record any source or engine limitation that affects the proposed scope. Distinguish cases where exceeding a threshold is stipulated from cases where the engine must compare a raw percentage with the threshold.

**After approval.** Create `artifacts/aml-cft-reasoning-pilot/05-real-claims-run-001/`. Save a versioned manifest, stable identifiers, source passages, readable rules, case contexts, synthetic customer facts, and questions. Save **new proposed reference answers and proofs in separate files**. Link rules to their source passages and factual premises to facts stipulated in the case. Record the coverage limits and hand off the manifest and hashes to Stage 2.

## Setup, execution and independent review

Follow the [pilot stage handoffs](aml-cft-reasoning-pilot.md#stage-handoffs) for
the required manifests, commands, validation and status transitions.

- **Stage 2.** Pin the BO selection in both languages and verify setup reuse by
  version and hash. Any new operation needs documented semantics, representation,
  proof checks, a versioned extension and identified regression cases.
- **Stage 3.** Encode the rules, stipulated facts, scope and incompatibilities.
  Translate questions separately and keep references out of formalizer inputs.
- **Stage 4.** Run the engine and proof checker on the fixed selection. At
  `pending-review`, hand off to a fresh reviewer to solve the cases from the
  original passages and stipulated facts. Apply the [review criteria and shared
  repair budget](aml-cft-reasoning-pilot.md#review-policy-and-repair-budget).

At each handoff, retain the batch ID, counters, selection, versions, paths and
hashes, checks, open findings, and exact next-session prompt. Record any changed
rules, predicates or operations and the cases rerun. Keep evidence in the
batch's own run directories and link its [authoritative status](../artifacts/aml-cft-reasoning-pilot/04-reasoning-run-002/evaluation-status.json).
