# Chunking evaluation

Check saved chunk/parse artifacts, then review sampled chunks against originals.
Evaluation leaves inputs unchanged and makes no legal judgment.

## Build and review sessions

Complete [parsing](parsing.md), [parsing evaluation](parsing-evaluation.md) and
[chunking](chunking.md) first, using their individual commands in the build session.

The [agent prompt](../src/knowledge_foundry/prompts/agent_prompt_chunking_review)
initializes/reuses the review, inspects originals and evaluates judgments without
rebuilding inputs. Invoke it with the completed run ID:

```text
Read AGENTS.md and .agents/AGENTS.md, then follow
src/knowledge_foundry/prompts/agent_prompt_chunking_review
for RUN_ID="your-build-run-id".
Complete initialization/reuse, source review and final evaluation.
Preserve existing judgments and report the final results.
```

Use the manual steps below for human review or explicit CLI control.

## Initialise evaluation manually

Skip if a review already exists; validate and reuse it instead. From the repository
root, select matching saved runs; set directories separately if IDs differ:

```sh
RUN_ID="your-build-run-id"
PARSED_DIR="artifacts/${RUN_ID}/parsing"
CHUNKED_DIR="artifacts/${RUN_ID}/chunking"
CHUNK_REVIEW_DIR="artifacts/${RUN_ID}/chunking-review"
MANIFEST_PATH="data/manifest.json"

direnv exec . .venv/bin/python scripts/auto_review_chunking.py \
  --chunked-dir "$CHUNKED_DIR" \
  --parsed-dir "$PARSED_DIR" \
  --manifest "$MANIFEST_PATH" \
  --initialize-review "$CHUNK_REVIEW_DIR/review.json" \
  --output "$CHUNK_REVIEW_DIR/report.json"
```

Creates `report.json` and pending `review.json` in `CHUNK_REVIEW_DIR`.
Inspect originals, record judgments, then rerun with `--review-file`.

Optional arguments apply to both evaluation steps:

| Argument | Use |
| --- | --- |
| `--reference-fixture PATH` | Check source-verified passages. Use a fixture matching the selected sources and versions; omit when none exists. Use the same selection in both steps. |
| `--sample-per-document N` | Override `evaluation.chunking.sample_per_document` in `config/pipeline.json` (default: 12). |
| `--review-policy PATH` | Override `config/review-policy.json`; selects subject `chunking`. |
| `--output PATH` | Choose another report location. |

`--initialize-review PATH` chooses another review-file location and refuses to
overwrite an existing file. Default input directories are `artifacts/chunked`
and `artifacts/parsed`; the default manifest is `data/manifest.json`. Another
corpus needs matching manifest, parse and chunk artifacts.

## What the evaluator checks automatically

Checks cover schemas/links, matching sources, ordered authoritative-text coverage,
exact spans, token/section/list/table boundaries, figures and document views.
Auxiliary/structural text cannot become independent evidence. Reference fixtures
check source-specific passages/pages; they are not approved domain labels.

Findings have codes/counts and ≤5 examples; metrics cover sizes, split tables/lists
and corpus totals. Reports are deterministic. Passing does not establish standalone
clarity, legal meaning or coverage of text the parser missed.

## Review saved sources

Sampling selects ≤12 chunks/document: one per available risk, then reading-order
coverage; small documents contribute all chunks. Risks: picture-contained prose,
tables, split elements, short/near-limit chunks, lists and footnotes.

Samples include text, source/version, locations and selection reasons. Resolve
`source_file` against the pipeline root. Check fidelity, order, standalone meaning,
headings, boundaries, duplicates and whole-chunk support, including PDF layout.
Uninspected sources stay pending.

In `$CHUNK_REVIEW_DIR/review.json`:

- Preserve the build ID and every sampled chunk ID. A new build requires a new review.
- Set `reviewer.kind` to `human` or `agent`, provide a nonempty `name`, and record
  `model` when known (`null` otherwise). New reviews require kind/name once any
  sample is assessed. Legacy reviews without metadata remain accepted as unknown;
  reports without a supplied review have `reviewer: null`.
- Set each status to `pass`, `fail` or `pending`. Pending/failed items need
  source-located notes. Failures need one worst severity: `critical` for materially
  incorrect text/citations/table associations, `major` for misleading missing
  context, or `minor` for presentation defects without a meaning change.

## Rerun chunking evaluation with the human or agent review

Retain initial inputs, sample size, policy and reference selection:

```sh
direnv exec . .venv/bin/python scripts/auto_review_chunking.py \
  --chunked-dir "$CHUNKED_DIR" \
  --parsed-dir "$PARSED_DIR" \
  --manifest "$MANIFEST_PATH" \
  --review-file "$CHUNK_REVIEW_DIR/review.json" \
  --output "$CHUNK_REVIEW_DIR/report.json"
```

The [policy](../config/review-policy.json) requires 100% review coverage,
pass rate ≥80%, score ≥90 and zero critical failures. Points are pass 10, minor 8,
major 2, critical 0; `weighted_score = 100 × awarded points / (reviewed × pass points)`.
`review.to_review` counts samples; `review.reviewed` counts pass/fail judgments.
Points and thresholds can be configured before review.

`automatic_gate` covers artifact checks; `review_gate` is `pending`, `pass` or
`fail`. Overall `gate` is `pending_review`, `pass` or `fail`; both gates must pass
for acceptance. Initial pending review may exit `0`; with `--review-file`, exit
`0` requires acceptance. Exit `1` means failure or pending supplied review; `2`
means invalid input. Failed-gate reports are still written.

Keep findings in the saved review/report. A pass can retain non-critical failures
and does not certify every chunk or legal correctness. See [retrieval evaluation](retrieval-rag.md#validation-and-troubleshooting).

The shared policy file keeps independently versioned subject sections. Chunking
selects `policies.chunking`; new review files pin that selected policy's hash.
Changes to claim-review settings do not invalidate chunking reviews. Standalone
legacy policy overrides remain readable.
