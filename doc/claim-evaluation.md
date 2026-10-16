# Claim evaluation

Review generated claims and topic classifications against saved originals. The evaluator checks artifacts and
scores judgments without model calls; claims/topics remain drafts.

After [generation/classification](claim-generation-and-classification.md), select a saved run from
the repository root; set directories separately if stage run IDs differ:

```sh
RUN_ID="your-claim-run-id"
CANDIDATES_DIR="artifacts/generated-claims-${RUN_ID}"
CLASSIFIED_DIR="artifacts/classified-claims-${RUN_ID}"
```

For the saved subset, use `RUN_ID=20261016-subset`; the full run uses
`RUN_ID=20261016-full-01`. Structural chunks and semantic clusters are shared from
`artifacts/structural-chunks-and-semantic-clusters-20261016`.

## One agent-review task

Use the [reviewer prompt](../src/knowledge_foundry/prompts/agent_prompt_claim_reviewer)
after classification. The agent initializes evaluation if needed, reviews saved
sources, and runs final evaluation. Existing review files are validated and reused
without resetting judgments. Stale snapshots or failed automatic checks stop the
review. A pending review is expected before source inspection.

To invoke the reviewer by path without copying the full prompt, send the agent:

```text
Read AGENTS.md and .agents/AGENTS.md, then follow
src/knowledge_foundry/prompts/agent_prompt_claim_reviewer
for RUN_ID="your-run-id".

Complete initialization/reuse, source review, and final evaluation.
Preserve existing review judgments and report the final results.
```

## Human-review setup

Run once before handing the review file to a human:

```sh
.venv/bin/python scripts/evaluate_claims.py \
  --candidates-dir "$CANDIDATES_DIR" \
  --classified-dir "$CLASSIFIED_DIR" \
  --initialize-review
```

Creates `compilation-evaluation-report.json` and pending `compilation-review.json`
in `CLASSIFIED_DIR`. Source review is still required. Both steps accept:

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

A separate human or agent inspects every sample against originals and fills the
review file; agents use the [reviewer prompt](../src/knowledge_foundry/prompts/agent_prompt_claim_reviewer).
Do not reinitialize existing review files or reuse another build’s judgments.

Automatic checks validate schemas, hashes, IDs, exact spans, unchanged candidate
claims, stage errors, source versions, inverse links, JSONL exports and topic views.
For frozen structural chunks and semantic clusters, they also validate unique
membership, reconstructed structural links, similarity, request assumptions and
candidate-claim lineage. Legacy candidate claim artifacts remain readable.
Evaluation makes no embedding or generation calls.
Missing candidate claims and null classifications need source review; they are not
automatically wrong.

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

Samples give text/evidence, native locations, source paths/versions and selection
reasons. Inspect generation/classification together: they sample the same candidate claims.
Candidate claim IDs differ from claim IDs; resolve them in `CANDIDATES_DIR/claim-candidates.jsonl`.

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

After the human or agent records judgments, rerun with `--review-file` and the
same optional arguments as during initialisation to finalize the report:

```sh
.venv/bin/python scripts/evaluate_claims.py \
  --candidates-dir "$CANDIDATES_DIR" \
  --classified-dir "$CLASSIFIED_DIR" \
  --review-file "$CLASSIFIED_DIR/compilation-review.json"
```

Claim evaluation and chunking evaluation share scoring: pass 10, minor 8, major 2, critical 0.
`weighted_score = 100 × awarded points / (reviewed × 10)`.

The [policy](../config/review-policy.json) requires **each applicable
dimension** to have full review coverage, pass rate ≥80%, score ≥90 and zero
critical failures. The combined score cannot compensate for a failed dimension:
combined 92 with generation 86 still fails. Set thresholds before review.

## Reports and reproducibility

Snapshots pin both manifests, source hashes, taxonomy, the selected subject's
policy and sample. Changing another subject's policy does not invalidate this
review. Changes to the selected policy require fresh review. Each subject has
its own version, thresholds and scoring in the shared `review-policy.json`.
Explicit standalone legacy policy files remain supported. Record timezone-qualified review start/end times and measured
requests/tokens/cost separately from generation/classification; unknowns stay `null`.
Duration derives from timestamps. Identical inputs/judgments give identical
samples, snapshots and scores.

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

Build reports include structural-chunk and semantic-cluster counts, size
distributions, singletons, refinement splits and cross-document clusters.
Generation adds cross-document candidate-claim counts. These diagnostics and
citation coverage do not establish semantic completeness; source review must
check missed relationships and duplicate claims.
