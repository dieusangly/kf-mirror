# AML/CFT pipeline: pilot commands and validation

This is the command guide for the 13-document pilot, from ingestion through
downstream processing. Extend it with graph, PostgreSQL and assistant commands
as those stages are validated on the pilot.

Exactly **13 saved/selected files**: 11 PDFs (801 pages), two HTML articles.
`data/manifest.json` owns provenance, hashes, editions and relationships.
IDs: A, T, L1, L2, P, BASEL20, NRA23, R24EN, R24FR, TFR23EN, KYC21, GEL26, PANN24.
P is the 2018 PPE body paired with the separate 2024 PANN24 annex. R24EN/R24FR
are language variants of the same work with distinct source IDs. KYC21 is dated
December 2021; GEL26 is dated March 2026. Development anchors are regression
inputs, not legal approval.

## Setup and artifact layout

Install dependencies with `python -m pip install -r requirements.txt`. Internal
PDF parsing requires Tesseract with French/English data and Docling model weights.

Use `artifacts/YYYYMMDD-provider-corpus-NN/`, for example
`artifacts/20261027-docling-aml-cft-01/`. The date is the execution date in
Europe/Paris; provider/corpus identify the comparison; NN distinguishes runs on
the same day. A new provider or configuration gets a fresh run directory.
Shared original bytes and immutable snapshots stay in `artifacts/ingested/`;
reusable caches stay outside dated runs.

The existing Docling result is `artifacts/20261027-docling-aml-cft-01/`.
It was relocated without rerunning ingestion, parsing or chunking. `run.json`
pins its shared ingestion snapshot and records preservation checks. Native
Docling output, elements, chunks and figure images retain their exact bytes.
Reviews remain pending; relocation does not grant acceptance.

## 1. Ingestion

Only acquire/ingest again for new source bytes or provenance:

```sh
.venv/bin/python scripts/ingest.py --config config/pipeline.json --output-dir artifacts/ingested
```

For the same originals, select the immutable snapshot recorded in the existing
run's `run.json`; do not follow a changing `ingested/report.json` for a retained
comparison.

## 2. Document Structuring

### Parse and structurally chunk

Example using the current pilot snapshot and a fresh output:

```sh
RUN=artifacts/20261027-docling-aml-cft-02
SNAPSHOT=artifacts/ingested/snapshots/6d50d268e3fbdbaa4ecc5f1845d0b1e1352635fb02afda76b7257202b5bba965.json
.venv/bin/python scripts/parse.py --config config/pipeline.json --ingested-dir "$SNAPSHOT" --run-id 20261027-docling-aml-cft-02 --output-dir "$RUN/document-structuring/parsing"
.venv/bin/python scripts/chunk.py --config config/pipeline.json --input-dir "$RUN/document-structuring/parsing" --output-dir "$RUN/document-structuring/chunks"
```

For pilot validation of a shared-store run, create `RUN/run.json` with
`"ingestion_snapshot": "../ingested/snapshots/<snapshot-hash>.json"`.
Self-contained runs with `RUN/ingestion/` also remain supported. For a new day,
use a new date in RUN; unchanged source bytes reuse the same pinned snapshot.

### Review and repair

Initialize pending source reviews for a fresh run:

```sh
.venv/bin/python scripts/auto_review_parsing.py --input-dir "$RUN/document-structuring/parsing" --manifest data/manifest.json --source-root . --required-pages tests/fixtures/parsing-expanded-review-pages.json --initialize-review "$RUN/document-structuring/parsing-review/review.json" --output "$RUN/document-structuring/parsing-review/report.json"
.venv/bin/python scripts/auto_review_chunking.py --chunked-dir "$RUN/document-structuring/chunks" --parsed-dir "$RUN/document-structuring/parsing" --initialize-review "$RUN/document-structuring/chunk-review/review.json" --output "$RUN/document-structuring/chunk-review/report.json"
```

Inspect selected originals/layout; record reviewer identity, judgments and notes.
Reevaluate with `--review-file` replacing `--initialize-review`, retaining the
same options and output path. With `--review-file`, pending reviews return exit 1
even when automatic checks pass. Changed output requires a matching fresh review.

Add `--native-input-dir <prior-parsing-directory>` to `parse.py` only for
compatible Docling adapter repair. Changed OCR/DPI settings require a new
provider invocation. Another provider reads the same original bytes and writes
its own native/normalized output. Rechunking reads saved parsing and needs only
`chunk.py` with a fresh output directory.

### Accept validated structural chunks

Both reviews must pass for the exact output before acceptance:

```sh
.venv/bin/python scripts/accept_structure.py --input-dir "$RUN/document-structuring/chunks" --parsing-review "$RUN/document-structuring/parsing-review/report.json" --chunk-review "$RUN/document-structuring/chunk-review/report.json"
```

## 3. Semantic clustering

Requires `OPENAI_API_KEY` and makes paid calls, as do the generation steps below.

```sh
.venv/bin/python scripts/build_semantic_clusters.py --chunked-dir "$RUN/document-structuring/chunks" --output-dir "$RUN/semantic-clustering"
```

## 4. Claims, topics and summaries

Claims and topic classification form one branch; summaries independently consume
the same semantic clusters.

```sh
.venv/bin/python scripts/generate_claims.py --context-dir "$RUN/semantic-clustering" --output-dir "$RUN/claim-generation"
.venv/bin/python scripts/classify_claims.py --candidates-dir "$RUN/claim-generation" --output-dir "$RUN/claim-classification"
.venv/bin/python scripts/generate_summaries.py --context-dir "$RUN/semantic-clustering" --output-dir "$RUN/summary-generation"
```

## Workflow controller

The controller wraps these same commands: `workflow.py step --run-id <id> --stage
<checkpoint>` for manual execution, or `workflow.py run` with explicit budgets.
Use `--help` for checkpoint names. Repairs from `parse` preserve ingestion;
repairs from `chunk` also preserve parsing. Publication is separate.
The relocated run was built with individual commands and has no controller
`state.json`; continue it with the commands above, or start a fresh managed run.

## Recheck the retained run

These commands need no API key and do not rerun parsing:

```sh
RUN=artifacts/20261027-docling-aml-cft-01
.venv/bin/python scripts/auto_review_parsing.py --input-dir "$RUN/document-structuring/parsing" --source-root . --output "$RUN/document-structuring/parsing-review/automatic-audit.json"
.venv/bin/python scripts/auto_review_chunking.py --chunked-dir "$RUN/document-structuring/chunks" --parsed-dir "$RUN/document-structuring/parsing" --manifest data/manifest.json --output "$RUN/document-structuring/chunk-review/automatic-audit.json"
.venv/bin/python scripts/validate_pilot.py --run-dir "$RUN" --output "$RUN/validation.json"
```

The parsing audit checks native extraction, structure, locations and source
fidelity. The chunk audit checks coverage, boundaries, references and figures.
Automatic passes do not inspect/approve the selected source pages visually.
Combined audit/review reports are in each review directory's `report.json`;
`review.json` contains the pending human/agent judgments.

## Tests

```sh
PYTHONPATH=src:.:tests .venv/bin/python -m pytest tests -q
.venv/bin/python scripts/validate_artifact_examples.py
```

Set `KF_TEST_DATABASE_URL` for PostgreSQL/pgvector tests and `KF_TEST_OCR=1` for
the raster OCR test. Hosted provider exports have contract tests, not a completed
comparison on all 13 originals.
