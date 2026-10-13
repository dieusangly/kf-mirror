# Local parsing

Parse files selected by [`manifest_ids` in `config/pipeline.json`](../config/pipeline.json) into [schema v1.0.0](artifact-schemas.md) records. Later-stage arrays remain empty.

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

For other systems, see [Tesseract installation](https://tesseract-ocr.github.io/tessdoc/Installation.html). Docling may download model weights on first use; `parsing.pdf.artifacts_path` can point to local weights.

## Run

```sh
python scripts/parse.py --config config/pipeline.json
python scripts/parse.py --config config/pipeline.json --output-dir /tmp/kf-parse --source A --source L1
python scripts/evaluate_parsing.py --input-dir artifacts/parsed --output artifacts/parsed/parsing-evaluation-report.json
```

`--config` is required; `--output-dir` defaults to `artifacts/parsed`. `manifest_ids` selects and orders sources; repeat `--source` for a subset. Progress and elapsed time go to stderr, the JSON report to stdout. Parsing continues after a document fails. Exit codes: `0` all parsed; `1` any failed or partial; `2` invalid configuration or manifest. Missing OCR dependencies fail affected PDFs only.

For subset reruns in another directory, `--additional-input-dir` lets the [parsing evaluator](pilot-parsing-evaluation.md) select the newest valid outcome per source.

## Configuration

Set `parsing` in [config/pipeline.json](../config/pipeline.json). Defaults are:

- **PDF:** Docling `docling_parse`, table recognition on, `accurate` mode, no local `artifacts_path`.
- **OCR:** Tesseract `mode: auto`, `languages: ["fra", "eng"]`, `scale: 3.0`, `psm: 3`, `tesseract_cmd: "tesseract"`, `tessdata_path: null`. `auto` OCRs raster regions; `force` OCRs full pages; `off` disables OCR.
- **HTML:** `content_selector: "main"`; the pilot overrides this for Légifrance. Per-source overrides may set `content_selector` or `article_id`. A selector must match exactly one element.
- **Markdown / DOCX:** Pipe tables enabled; DOCX headers and footers excluded.

## Read the output

`report.json` is the latest report; UTC `report-YYYYMMDD-HH-MM-SS-<id>.json` files retain earlier runs. Reports include settings, outcomes, counts, times, and peak memory.

`parsed` means canonical evidence was located, though documented graphical/OCR or furniture warnings may remain. `partial` means completeness is uncertain, for example unresolved document-flow text or a missing figure asset. Both retain warnings. Picture text assembled from Docling fragments retains ordered refs and provenance in `parse.json` (`docling_refs`, `source_parts`).

Parsed files live in `documents/<document-hash>/`. The document ID is `doc:` + SHA-256 of `source_id + "\n" + content_sha256` (saved-byte hash); the folder omits `doc:`. OCR settings do not affect it.

| File | How to read it |
| --- | --- |
| `artifact-bundle.json` | Schema-valid source, document, and ordered elements with stable IDs and native locations. |
| `parse.json` | Settings, warnings, provenance, evidence roles, parents, and heading/table detail; join on `element_id`. |
| `docling.json` (PDF only) | Native PDF pages, layout, and tables for inspection. |
| `figures/*.png` (PDF only) | Source-PDF figure crops linked and hashed in `parse.json`. |

### Evidence and structure contract

`parse.json` assigns every element an `evidence_role`. Chunking uses only `authoritative` text. A table is authoritative as a whole; its cells are `structural`. A PDF `figure` is a structural text container with an authoritative **visual** PNG crop. Its sidecar entry records the relative asset path, media type, SHA-256, role, render scale, source page, and box. Diagram labels are `auxiliary`, not independent text claims or evidence of arrows and flow. Coherent prose nested under a Docling picture stays `authoritative` with its parent ref retained.

Multi-page PDF text is split by Docling `charspan` and page provenance. Heading levels use reliable numbering and index indentation. Warnings locate excluded graphics, corrupt OCR, furniture, uncertain labels, and unresolved document-flow text.

PDF cleanup removes control characters and joins layout-hyphenated words. Tables retain cell positions and may repair narrow merged headers. Repeated running page headers, sparse furniture, figure labels, and filename-only footnotes are not authoritative prose. Check these layout-dependent decisions against the saved PDF when meaning matters.

Re-parsing unchanged bytes replaces the same document folder; changed bytes create a new ID and leave the old folder. See [refresh one document](local-chunking.md#refresh-one-document) before chunking a revised edition.

## Limits

`partial` PDFs may have missing page provenance or figure crops, unresolved text, or multi-page tables. DOCX tracked changes and page numbers are unsupported. Figures preserve layout, not graphical relationships; Docling may not expose element-level OCR origin. The pilot lacks a real scan; see [parsing evaluation](pilot-parsing-evaluation.md).
