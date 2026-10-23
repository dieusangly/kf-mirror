# Claim evaluation

Review claims/classifications against saved originals. The evaluator checks
artifacts and scores judgments without model calls; outputs remain drafts.

After [generation/classification](claim-generation-and-classification.md), select a saved run from
the repository root; set directories separately if stage run IDs differ:

```sh
RUN_ID="your-completed-run-id"
CANDIDATES_DIR="artifacts/${RUN_ID}/claim-generation"
CLASSIFIED_DIR="artifacts/${RUN_ID}/claim-classification"
CLAIM_REVIEW_DIR="artifacts/${RUN_ID}/claim-review"
```

Frozen context lives in `CANDIDATES_DIR/preparation`; superseded claim/context runs
are unnecessary. Keep judgments/reports in the separate claim-review stage.

## One agent-review task

After classification, the [agent prompt](../src/knowledge_foundry/prompts/agent_prompt_claim_review)
initializes/reuses the review, inspects originals and evaluates judgments. Stale
snapshots or failed automatic checks stop inspection; pending is expected initially.

```text
Read AGENTS.md and .agents/AGENTS.md, then follow
src/knowledge_foundry/prompts/agent_prompt_claim_review
for RUN_ID="your-completed-run-id".

Complete initialization/reuse, source review, and final evaluation.
Preserve existing review judgments and report the final results.
```

## Human-review setup

Run once before handing the review file to a human:

```sh
direnv exec . .venv/bin/python scripts/auto_review_claims.py \
  --candidates-dir "$CANDIDATES_DIR" \
  --classified-dir "$CLASSIFIED_DIR" \
  --initialize-review "$CLAIM_REVIEW_DIR/review.json" \
  --output "$CLAIM_REVIEW_DIR/report.json"
```

Creates `report.json` and pending `review.json` in `CLAIM_REVIEW_DIR`.
Source review is still required. Both steps accept:

| Argument | Default / purpose |
| --- | --- |
| `--manifest PATH` | `data/manifest.json`; source versions, hashes and saved paths. |
| `--source-root PATH` | Repository root; resolves manifest `local_path` entries. |
| `--taxonomy PATH` | `config/topics-v1.json`; must match the classification build. |
| `--review-policy PATH` | `config/review-policy.json`, subject `claim_generation_and_classification`. |
| `--sample-per-document N` | 12 per document for each of generation, classification and completeness. |
| `--output PATH` | Another evaluation-report location. |

`--initialize-review PATH` selects another review location and refuses to
overwrite an existing file. Evaluation outputs cannot overwrite input artifacts.
Use the same inputs, source root, taxonomy, policy and sample size in both steps.

## Review saved sources

Inspect every sample against originals. Preserve judgments; never reinitialize
an existing review or copy another build's approval.

Automatic checks validate schemas, hashes, IDs, exact spans, unchanged candidate
claims, stage errors, source versions, inverse links, JSONL exports and topic views.
For frozen structural chunks and semantic clusters, they also validate unique
membership, reconstructed structural links, similarity, request assumptions and
candidate-claim lineage. Legacy candidate claim artifacts remain readable.
Evaluation makes no embedding or generation calls.
Missing candidate claims and null classifications need source review; they are not
automatically wrong.

`evidence-accounting.json`, when present, is checked for references, candidate links,
membership and counts. Inspect unresolved/high-risk dispositions: `represented`
does not prove that a claim preserves every proposition or qualification. Older
runs remain reviewable without accounting.

Sampling selects available risks first, then spreads through reading order:

| Dimension | Review unit and purpose |
| --- | --- |
| Generation | Candidate claim: full support, atomicity, scope, conditions and exceptions, including unclassified candidate claims. |
| Classification | Candidate claim outcome: main proposition fits the topic scope or justifies null. |
| Completeness | Selected chunk: missing rules, list options or qualifications; also check nearby source and candidate claims citing overlapping evidence. |
| Topics | Every exported view: draft labels, traceability and unsupported synthesis. |

Risks: unclassified outcomes, classified narratives, notes, tables/lists, qualifiers,
cross-chunk/document evidence, repeated context and uncited chunks. Small populations
contribute all items; cross-document units count once/dimension. Unselected chunks
are excluded.

Samples include evidence, locations, source versions and selection reasons.
Generation/classification share sampled candidates; resolve their IDs in
`CANDIDATES_DIR/claim-candidates.jsonl`, not classified claim records.

Preserve `build_id`, `snapshot_sha256`, sample IDs and dimensions. Record reviewer
kind (`human` or `agent`), name and model when known. Set every status to `pass`,
`fail` or `pending`; uninspected items remain pending. Pending/failed items need
source-located notes. Record one worst severity per failed unit:

- **Critical:** materially incorrect or fabricated rule, unsupported obligation,
  or evidence that cannot support the assertion.
- **Major:** misleading missing rule, qualification, exception or topic assignment.
- **Minor:** clarity/presentation defect without a meaning change.

Put extra failures in `additional_findings`: valid `<dimension>:<record-id>`,
dimension, `status: fail`, severity and source-located notes. Count once; no sampled
duplicates or extra passes. Count a claim defect as a topic defect only if the
view adds an independent problem.

## Rerun evaluation with the human or agent review

After inspection, reuse the initialization arguments with `--review-file`:

```sh
direnv exec . .venv/bin/python scripts/auto_review_claims.py \
  --candidates-dir "$CANDIDATES_DIR" \
  --classified-dir "$CLASSIFIED_DIR" \
  --review-file "$CLAIM_REVIEW_DIR/review.json" \
  --output "$CLAIM_REVIEW_DIR/report.json"
```

Claim evaluation and chunking evaluation share scoring: pass 10, minor 8, major 2, critical 0.
`weighted_score = 100 × awarded points / (reviewed × 10)`.

The [policy](../config/review-policy.json) requires **each applicable
dimension** to have full review coverage, pass rate ≥80%, score ≥90 and zero
critical failures. The combined score cannot compensate for a failed dimension:
combined 92 with generation 86 still fails. Set thresholds before review.

## Reports and reproducibility

Snapshots pin manifests, source hashes, taxonomy, samples and the selected policy
subject. Only that subject's changes invalidate review; legacy standalone policies
remain supported. Record timezone-qualified review times and measured usage/cost
separately from generation; unknowns stay `null`. Identical inputs/judgments give
identical samples, snapshots and scores.

- `automatic_gate`: `pass` or `fail` for artifact checks.
- `review_gate`: `pending`, `pass` or `fail` from dimension results. Known critical
  findings block passing even while other samples remain pending.
- `gate`: `pending_review`, `pass` or `fail`; both gates must pass for acceptance.

Initial pending review returns exit `0`. With `--review-file`, exit `0` requires
acceptance. Exit `1` means failure or pending supplied review; `2` means invalid
input/review. Failed-gate reports are still written.

Keep run-specific findings in the review/report. A policy pass may retain minor
or limited major failures; it is not a whole-corpus accuracy estimate or legal
verification. `retrieved_on` is a capture date, not proof of legal effectiveness.

Build/generation reports include cluster sizes, singletons, refinement splits and
cross-document counts. These and citation coverage do not establish completeness;
inspect missed relationships and duplicate claims against sources.
