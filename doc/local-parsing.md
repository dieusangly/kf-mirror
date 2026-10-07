# Local parsing

Parse [ingested files](local-ingestion.md) into [schema v1.0.0](artifact-schemas.md) source, document, and element records. Later-stage arrays remain empty.

## Setup

With Python 3.11+ in a virtual environment:

```sh
pip install -r requirements.txt
```

On Ubuntu or Debian, install Tesseract and French/English data:

```sh
sudo apt update
sudo apt install tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng
tesseract --list-langs  # should include fra and eng
```

For other systems, see [Tesseract installation](https://tesseract-ocr.github.io/tessdoc/Installation.html). Docling may download PDF model weights on first use; `parsing.pdf.artifacts_path` can point to local weights.

## Run

```sh
python scripts/parse.py --config config/pipeline.json
python scripts/parse.py --config config/pipeline.json --output-dir /tmp/kf-parse --source A --source L1
python scripts/evaluate_parsing.py --input-dir artifacts/parsed --output artifacts/parsed/quality-report.json
```

`--config` is required; `--output-dir` defaults to `artifacts/parsed`. Repeat `--source` for selected manifest IDs or omit it for all. Per-document progress and elapsed parsing time go to stderr; the JSON report goes to stdout. The parser continues after a document fails. Exit codes: `0` all parsed; `1` any failed or partial; `2` invalid configuration or manifest. Verification failures have no parsing time; missing OCR dependencies fail affected PDFs only.

## Configuration

Set `parsing` in [config/pipeline.json](../config/pipeline.json); existing `root`, `manifest`, and `sources` retain their ingestion meanings. Defaults are:

- **PDF:** Docling backend `docling_parse`, table recognition on, `accurate` table mode, no local `artifacts_path`.
- **OCR:** Tesseract `mode: auto` (`off` and `force` also supported), `languages: ["fra", "eng"]`, `scale: 3.0`, `psm: 3`, `tesseract_cmd: "tesseract"`, `tessdata_path: null`. `auto` OCRs PDF raster regions; `force` OCRs full pages.
- **HTML:** `content_selector: "main"`. A source can override `content_selector` and `article_id`; the selector must match exactly one element.
- **Markdown / DOCX:** Pipe tables enabled; DOCX headers and footers excluded.

## Read the output

`report.json` holds the latest run; `report-YYYYMMDD-HH-MM-SS-<id>.json` (UTC) preserves each run, including same-second runs. Reports show settings, outcomes, counts, times, and peak memory.

Parsed files live in `documents/<document-hash>/`. The folder name is the document ID without `doc:`: `doc:` + SHA-256 of `source_id + "\n" + content_sha256`, where `content_sha256` hashes saved bytes. OCR settings do not affect this ID.

| File | How to read it |
| --- | --- |
| `artifact-bundle.json` | The schema-valid source, document, and reading-ordered `elements`. Each element has text, kind, stable ID, and a location (PDF page and optional box, HTML selector, Markdown lines, or DOCX block). Later-stage arrays are empty. |
| `parse.json` | Parser versions/settings, metadata origins, warnings, and extra structure. Join `elements[].element_id` to the bundle's `elements[].id` for parent links, heading context, and table cells or spans. |
| `docling.json` (PDF only) | Docling's native pages, layout, and tables for inspection. This is parser-specific detail, not the normalized artifact contract. |

Re-parsing replaces that document's folder; historical reports remain.

## Limits

Partial PDFs carry warnings for missing page provenance or tables spanning pages. DOCX tracked changes and page numbers are unsupported. Figures have locations but no content interpretation. Docling may not expose element-level OCR origin. The pilot lacks a real scan; see [quality measurements](pilot-parsing-quality.md).
