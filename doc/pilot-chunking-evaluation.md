# Pilot chunking evaluation

The chunk-quality evaluator reads saved chunk and parse artifacts without changing them. It supports the pilot corpus and compatible new documents.

## Two-stage workflow

1. In a build session, run:

   ```sh
   scripts/run_parsing_chunking_auto_eval.sh
   ```

   It runs parsing, parsing evaluation, chunking, and initial automatic chunking evaluation, then reports a run ID and creates the pending review file.
2. In a separate review session, use the [reviewer prompt](../src/knowledge_foundry/prompts/agent_prompt_chunking_reviewer) with that run ID. The reviewer verifies the selected chunks against saved originals, updates only the review file, and runs the final policy evaluation. It must not run parsing, chunking, or the build script.

## Run it

For the pilot, include its source-checked passages:

```sh
.venv/bin/python scripts/evaluate_chunking.py \
  --reference-fixture tests/fixtures/pilot_parsing_reference.json \
  --initialize-review
```

The default report is `artifacts/chunked/chunking-evaluation-report.json`. The fixture is a checked-in evaluation input with draft source-checked passages, not expert-approved legal labels; generated reports stay under `artifacts/`.

For a new source without a reference fixture, omit `--reference-fixture`:

```sh
.venv/bin/python scripts/evaluate_chunking.py --initialize-review
```

Automatic checks need no pilot IDs but do require matching manifest, parse, and chunk artifacts. Without a source-checked reference, they cannot detect text the parser itself omitted.

For other output locations:

```sh
.venv/bin/python scripts/evaluate_chunking.py \
  --chunked-dir artifacts/chunked-new \
  --parsed-dir artifacts/parsed-new \
  --manifest data/new-manifest.json \
  --output artifacts/chunked-new/chunking-evaluation-report.json
```

- `--chunked-dir` contains the collection bundle, figure links, build report, and per-document views; default: `artifacts/chunked/`.
- `--parsed-dir` contains per-document bundles, `parse.json`, and figure assets; default: `artifacts/parsed/`.
- `--manifest` supplies matching source IDs, versions, hashes, and saved paths; default: `data/manifest.json`.
- `--output` names the report; default: `chunking-evaluation-report.json` inside the chunked directory.

## Automatic gate

A pass requires:

- valid schema/links and matching parse and manifest sources;
- ordered coverage of authoritative text, with no auxiliary or structural text as independent evidence;
- exact reconstruction from cited spans and heading context;
- token, section, list, and table boundaries; and
- valid figure links and views matching the collection. With `--reference-fixture`, each passage must appear with its expected source page.

Findings have codes, counts, and up to five examples. Metrics cover chunk sizes, split tables/lists, and corpus totals. The report has no timestamp and is deterministic for the same inputs.

`automatic_gate: pass` covers these checks, not legal meaning or standalone clarity. The pilot fixture adds eight passages.

## Source-linked review sample

The deterministic sample selects **up to 12 chunks per document** by default. Set `evaluation.chunking.sample_per_document` in [`config/pipeline.json`](../config/pipeline.json) or override it with `--sample-per-document N`:

- First, one available chunk for each risk type: picture-contained prose, table, split element, short chunk, near-limit chunk, list item, and footnote.
- Then, chunks spread through reading order. A smaller document contributes all its chunks.

Each `review_sample` includes full text, selection reasons, source file/URL/version, and cited native locations. Compare it with the saved source for:

- fidelity, reading order, standalone meaning, and heading context;
- sentence, list, table, and footnote boundaries;
- source support, exact location, and absence of unsupported visual claims.

The initial evaluation creates `chunking-review.json` when run with `--initialize-review`; without a path, it writes it in the chunked directory. `--initialize-review PATH` selects another path. It refuses to overwrite an existing review file.

Set each status in that file to `pass`, `fail`, or `pending` after source review. Pending and failed samples need source-located `notes`; failures also need `severity`: `critical` for materially wrong text/citation/table association, `major` for misleading missing context, or `minor` for limited context/serialization defects without a meaning change. When a reference fixture applies, pass `--reference-fixture` to both evaluator runs: it supplies the automatic reference checks in each run, while `--review-file` separately adds the human-review policy gate. Then run:

```sh
.venv/bin/python scripts/evaluate_chunking.py \
  --reference-fixture tests/fixtures/pilot_parsing_reference.json \
  --review-file artifacts/chunked/chunking-review.json
```

The file must preserve the current build ID and every sampled chunk ID; a new build makes it stale. The [review policy](../config/chunking-review-policy.json) (or `--review-policy`) requires **100% coverage**, pass rate ≥80%, weighted score ≥90, and zero critical failures. `review.to_review` counts samples; `review.reviewed` counts `pass` or `fail` statuses.

Configured points are pass 10, minor 8, major 2, critical 0. `weighted_score = 100 × awarded points / (reviewed × pass points)`; points and the minimum are configurable. A critical failure fails regardless of score. `review_gate` is `pending`, `pass`, or `fail`; overall `gate` is `pending_review`, `pass`, or `fail`. Without `--review-file`, a pending review can return exit `0`; with it, exit `0` requires both gates to pass. Exit `1` means a failed gate or pending supplied review, and `2` invalid input. Failed-gate reports are still written. The evaluator makes no automatic legal judgment.

For Codex-assisted review, use the [reviewer prompt](../src/knowledge_foundry/prompts/agent_prompt_chunking_reviewer) in a new session.

Consult the saved review file and evaluation report for run-specific findings; a policy pass does not resolve failed samples.

Retrieval relevance, ranking, and answer support belong to [retrieval evaluation](knowledge-foundry-implementation-plan.md), not this gate.
