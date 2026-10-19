# Chunking

From the repository root, select saved bundles/`parse.json` sidecars in a parsing
run’s `documents/` directory and a new chunking run. Inputs remain unchanged:

```sh
INPUT_RUN_ID="your-parsing-run-id"
RUN_ID="your-chunking-run-id"
PARSED_DIR="artifacts/parsed-${INPUT_RUN_ID}"
CHUNKED_DIR="artifacts/chunked-${RUN_ID}"

.venv/bin/python scripts/chunk.py \
  --config config/pipeline.json \
  --input-dir "$PARSED_DIR" \
  --output-dir "$CHUNKED_DIR"
```

Defaults: `--input-dir artifacts/parsed`, `--output-dir artifacts/chunked`.
`manifest_ids` resolves short IDs such as `A` through `data/manifest.json`. Supply
exactly one parsed document/selected source, no extras; folders use document IDs
without `doc:`. Missing sidecars, invalid schemas/links or missing/mismatched
figure assets stop the build.

## Output

The command writes three deterministic collection files:

- `artifact-bundle.json`: source, document, element, chunk, and build records. Claim/topic arrays remain empty.
- `figure-links.json`: source locations and verified figure assets; no diagram relationships are inferred.
- `report.json`: build ID, settings, and per-document counts.

`documents/<document-id-without-doc-prefix>/document-view.json` holds each
document’s records, chunks, figure links and metrics, sharing bundle chunk/build
IDs. The bundle is authoritative; reruns remove stale views.

## Refresh one document

Record the old parsed folder before changing source bytes. Update hash, size and
edition in `data/manifest.json`, then parse only that selector:

```sh
.venv/bin/python scripts/parse.py \
  --config config/pipeline.json --output-dir "$PARSED_DIR" --source A
```

Only `A` is reparsed. Changed bytes create a **new** folder; after success, move
the old folder outside the active inputs:

```sh
PARSED_HISTORY_DIR="${PARSED_DIR}-history"
mkdir -p "$PARSED_HISTORY_DIR/documents"
mv "$PARSED_DIR/documents/OLD_DOCUMENT_HASH" "$PARSED_HISTORY_DIR/documents/"
```

Replace `OLD_DOCUMENT_HASH` with the recorded name. Keep one folder/source;
unchanged bytes overwrite the same folder and need no move.

Archive the old collection/parsed inputs if needed, rerun chunking above, then
[review/evaluate](chunking-evaluation.md). Chunking rewrites all configured
documents/views, without `--source` or incremental cache. Unchanged documents
retain chunk IDs; changed document IDs change the collection build ID. Use
reference fixtures only while their passages apply.

## Configuration and version IDs

- `chunking.max_tokens` must be at least 32; the default/pilot value is 256. The Unicode word/punctuation counter (`\w+|[^\w\s]`) is reproducible, not a model tokenizer.
- Build version `chunking-v1` and chunker version `structural-v4` contribute to IDs; an output-changing algorithm needs a new version. Schema version is 1.0.0.
- Outputs have no timestamps; identical inputs and settings produce identical bytes.

## Chunking rules

- Fold layout spaces/prose line breaks, remove soft hyphens and preserve table
  rows; no OCR repair or visual interpretation.
- Follow element `ordinal`/heading-path changes; repeat earlier authoritative
  headings/list introductions as cited context when needed. Footnotes stay separate.
- End a local list introduction when a numbered rule resumes at a less-indented
  PDF position. Keep ordinary numbered alternatives within their governing list.
- Keep fitting sentences/list items intact, dropping outer headings before
  splitting a fitting list item. Oversized sentences split at whitespace; long
  lists prefer sentence boundaries.
- Start tables in new chunks; prefer rows and repeat the first header when split.
  Oversized rows may split. Whole tables are evidence; cells are structural.
- Deduplicate only matching kind, cleaned text and native location; retain
  identical wording at distinct locations.
- Use only `authoritative` text, including picture-contained prose; exclude
  auxiliary diagram labels/structural figure text. Linked images preserve visuals
  without asserting relationships.
- Link evidence to source, document, element revision and split span; native
  locators live on elements.

Chunking does not resolve parser OCR/layout warnings. Heading context appears as cited text; visual links live in `figure-links.json` because the chunk schema has no dedicated fields for them.

## Validate and evaluate

The chunk command validates schema v1.0.0 and links before writing. To check the saved bundle independently:

```sh
.venv/bin/python scripts/validate_artifact_examples.py "$CHUNKED_DIR/artifact-bundle.json"
```

Validation also checks manifest/source hashes and bundle metadata without
rechunking. Unit tests serve development/CI.

Then run [chunking evaluation](chunking-evaluation.md) for coverage, boundaries,
links and source review.
