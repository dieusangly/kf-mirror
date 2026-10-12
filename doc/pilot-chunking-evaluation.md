# Pilot chunking evaluation

This guide describes the chunk-quality gate for the pilot corpus. The evaluator compares a saved chunk bundle with the parsed bundles and sidecars from the parsing step. It reads the existing chunk and parsing outputs without changing them and can also evaluate compatible new documents.

## Run it

For the five-document pilot, include the source-checked reference passages:

```sh
.venv/bin/python scripts/evaluate_chunking.py \
  --reference tests/fixtures/pilot_parsing_reference.json
```

The default report path is `artifacts/chunked/chunking-evaluation-report.json`. The reference file is a checked-in **evaluation input**, reused by parsing and chunking tests and by this optional pilot check. It contains draft, source-checked passages, not expert-approved legal labels. Generated evaluation reports belong in `artifacts/chunked/`; the reference fixture belongs in `tests/fixtures/` because it is a stable regression input, not a generated result.

For a new source without a reference fixture, omit `--reference`:

```sh
.venv/bin/python scripts/evaluate_chunking.py
```

The automatic checks then work for any compatible saved chunk bundle and parsing output, including newly parsed documents. They do not require pilot IDs or a pilot-specific reference file. The source must still have a matching entry in the supplied manifest; a missing reference means the evaluator cannot check additional known passages or detect text the parser itself omitted.

To evaluate outputs in other locations, pass their paths together:

```sh
.venv/bin/python scripts/evaluate_chunking.py \
  --chunked-dir artifacts/chunked-new \
  --parsed-dir artifacts/parsed-new \
  --manifest data/new-manifest.json \
  --output artifacts/chunked-new/chunking-evaluation-report.json
```

- `--chunked-dir` points to the directory containing `artifact-bundle.json`, `figure-links.json`, `report.json`, and per-document `documents/<document-id-without-doc-prefix>/document-view.json` files. Its default is `artifacts/chunked/`.
- `--parsed-dir` points to the parsing output with `documents/<document-id-without-doc-prefix>/artifact-bundle.json` and `parse.json`, plus any linked figure assets. Its default is `artifacts/parsed/`.
- `--manifest` points to the source inventory whose source IDs, versions, and hashes match the documents. It also supplies each saved source path for review. Its default is `data/manifest.json`. Use the manifest that was used to select and parse those documents.
- `--output` sets the evaluation report filename; by default the report is written to the chunked directory.

## Automatic gate

A pass requires:

- schema and cross-record validity, with matching parsed input and manifest source;
- complete, ordered coverage of authoritative element text through body spans, without auxiliary or structural elements as independent evidence;
- exact reconstruction of chunk text from cited source spans, including heading context;
- token-limit, section, list-item, and table-boundary rules; and
- verified figure asset links and per-document inspection views that exactly match the collection output. If `--reference` is supplied, each source-checked passage must appear in a chunk citing the expected source page.

Findings have codes, counts, and up to five examples. Per-document metrics include short and near-limit chunks, split tables, split oversized list items, and token-size distribution. The corpus section totals the main metrics. The report has no timestamp, so the same inputs produce byte-identical output.

`automatic_gate: pass` means those objective checks passed. It does not certify that every chunk communicates legal meaning clearly. The pilot gate uses eight reference passages from the parsing fixture.

## Source-linked review sample

By default, the evaluator selects **up to 12 chunks per document** for human review (`--sample-per-document N` changes the limit). This is a sample size, not 12 separate checks or 12 copies of every chunk. Selection is deterministic for the same input and settings:

- It first chooses one available chunk for each of these cases: picture-contained flow prose, a table, a split element, a short chunk, a near-limit chunk, a list item, and a footnote.
- It fills remaining places with chunks spread across the document's reading order. A document with fewer than 12 chunks contributes only its available chunks.

Each document's `review_sample` includes full chunk text, selection reasons, source file and URL, source version, and the native location of each cited element. Check each sample against the saved source for:

- a complete, understandable passage and appropriate heading context;
- accurate list, table, and footnote boundaries;
- an exact source location and absence of unsupported diagram interpretation.

The report's `review_template` is a JSON object that can be copied to a separate file:

```sh
.venv/bin/python -c 'import json; r=json.load(open("artifacts/chunked/chunking-evaluation-report.json")); print(json.dumps(r["review_template"], ensure_ascii=False, indent=2))' > artifacts/chunked/chunking-review.json
```

Change each `status` from `pending` to `pass` or `fail`; add a nonempty `notes` value for a failure. Then run:

```sh
.venv/bin/python scripts/evaluate_chunking.py \
  --reference tests/fixtures/pilot_parsing_reference.json \
  --review artifacts/chunked/chunking-review.json --require-review
```

The review file must name the current build ID and sampled chunk IDs. A changed chunk build makes an old review file stale. `review_gate` is `pending`, `pass`, or `fail`; overall `gate` is `pending_review`, `pass`, or `fail`. Exit code `0` means the automatic gate passed with no recorded review failure; `--require-review` also requires all sampled reviews to pass. Exit code `1` means a failed gate, and `2` means invalid input. The report is written for gate failures. The evaluator does not use an LLM or make legal judgments automatically.

For a Codex-assisted source review, use the [chunking reviewer prompt](../src/knowledge_foundry/prompts/agent_prompt_chunking_reviewer) in a new session. It follows the review file and gate workflow above.

Retrieval relevance against questions, ranking metrics, and answer support belong to the downstream [retrieval evaluation step](knowledge-foundry-implementation-plan.md). This gate checks chunk artifacts before claim generation and supplies passages for targeted manual review.
