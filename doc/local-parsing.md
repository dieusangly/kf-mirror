# Local parsing

Parse files selected by the [`manifest_ids` list in `config/pipeline.json`](../config/pipeline.json) into [schema v1.0.0](artifact-schemas.md) source, document, and element records. Later-stage arrays remain empty.

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
python scripts/evaluate_parsing.py --input-dir artifacts/parsed --output artifacts/parsed/parsing-evaluation-report.json
```

`--config` is required; `--output-dir` defaults to `artifacts/parsed`. The top-level `manifest_ids` list selects and orders manifest `manifest_id` values. Repeat `--source` to parse a subset of that list, or omit it for all configured sources. Per-document progress and elapsed parsing time go to stderr; the JSON report goes to stdout. The parser continues after a document fails. Exit codes: `0` all parsed; `1` any failed or partial; `2` invalid configuration or manifest. Verification failures have no parsing time; missing OCR dependencies fail affected PDFs only.

When evaluating a subset rerun saved in another directory, pass that directory to `scripts/evaluate_parsing.py` with `--additional-input-dir`; the evaluator selects the newest valid outcome per manifest source across the supplied directories.

## Configuration

Set `parsing` in [config/pipeline.json](../config/pipeline.json); `root`, `manifest_file`, and `manifest_ids` retain their ingestion meanings. Defaults are:

- **PDF:** Docling backend `docling_parse`, table recognition on, `accurate` table mode, no local `artifacts_path`.
- **OCR:** Tesseract `mode: auto` (`off` and `force` also supported), `languages: ["fra", "eng"]`, `scale: 3.0`, `psm: 3`, `tesseract_cmd: "tesseract"`, `tessdata_path: null`. `auto` OCRs PDF raster regions; `force` OCRs full pages.
- **HTML:** `content_selector: "main"`; the pilot sets a Légifrance article selector. Optional `source_overrides` can set `content_selector` or `article_id` for a manifest ID. Each selector must match exactly one element.
- **Markdown / DOCX:** Pipe tables enabled; DOCX headers and footers excluded.

## Read the output

`report.json` holds the latest run; `report-YYYYMMDD-HH-MM-SS-<id>.json` (UTC) preserves each run, including same-second runs. Reports show settings, outcomes, counts, times, and peak memory.

`parsed` means all content selected as canonical evidence was located; it may still have documented warnings for graphical/corrupt OCR, publisher furniture, uncertain visual labels, or an empty Docling table with no cells. `partial` means at least one warning affects completeness, including unresolved document-flow text or an unavailable figure asset. Both outcomes retain their warnings for review. Picture text assembled from adjacent Docling fragments keeps the original ordered refs, text, and provenance in `parse.json` as `docling_refs` and `source_parts`; its element box encloses those fragments.

Parsed files live in `documents/<document-hash>/`. The folder name is the document ID without `doc:`: `doc:` + SHA-256 of `source_id + "\n" + content_sha256`, where `content_sha256` hashes saved bytes. OCR settings do not affect this ID.

| File | How to read it |
| --- | --- |
| `artifact-bundle.json` | The schema-valid source, document, and reading-ordered `elements`. Each element has text, kind, stable ID, and a location (PDF page and optional box, HTML selector, Markdown lines, or DOCX block). Later-stage arrays are empty. |
| `parse.json` | Parser versions/settings, metadata origins, warnings, and extra structure. Join `elements[].element_id` to the bundle's `elements[].id` for parent links, heading context, and table cells or spans. |
| `docling.json` (PDF only) | Docling's native pages, layout, and tables for inspection. This is parser-specific detail, not the normalized artifact contract. |
| `figures/*.png` (PDF only) | Deterministic source-PDF crops for figure elements; hashes and relative paths are linked from their `parse.json` sidecar entries. |

### Evidence and structure contract

`parse.json` assigns every element an `evidence_role`. Downstream text chunking uses only `authoritative` elements as independent evidence. A table is the authoritative text aggregate; its `table_cell` children are `structural`. A PDF `figure` is a structural text container whose linked PNG asset is the authoritative **visual** representation. The figure sidecar entry (joined by `element_id`) records `asset.path` relative to the document folder, `media_type`, `sha256`, `representation_role`, and render scale. The crop uses the figure's source page and exact PDF box at 2× resolution, with a filename derived from the stable element ID. Readable diagram labels are `auxiliary` children for search and review; they must not be chunked as independent evidence or used to infer arrows, ownership, or flow. Coherent document-flow prose and list items accidentally nested under a Docling picture remain normal `authoritative` text, with their Docling parent ref retained in the sidecar.

PDF text with multiple Docling provenance entries is split using each entry's `charspan`, so each element contains only text supported by its page and box. Heading levels use explicit numbering and document-index indentation where those cues resolve a parent; unresolved headings remain conservative. `parse.json` warnings identify excluded graphical fragments, corrupt OCR, publisher furniture, uncertain visual labels, and any unresolved document-flow text by source reference.

Re-parsing with the same source ID and saved bytes replaces the folder for that document ID; the latest parsing report describes the new run. If the saved bytes change, the content hash produces a new document ID and a new folder, while the old parsed folder remains. See [refresh one document](local-chunking.md#refresh-one-document) for the steps to build a current chunk collection from that new edition.

## Limits

Partial PDFs carry warnings for missing page provenance, unavailable figure crops, unresolved document-flow text, or tables spanning pages. DOCX tracked changes and page numbers are unsupported. Figure images preserve visual layout, but the parser does not interpret graphical relationships. Docling may not expose element-level OCR origin. The pilot lacks a real scan; see the [parsing evaluation](pilot-parsing-evaluation.md).
