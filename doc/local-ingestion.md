# Local ingestion

Create and activate a Python 3.11+ virtual environment, install the requirements, and run the configured pilot inventory:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/ingest.py --config config/ingestion.json --output ingestion-report.json
```

Without `--output`, the script writes the JSON report to stdout. It returns exit code 0 when all entries are ingested, 1 when any source fails, and 2 for a setup error such as an unreadable configuration or manifest. A report with failures still includes every manifest entry.

The configuration's `root` is relative to the configuration file. `manifest` and each manifest `local_path` are relative to that root. The `sources` map assigns stable artifact source IDs, titles, and classes to the manifest's short pilot labels. Keep a source ID stable across publisher URL or edition changes, and update its version in the manifest. The supplied configuration covers the five saved pilot files. To inventory another collection, provide a manifest with the same fields and a matching configuration; do not edit the saved pilot inputs.

The CLI detects PDF and HTML from file signatures, DOCX from its ZIP members, and UTF-8 Markdown from its `.md` or `.markdown` extension. It hashes the saved bytes with SHA-256 and checks size, hash, and declared media type against the manifest. Each successful outcome carries `source` and `document` records using the v1.0.0 field layout and document ID rule. A failed outcome has an error code and no document record. The report is an ingestion stage inventory, not a v1.0.0 artifact bundle or the export format planned for Task 6. File contents are not parsed, chunked, or compiled into claims and topics.

The script runs directly from this checkout, so the local package does not need to be installed. The requirements file includes `jsonschema` for the focused tests and existing artifact-example validator. Pip's requirements flag is `-r`, not `-m`.

Run the focused tests and existing artifact-example validator:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/validate_artifact_examples.py
```
