# Ingestion

From the repository root, set up Python 3.11+ and inventory saved sources under
a chosen run ID:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
RUN_ID="your-ingestion-run-id"
INGESTION_DIR="artifacts/ingestion-${RUN_ID}"
mkdir -p "$INGESTION_DIR"
python scripts/ingest.py --config config/pipeline.json --output "$INGESTION_DIR/ingestion-report.json"
```

Omit `--output` for stdout. Report every source, including failures. Exits: `0`
all ingested, `1` any failure, `2` invalid config/manifest. Run from the checkout
without package installation; dependencies include validation’s `jsonschema`.

## Configuration and results

In [config/pipeline.json](../config/pipeline.json), `root` is relative to the
configuration file; `manifest_file` and source `local_path` values are relative
to `root`. `manifest_ids` selects/orders short source IDs, such as `["A", "T"]`.

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

This inventory is not an artifact bundle. Continue with [parsing](parsing.md).
For development, run the tests and artifact validator:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/validate_artifact_examples.py
```
