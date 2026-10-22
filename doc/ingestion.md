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

Omit `--output` for stdout. Report every source, including failures. Exits: `0`
all ingested, `1` any failure, `2` invalid config/manifest. Run from the checkout
without package installation; dependencies include validation’s `jsonschema`.

## Configuration and results

In [config/pipeline.json](../config/pipeline.json), `root` is relative to the
configuration file; `manifest_file` and source `local_path` values are relative
to `root`. `manifest_ids` selects/orders the short IDs defined in the manifest.

[Manifest entries](../data/manifest.json) hold short `manifest_id`, stable `source_id`,
title, class, publisher version and file metadata. Retain `source_id` across
URL/edition changes; update `source_version` for new editions. Collections need
matching config/manifests.

The CLI detects PDF/HTML by signature, DOCX by ZIP members and UTF-8 Markdown by
extension. It verifies size, SHA-256 and media type. Successful outcomes contain
v1.0.0 source/document records; failures contain an error and no document.
`content_sha256` is calculated from saved bytes and repeated in successful
document records. A read file with a mismatching hash also reports the manifest
hash as `error.expected_sha256`, even when size mismatch was reported first;
matching hashes omit this field.

Embedding, generation and classification commands also require `OPENAI_API_KEY`
in the environment.

This inventory is not an artifact bundle. Continue with [parsing](parsing.md).
For development, run the tests and artifact validator:

```sh
direnv exec . env PYTHONPATH=src:.:tests .venv/bin/python -m pytest tests -q
direnv exec . .venv/bin/python scripts/validate_artifact_examples.py
```
