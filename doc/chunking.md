# Chunking

Select saved bundles/`parse.json` sidecars and a fresh chunking directory:

```sh
INPUT_RUN_ID="your-parsing-run-id"
RUN_ID="your-chunking-run-id"
PARSED_DIR="artifacts/${INPUT_RUN_ID}/parsing"
CHUNKED_DIR="artifacts/${RUN_ID}/chunking"

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

Collection outputs:

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

Changed bytes create a new folder. After successful parsing, move the recorded old
folder outside active inputs:

```sh
PARSED_HISTORY_DIR="${PARSED_DIR}-history"
mkdir -p "$PARSED_HISTORY_DIR/documents"
mv "$PARSED_DIR/documents/OLD_DOCUMENT_HASH" "$PARSED_HISTORY_DIR/documents/"
```

Replace `OLD_DOCUMENT_HASH` with the recorded name. Keep one folder/source;
unchanged bytes overwrite the same folder and need no move.

Archive old outputs, rerun chunking, then [review](chunking-evaluation.md).
Chunking rewrites all configured documents/views without a subset/cache option.
Unchanged documents retain chunk IDs; changed documents change the build ID.
Reference fixtures must still match the source editions.

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

The [structural build](structural-chunks-and-semantic-clusters.md) reads the bundle
and `document-structure.json`. Its `document-hierarchy.json` stores `element_id`,
nullable `parent_element_id`, `role` and `basis`. Parents precede children within
each document; canonical source order stays unchanged. Roles distinguish sections,
numbered paragraphs, list introductions, boxes, content and detached notes/furniture.

Docling supplies labels, groups and page geometry. The adapter derives heading
paths/parents; assembly derives numbered-paragraph/list scope without semantic
similarity. Geometry can restore topic/case hierarchy, supported cross-page list
continuations and independent box context. Contradictory spanless headings are
excluded from derived evidence. These heuristics require source review.

Example: an introduction's numbered paragraph can govern separate banking and
insurance leads, each with its own list across pages. The next independent
paragraph ends that scope; footnotes remain detached. Hierarchy spans focused
chunks without merging a whole section.

Each structural chunk keeps unique original-chunk membership, exact evidence
memberships, and `context_element_ids` for source ancestors and direct note hints.
Generation renders it as non-citable C labels; summaries also receive context
separately. Neither invents spans or expands original memberships.

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
