# Local chunking

Chunking reads saved bundles and `parse.json` sidecars from `artifacts/parsed/documents/`; it does not reparse or alter them. With the project virtual environment installed, run:

```sh
.venv/bin/python scripts/chunk.py --config config/pipeline.json
```

`--input-dir` defaults to `artifacts/parsed`; `--output-dir` to `artifacts/chunked`. `manifest_ids` contains short selectors such as `A`; the chunker resolves them through `data/manifest.json` and requires exactly one parsed document per selected source, with no extras. Folders use the full document ID without `doc:`, not the selector. Missing sidecars, invalid links/schema, or missing/mismatched figure assets stop the build.

## Output

The command writes three deterministic collection files:

- `artifact-bundle.json`: source, document, element, chunk, and build records. Claim/topic arrays remain empty.
- `figure-links.json`: source locations and verified figure assets; no diagram relationships are inferred.
- `report.json`: build ID, settings, and per-document counts.

Each document also gets `documents/<document-id-without-doc-prefix>/document-view.json`, a derived inspection view with its records, chunks, figure links, and metrics. The collection bundle remains the schema-valid source of truth; views share its chunk and build IDs. Reruns remove stale views.

## Refresh one document

Before changing a saved file, record its parsed folder name. Update its `data/manifest.json` hash, size, and edition metadata, then parse it by short selector; for example:

```sh
.venv/bin/python scripts/parse.py --config config/pipeline.json --source A
```

The parser writes a new `A` bundle and sidecar without re-parsing other sources.

Changed bytes create a **new** folder. After a successful parse, move the old folder out of the active input directory:

```sh
mkdir -p artifacts/parsed-history/documents
mv artifacts/parsed/documents/OLD_DOCUMENT_HASH artifacts/parsed-history/documents/
```

Replace `OLD_DOCUMENT_HASH` with the recorded name. Keep exactly one folder per configured source; unchanged bytes replace the same folder, so no move is needed.

Then rebuild and evaluate the chunk collection:

```sh
.venv/bin/python scripts/chunk.py --config config/pipeline.json
.venv/bin/python scripts/evaluate_chunking.py
```

Chunking has no `--source` option or incremental cache: it rewrites the collection and views for all configured documents. Unchanged documents retain deterministic chunk IDs; an input document ID change changes the collection build ID. Add `--reference tests/fixtures/pilot_parsing_reference.json` only if its passages still apply. Archive the old collection with its parsed inputs before rebuilding if you need it; collection outputs are rewritten.

## Configuration and version IDs

- `chunking.max_tokens` must be at least 32; the default/pilot value is 256. The Unicode word/punctuation counter (`\w+|[^\w\s]`) is reproducible, not a model tokenizer.
- Build version `chunking-v1` and chunker version `structural-v2` contribute to IDs; an output-changing algorithm needs a new version. Schema version is 1.0.0.
- Outputs have no timestamps; identical inputs and settings produce identical bytes.

## Chunking rules

- Cleanup folds layout spaces and prose line breaks, removes soft hyphens, and preserves table rows; it does not repair OCR or interpret visuals.
- Chunks follow element `ordinal` and heading-path changes. Earlier authoritative headings and list introductions may repeat as cited context; footnotes stay separate.
- Fitting sentences and list items remain intact when possible. Outer heading context is dropped before splitting a fitting list item; oversized sentences split at whitespace and long lists prefer sentence boundaries.
- Tables start new chunks, prefer row boundaries, and repeat the first header row when split. The table aggregate is evidence; cells are structural. Oversized rows may split.
- Exact duplicates match kind, cleaned text, and native location. Repeated wording at distinct locations remains evidence.
- Only `authoritative` elements supply text evidence. Auxiliary diagram labels and structural figure text are excluded; authoritative prose nested under a picture remains. Linked images preserve visual information without asserting relationships.
- Evidence links each chunk to source, saved document, element revision, and split span; the element carries the native locator.

Chunking does not resolve parser OCR/layout warnings. Heading context appears as cited text; visual links live in `figure-links.json` because the chunk schema has no dedicated fields for them.

## Validate and evaluate

The chunk command validates schema v1.0.0 and links before writing. To check the saved bundle independently:

```sh
.venv/bin/python scripts/validate_artifact_examples.py artifacts/chunked/artifact-bundle.json
```

The validator also checks manifest and saved-source hashes and matching bundle metadata; it does not rechunk. Unit tests are for development and CI.

Then run [chunking evaluation](pilot-chunking-evaluation.md) for coverage, boundaries, links, and source-linked review samples.
