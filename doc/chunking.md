# Chunking

From the repository root, select saved bundles/`parse.json` sidecars in a parsing
run’s `documents/` directory and a new chunking run. Inputs remain unchanged:

```sh
INPUT_RUN_ID="your-parsing-run-id"
RUN_ID="your-chunking-run-id"
PARSED_DIR="artifacts/parsed-${INPUT_RUN_ID}"
CHUNKED_DIR="artifacts/chunked-${RUN_ID}"

direnv exec . .venv/bin/python scripts/chunk.py \
  --config config/pipeline.json \
  --input-dir "$PARSED_DIR" \
  --output-dir "$CHUNKED_DIR"
```

Defaults: `--input-dir artifacts/parsed`, `--output-dir artifacts/chunked`.
`manifest_ids` resolves short source selectors through `data/manifest.json`. Supply
exactly one parsed document/selected source, no extras; folders use document IDs
without `doc:`. Missing sidecars, invalid schemas/links or missing/mismatched
figure assets stop the build.

## Output

The command writes four deterministic collection files:

- `artifact-bundle.json`: source, document, element, chunk, and build records. Claim/topic arrays remain empty.
- `figure-links.json`: source locations and verified figure assets; no diagram relationships are inferred.
- `report.json`: build ID, settings, and per-document counts.
- `document-structure.json`: a versioned snapshot of parser parents, heading
  levels/paths, evidence roles and native Docling references, keyed by element ID.
  It is separate from the immutable canonical bundle and does not alter chunk IDs.

`documents/<document-id-without-doc-prefix>/document-view.json` holds each
document’s records, chunks, figure links and metrics, sharing bundle chunk/build
IDs. The bundle is authoritative; reruns remove stale views.

## Refresh one document

Record the old parsed folder before changing source bytes. Update hash, size and
edition in `data/manifest.json`, then parse only that selector:

```sh
direnv exec . .venv/bin/python scripts/parse.py \
  --config config/pipeline.json --output-dir "$PARSED_DIR" --source MANIFEST_ID
```

Replace `MANIFEST_ID` with the selected source selector; only that source is reparsed. Changed bytes create a **new** folder; after success, move
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

- `chunking.max_tokens` must be at least 32; the default is 256. The Unicode word/punctuation counter (`\w+|[^\w\s]`) is reproducible, not a model tokenizer.
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

## Structural assembly after original chunking

The [structural chunk and semantic cluster build command](structural-chunks-and-semantic-clusters.md)
reads the original bundle and `document-structure.json`. It saves
`document-hierarchy.json`: one record per element with `element_id`,
`parent_element_id` (nullable), `role` and `basis`. Parents precede their children
in the derived hierarchy within the same document; canonical source order stays
unchanged. Roles distinguish sections, numbered paragraphs, local
list introductions, independent boxes, ordinary content and detached notes/furniture. The basis
records whether a relationship came from parser metadata, heading levels, a
numbered paragraph's scope or local list flow.

Docling extracts reading order, text labels (including headings, paragraphs and
list items), native groups and page/bounding-box provenance. Our parsing adapter
derives heading levels/paths from numbering and index indentation and resolves
element parent IDs. Structural assembly then builds numbered-paragraph and local
list scope from these saved signals; Docling does not provide this finished
hierarchy or our structural chunk links. No semantic similarity is used to infer
document parents.

When a PDF topic banner appears above a case heading on the page but late in
parser reading order, assembly uses page geometry to restore the topic/case
relationship. Case subheadings stay within that case. Stale, spanless heading
context is excluded from derived evidence when it contradicts these ancestors;
the original chunks remain unchanged. A markerless continuation across a page
break retains its list introduction when text and column geometry support the
continuation, even when detached footnotes intervene. Captioned boxes establish
their own context under the section instead of inheriting the preceding numbered
paragraph. These repairs are heuristics and require source review.

For the ACPR–Tracfin **INTRODUCTION**, the numbered paragraphs are children of the
introduction. Paragraph 7 remains the parent of the split “Dans le secteur de la
banque :” and “Dans le secteur de l'assurance :” leads. Their respective list items
are children of those leads, even across adjacent pages. The next independent
numbered paragraph ends paragraph 7's scope. Page-bottom footnotes remain detached.
These relationships survive when the paragraph, leads and lists occupy many
structural chunks: they do not require one large introduction/section chunk.

Each structural chunk keeps unique original-chunk membership, exact evidence
memberships, and `context_element_ids` for source ancestors and direct note hints.
Generation renders this extra context with non-citable C labels; it never invents
supporting spans or expands original chunk membership. Summary generation also
receives parent context separately from source evidence.

Only adjacent shared-element fragments and geometrically supported sentence/table
continuations may merge. Merged components are bounded to four original chunks and
12,000 original-text characters; over-limit links remain relationships. List,
parent, repeated-context and unique same-page footnote links never merge chunks.
The `merges` flag in `structural-links.jsonl` distinguishes these cases. Original
chunks are never split, rewritten or reassigned to multiple structural chunks.
An already mixed original chunk cannot be disentangled by this stage.

## Validate and evaluate

The chunk command validates schema v1.0.0 and links before writing. To check the saved bundle independently:

```sh
direnv exec . .venv/bin/python scripts/validate_artifact_examples.py "$CHUNKED_DIR/artifact-bundle.json"
```

Validation also checks manifest/source hashes and bundle metadata without
rechunking. Unit tests serve development/CI.

Then run [chunking evaluation](chunking-evaluation.md) for coverage, boundaries,
links and source review.
