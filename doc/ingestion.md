# Ingestion

Run from the repository root with Python 3.12 and direnv installed. Set up once:

```sh
test -d .venv || python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
test -f .envrc || cp .envrc.example .envrc
```

Set `OPENAI_API_KEY` in `.envrc` for embedding/generation steps, and
`KF_DATABASE_URL` for [PostgreSQL/RAG](retrieval-rag.md). Run `direnv allow` after
editing. Commands use `direnv exec .`, so no shell hook or `source .envrc` is needed.

Save the original files and record their sizes/hashes/editions in the manifest;
ingestion validates local files and does not download them. Inventory a new run:

```sh
direnv allow
RUN_ID="your-ingestion-run-id"
INGESTION_DIR="artifacts/ingestion-${RUN_ID}"
mkdir -p "$INGESTION_DIR"
direnv exec . .venv/bin/python scripts/ingest.py --config config/pipeline.json --output "$INGESTION_DIR/ingestion-report.json"
```

Omit `--output` for stdout. Every source gets an outcome. Exits: `0` all ingested,
`1` any failure, `2` invalid config/manifest. No package installation is needed.

## Configuration and results

In [config/pipeline.json](../config/pipeline.json), `root` is relative to the
configuration file; `manifest_file` and source `local_path` values are relative
to `root`. `manifest_ids` selects/orders the short IDs defined in the manifest.

[Manifest entries](../data/manifest.json) hold IDs, publisher/file metadata and
coverage decisions. Pilot filenames use `<manifest_id>-<filename>`. Renaming leaves
hashes and IDs unchanged; retain `source_id` across URL/edition changes and update
`source_version` for new editions.

Detection uses PDF/HTML signatures, DOCX ZIP members and UTF-8 Markdown extensions.
Size, SHA-256 and media type must match. Success returns v1.0.0 source/document
records with the saved-byte hash; failure returns an error without a document.
Hash mismatches include `error.expected_sha256`, even if size mismatch is reported first.

Continue with [parsing](parsing.md); this inventory is not an artifact bundle.
Development checks:

```sh
direnv exec . env PYTHONPATH=src:.:tests .venv/bin/python -m pytest tests -q
direnv exec . .venv/bin/python scripts/validate_artifact_examples.py
```
