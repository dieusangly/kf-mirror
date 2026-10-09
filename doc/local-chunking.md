# Local chunking

The chunking step consumes saved parsing bundles and `parse.json` sidecars from `artifacts/parsed/documents/`. It does not rerun parsing or change those files. With the project virtual environment and the dependencies in `requirements.txt` installed, run:

```sh
.venv/bin/python scripts/chunk.py --config config/pipeline.json
```

`--input-dir` defaults to `artifacts/parsed`; `--output-dir` defaults to `artifacts/chunked`. In `config/pipeline.json`, `manifest_ids` contains short **manifest selectors** such as `A` and `T`. The chunker resolves each selector through `data/manifest.json` to a source ID, then requires exactly one matching parsed document for each selected source and no extras. Parsed document folders are named by full document ID without the `doc:` prefix, not by the short selector. A missing sidecar, invalid record link, figure asset, or schema contract stops the build.

## Output

The command writes three deterministic files in `artifacts/chunked/`:

- `artifact-bundle.json`: source, document, element, chunk, and one complete build record. Claim and topic arrays and their build outputs are empty; those are downstream steps.
- `figure-links.json`: figure element IDs, revisions, native locations, and verified visual asset links relative to the input directory. This file does not assert diagram relationships.
- `report.json`: build ID, effective settings, and per-document element, duplicate, repetition, and chunk counts.

## Configuration and version IDs

- Set `chunking.max_tokens` in `config/pipeline.json` to an integer of at least 32. The default and pilot value is 256. The counter matches Unicode words and punctuation with `\w+|[^\w\s]`; it is reproducible but is **not** a particular language model's tokenizer. Every chunk stays within this count.
- `chunking-v1` is the first pipeline implementation version recorded in the build; `structural-v1` is the first chunking algorithm version recorded in chunks. Pre-release iterations are part of this initial implementation. The settings and version tags contribute to deterministic build or chunk IDs, so a future output-changing release must use new version tags. The artifact schema version remains 1.0.0.
- Outputs contain no runtime timestamps. The same parsed input and configuration produce byte-identical output.

## Chunking rules

- Cleanup removes soft hyphens, folds layout spaces and prose line breaks, and preserves table row breaks. It does not paraphrase, repair OCR, join broken words, or interpret visuals.
- Chunking follows element `ordinal` order and starts a new group when the sidecar heading path changes. Earlier authoritative headings may be added as cited context, within a quarter of the token budget.
- A sentence that fits a fresh chunk moves there intact when it does not fit the remaining space. Only a sentence larger than a fresh chunk budget splits at whitespace token boundaries. A list item that fits the full limit stays intact: optional outer heading context is dropped before splitting it. Longer list items prefer sentence boundaries.
- Tables start in a fresh chunk, use the authoritative table aggregate, and prefer row boundaries; cells are structural and never become independent evidence. An oversized row may split.

- Exact duplicate detection compares kind and cleaned text at the same native location within a document. Repeated wording at distinct source locations stays as separate evidence, including repetitions in T.
- Only `authoritative` parsing elements supply independent chunk evidence. Auxiliary diagram labels and structural figure text are excluded; coherent prose nested under a picture stays included when marked authoritative. Figure images remain linked for later visual review without creating text claims from them.
- Each chunk cites the source, saved document, element revision, and text span where split. Its element provides the native page, selector, line, or block location. The build record lists all input documents and output chunks.

Parsed PDF sidecars may contain OCR and layout warnings; chunking does not resolve them. The schema has no field for heading paths or figure assets on a chunk, so heading context appears as cited text and visual links live in the companion file.

## Validate and evaluate

The chunk command checks schema v1.0.0 and cross-record links before writing. For an independent check of the saved bundle, run:

```sh
.venv/bin/python scripts/validate_artifact_examples.py artifacts/chunked/artifact-bundle.json
```

Despite its historical name, `validate_artifact_examples.py` accepts a supplied artifact bundle. It also validates the manifest and saved source file hashes and checks that bundle source metadata matches the manifest. This is an optional verification command, not a second chunking step. Unit tests are for development and CI, not part of the normal run command.

Run the [chunking evaluation](pilot-chunking-evaluation.md) after building chunks to check coverage, boundaries, and source links and to prepare source-linked chunks for review.
