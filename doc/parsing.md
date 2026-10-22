# Parsing

Parse configured [`manifest_ids`](../config/pipeline.json) into [v1.0.0 records](artifact-schemas.md);
later-stage arrays remain empty.

## Setup

Set up Python 3.11+ and dependencies using [ingestion](ingestion.md).

On Ubuntu or Debian, install Tesseract and French/English data:

```sh
sudo apt update
sudo apt install tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng
tesseract --list-langs  # should include fra and eng
```

See [Tesseract installation](https://tesseract-ocr.github.io/tessdoc/Installation.html)
for other systems. Docling may download weights on first use; set
`parsing.pdf.artifacts_path` to use local weights.

## Run

Run from the repository root. Replace the run ID for each new output directory:

```sh
RUN_ID="your-parsing-run-id"
PARSED_DIR="artifacts/parsed-${RUN_ID}"

direnv exec . .venv/bin/python scripts/parse.py --config config/pipeline.json --output-dir "$PARSED_DIR"
```

`--config` is required; output defaults to `artifacts/parsed`. `manifest_ids`
selects/orders sources; add `--source MANIFEST_ID` for a subset in a fresh directory.
Replace `MANIFEST_ID` with a configured source selector; repeat the option for more
sources. Progress/time go to stderr, JSON to stdout.

Continue after failures. Exits: `0` all parsed, `1` any failed/partial, `2` invalid
config/manifest. Missing OCR dependencies fail only affected PDFs.

Next, [evaluate parsing](parsing-evaluation.md); `--additional-input-dir` combines
subset reruns using each source’s newest valid outcome.

## Configuration

Set `parsing` in [config/pipeline.json](../config/pipeline.json). Defaults are:

- **PDF:** Docling `docling_parse`, table recognition on, `accurate` mode, `artifacts_path: null`.
- **OCR:** Tesseract `mode: auto`, `languages: ["fra", "eng"]`, `scale: 3.0`, `psm: 3`, `tesseract_cmd: "tesseract"`, `tessdata_path: null`. `auto` OCRs raster regions; `force` OCRs full pages; `off` disables OCR.
- **HTML:** `content_selector: "main"`. Per-source overrides may set `content_selector` or `article_id`. A selector must match exactly one element.
- **Markdown / DOCX:** Pipe tables enabled; DOCX headers and footers excluded.

## Read the output

`report.json` includes settings, outcomes, counts, times, and peak memory.

`parsed`: canonical evidence located, possibly with graphics/OCR/furniture warnings.
`partial`: uncertain completeness, e.g. unresolved flow text or missing figure asset.
Both retain warnings. Assembled picture text preserves ordered `docling_refs`/
`source_parts` provenance in `parse.json`.

Files live in `documents/<document-hash>/`. The document ID is `doc:` + SHA-256
of `source_id + "\n" + content_sha256`; folders omit `doc:`. OCR settings do not
change the ID.

| File | How to read it |
| --- | --- |
| `artifact-bundle.json` | Schema-valid source, document, and ordered elements with stable IDs and native locations. |
| `parse.json` | Settings, warnings, provenance, evidence roles, parents, and heading/table detail; join on `element_id`. |
| `docling.json` (PDF only) | Native PDF pages, layout, and tables for inspection. |
| `figures/*.png` (PDF only) | Source-PDF figure crops linked and hashed in `parse.json`. |

### Evidence and structure contract

`parse.json` assigns each element an `evidence_role`:

- `authoritative`: text eligible for chunking, including whole tables and coherent
  picture-contained prose with parent refs.
- `structural`: table cells and figure text containers. Figure PNGs are separate
  authoritative visual evidence, with path, media type, hash, role, render scale,
  page and box recorded in the sidecar.
- `auxiliary`: diagram labels, not independent text claims or proof of arrows/flow.

Multi-page text follows Docling `charspan`/page provenance; headings use numbering/
index indentation. Unnumbered headings ending in a colon immediately before a
list are treated as local introductions, so their scope does not persist into
later rules. Bottom-note runs require a recognised note or multiple numbered
notes referenced by the body; adjacent same-page continuations retain all native
references. No cross-page note attachment is inferred.

Docling's labels, groups and page geometry are extraction signals. Heading levels,
paths and resolved `parent_element_id` values in `parse.json` are produced by our
adapter. Later [structural assembly](chunking.md#structural-assembly-after-original-chunking)
preserves these metadata and adds explicit numbered-paragraph/local-list scope;
for example, it connects paragraph 7's banking and insurance lists to that rule
under the ACPR–Tracfin INTRODUCTION without merging the whole introduction.

Table repairs use PDF glyph positions rather than language-specific header words.
Wrapped header lines stay together; merged vertical labels and repeated markers
are separated only when the text and geometry agree. Marker rows are reassigned
only when glyph positions uniquely identify the row.
Row spans retain their governing cell text. Unresolved marker alignment produces
`table_cell_alignment_uncertain` and requires source inspection; OCR-only tables
do not gain invented coordinates. The sidecar records `adapter_version`.

Numeric identifiers retain separators when local PDF glyphs prove their presence.
Cleanup removes control characters and joins layout-hyphenated words. Exclude running
headers, sparse furniture, figure labels and filename-only notes as authoritative
prose. Warnings locate graphics, corrupt OCR, furniture, uncertain labels and
unresolved flow. Check meaningful layout decisions against the PDF.

Unchanged bytes replace the same document folder; changed bytes create a new ID
and leave the old folder. Follow [refresh one document](chunking.md#refresh-one-document)
before chunking a revised edition.

## Limits

`partial` PDFs may lack page provenance/crops or contain unresolved text/multi-page
tables. DOCX tracked changes/page numbers are unsupported. Figures preserve layout,
not relationships; Docling may omit element-level OCR origins. Validate OCR on
representative scans; see [evaluation](parsing-evaluation.md).
