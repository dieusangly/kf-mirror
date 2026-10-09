# Local ingestion

With Python 3.11+, run the pilot inventory:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
mkdir -p artifacts
python scripts/ingest.py --config config/pipeline.json --output artifacts/ingestion-report.json
```

Without `--output`, the JSON report goes to stdout. Exit codes: `0` all ingested; `1` at least one source failed; `2` invalid configuration or manifest. Reports include every configured source, even after failures.

In [config/pipeline.json](../config/pipeline.json), `root` is relative to the configuration file. The `manifest_file` path and the manifest's `local_path` values are relative to `root`. `manifest_ids` is an ordered list of `manifest_id` values to ingest, for example `["A", "T"]`. Each [manifest entry](../data/manifest.json) holds its short `manifest_id`, stable `source_id`, title, class, publisher version, and saved-file metadata. Keep `source_id` stable across URL or edition changes; update `source_version` for a new edition. For another collection, provide a matching manifest and configuration.

The CLI detects PDF and HTML by signature, DOCX by ZIP members, and UTF-8 Markdown by extension. It verifies byte size, SHA-256, and media type against the manifest. The outcome's `content_sha256` is calculated from the saved file; a successful `document.content_sha256` repeats that verified value in the document record. When a file was read and its calculated hash differs from the manifest, the failure's `error.expected_sha256` contains the manifest value, including when an earlier size check reports `size_mismatch`. Matching hashes do not add this field. Successful outcomes contain v1.0.0 `source` and `document` records; failed outcomes contain an error code and no document. This inventory is not an artifact bundle. Parsing and later stages are separate.

The script runs from this checkout without installing the package. `requirements.txt` includes `jsonschema` for validation.

Run the focused tests and existing artifact-example validator:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/validate_artifact_examples.py
```
