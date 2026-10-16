# Chunking evaluation

Check saved chunk/parse artifacts, then review sampled chunks against originals.
Evaluation leaves inputs unchanged and makes no legal judgment.

## Build and review sessions

Run `scripts/run_parsing_chunking_auto_eval.sh` in the build session: it parses,
evaluates parsing, chunks, initialises evaluation and prints the run ID.

A human or agent reviews that run in a separate session, fills the review file
and runs final evaluation. Agents use the [reviewer prompt](../src/knowledge_foundry/prompts/agent_prompt_chunking_reviewer).
Do not repeat parsing, chunking or initialisation.

## Initialise evaluation manually

Skip if the build script created the report/review. From the repository root,
select matching saved runs; set directories separately if IDs differ:

```sh
RUN_ID="your-build-run-id"
PARSED_DIR="artifacts/parsed-${RUN_ID}"
CHUNKED_DIR="artifacts/chunked-${RUN_ID}"
MANIFEST_PATH="data/manifest.json"

.venv/bin/python scripts/evaluate_chunking.py \
  --chunked-dir "$CHUNKED_DIR" \
  --parsed-dir "$PARSED_DIR" \
  --manifest "$MANIFEST_PATH" \
  --initialize-review
```

This creates automatic results in `chunking-evaluation-report.json` and a pending
`chunking-review.json` in `CHUNKED_DIR`. A **human or agent** must
[review the sources](#review-saved-sources), fill the review, then
[rerun evaluation](#rerun-chunking-evaluation-with-the-human-or-agent-review)
to finalize the report’s review/overall gates.

Optional arguments apply to both evaluation steps:

| Argument | Use |
| --- | --- |
| `--reference-fixture PATH` | Check source-verified passages. For the pilot, use `tests/fixtures/pilot_parsing_reference.json`; omit for sources without a fixture. Use the same selection in both steps. |
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
check passages/pages; the pilot’s eight passages are draft source-checked locators,
not approved legal labels.

Findings have codes/counts and ≤5 examples; metrics cover sizes, split tables/lists
and corpus totals. Reports are deterministic. Passing does not establish standalone
clarity, legal meaning or coverage of text the parser missed.

## Review saved sources

Sampling selects ≤12 chunks/document: one per available risk, then reading-order
coverage; small documents contribute all chunks. Risks: picture-contained prose,
tables, split elements, short/near-limit chunks, lists and footnotes.

Samples give text, source file/URL/version, locations and selection reasons. Check
fidelity, reading order, standalone meaning, headings, boundaries, duplicates and
whole-chunk support. Inspect layout/figures as needed; never pass uninspected sources.

In `chunking-review.json`:

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

After the human or agent fills `chunking-review.json`, rerun with `--review-file`
to include their judgments. Keep initial inputs, sample size, policy and reference selection:

```sh
.venv/bin/python scripts/evaluate_chunking.py \
  --chunked-dir "$CHUNKED_DIR" \
  --parsed-dir "$PARSED_DIR" \
  --manifest "$MANIFEST_PATH" \
  --review-file "$CHUNKED_DIR/chunking-review.json"
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

Keep run-specific findings in the saved review/report. A policy pass can retain
failed samples and does not certify all chunks or legal correctness. Retrieval
quality belongs to [retrieval evaluation](knowledge-foundry-implementation-plan.md).

The shared policy file keeps independently versioned subject sections. Chunking
selects `policies.chunking`; new review files pin that selected policy's hash.
Changes to claim-review settings do not invalidate chunking reviews. Standalone
legacy policy overrides remain readable.
