# Local ingestion

With Python 3.11+, run the pilot inventory:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/ingest.py --config config/pipeline.json --output ingestion-report.json
```

Without `--output`, the JSON report goes to stdout. Exit codes: `0` all ingested; `1` at least one source failed; `2` invalid configuration or manifest. Reports include every manifest entry, even after failures.

In [config/pipeline.json](../config/pipeline.json), `root` is relative to the configuration file; `manifest` and its `local_path` values are relative to `root`. The `sources` map gives each manifest label a stable artifact source ID, title, and class. Keep IDs across URL or edition changes; update the source version in the manifest. For another collection, use a matching manifest and configuration.

The CLI detects PDF and HTML by signature, DOCX by ZIP members, and UTF-8 Markdown by extension. It verifies byte size, SHA-256, and media type against the manifest. Successful outcomes contain v1.0.0 `source` and `document` records; failed outcomes contain an error code and no document. This inventory is not an artifact bundle. Parsing and later stages are separate.

The script runs from this checkout without installing the package. `requirements.txt` includes `jsonschema` for validation.

Run the focused tests and existing artifact-example validator:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/validate_artifact_examples.py
```
