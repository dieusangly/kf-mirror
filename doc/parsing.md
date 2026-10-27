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
PARSED_DIR="artifacts/${RUN_ID}/parsing"

direnv exec . .venv/bin/python scripts/parse.py --config config/pipeline.json --output-dir "$PARSED_DIR"
```

`--config` is required; output defaults to `artifacts/parsed`. `manifest_ids`
selects/orders sources; repeat `--source MANIFEST_ID` for subsets in a fresh directory.
Progress goes to stderr, JSON to stdout. Parsing continues after individual failures.
Exits: `0` all parsed, `1` any failed/partial, `2` invalid config/manifest.
Missing OCR dependencies fail only affected PDFs.

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

Element `evidence_role` in `parse.json`:

- `authoritative`: text eligible for chunking, including whole tables and coherent
  picture-contained prose with parent refs.
- `structural`: table cells and figure text containers. Figure PNGs are separate
  authoritative visual evidence, with path, media type, hash, role, render scale,
  page and box recorded in the sidecar.
- `auxiliary`: diagram labels, not independent text claims or proof of arrows/flow.

The adapter derives heading paths and parents from Docling labels, groups and
geometry. Multi-page text follows native character spans. Local introductions
before lists do not become persistent section headings; later
[structural assembly](chunking.md#structural-assembly-after-original-chunking)
adds numbered-paragraph and local-list scope.

The `pdf-source-v5` adapter corrects a working copy; `docling.json` stays unchanged.
The sidecar ledger records source/node hashes and evidence for each repair:
contents entries, aligned lists/final rows, captions, notes, headings and column
headers. Column coarsening requires painted-divider and independent-header proof.
Every glyph must be accounted for. Verified parallel lists retain entry rows even
without painted separators; box and note scopes remain explicit.

An occluded duplicate requires a complete textual witness and zero changed
pixels when its whole text objects are removed from an in-memory page render.
Visible repetitions stay intact. Mixed native-text/OCR spans are classified
independently. Missing literal glyphs, narrow lettering and digit-only transcripts
do not prove that OCR is decorative: outlined text may carry real values.
Unmatched short suffixes are retained because an underestimated native box can
omit a real identifier. Raster heading recovery
uses bounded local Tesseract verification (PSM 6, configured languages/scale,
at most 12 regions per page, 30-second call timeout). Every recovered word must
have confidence at least 85. Failed graphic/cover verification retains the native
transcript and records a blocking uncertainty rather than excluding it as furniture.
The ledger records word boxes,
confidence, engine version, actual settings and trained-language-model hashes.
Source/crop/settings/model-specific caches are retained; they are not approvals.
The ledger also records `uncertainties`, propagated as parser warnings. The source
audit independently replays them, so deleting warnings cannot make the gate pass.

- **Tables:** repair labels/markers only with matching glyphs, geometry, leaf
  headers and unique row anchors. Preserve governing row spans and native numeric
  columns unless repair is justified. Ambiguity emits `table_cell_alignment_uncertain`;
  OCR-only tables receive no invented coordinates.
  The Docling adapter can reconstruct ruled cells from literal PDF glyphs,
  painted rules and visible fill boundaries. It requires a rectangular partition
  and complete text conservation; invisible background edges and isolated text
  underlines cannot introduce rows. Border overhangs are clipped to actual painted
  grid junctions. Implicitly closed filled rectangles remain source borders;
  disconnected in-cell paint cannot introduce a grid hole. A uniform initial
  translucent background covering the table is distinguished from later overlays.
  `table_geometry` in `parse.json` records the
  source grid, glyph digest and any perimeter supported by aligned rule endpoints.
  Repaired cells retain physical page boxes and `header_origin: pdf_rulings_and_glyphs`.
  When a divider moves between rows, `physical_columns` and `column_aliases` may
  record its alternative positions. This requires uniquely located, exact
  native/source cell agreement; no physical cell may collapse to zero width.
  Unmapped glyphs, conflicting borders or unsupported overlapping paint emit
  `table_geometry_uncertain` and leave the native table unchanged with partial status.
  Native callouts with only outside edges, open table styles, and adjacent native
  page fragments lacking a painted top remain in the native adapter. Header shading
  and zebra stripes alone cannot establish a ruled frame. Native cells retain
  `header_origin: docling_table_structure` and their own audit checks.
  Incomplete paint cannot collapse an independently witnessed native partition:
  every source glyph must have one matching native cell, separate columns need
  independent first-row leaf headers, and separate number cells need aligned
  prose anchors in the other column. Wrapped prose and subdivisions beneath a
  shared header do not establish such boundaries.
  Text normalization runs inside cells; prose dehyphenation must never join
  separate serialized rows or remove a row-ending identifier hyphen.
  Numeric superscript note markers are serialized separately, for example
  `42 [17]`. Separation requires complete cell glyph agreement, smaller raised
  digits beside a full-size digit and a unique source-verified native footnote
  below the cell on the same page. `footnote_references` retains marker boxes,
  glyph indices and the native note reference/location. Unsupported splits
  remain unchanged for inspection; digit size alone never establishes a note.
- **Table continuation:** compatible adjacent source grids, governing headers and
  source-located section/caption boundaries establish explicit `table_continuation`
  references. They point to original header spans and supported governing-cell
  fragments; text is never copied into a cell on a different page. `table_rows`,
  `table_headers` and reciprocal `table_continues_to` references preserve scope.
  Captions and sections are checked by source location even when native traversal
  yields them out of order. Self-contained tables with their own headers are checked
  before comparison with prior columns. Recognised year headers may establish their
  unmarked label stub. Unheaded, independently labelled field/description rows have
  `table_role: key_value`; they govern their own values without an invented header.
  Split-row witnesses or matching multi-row governing spans restrict inherited
  labels to the affected rows. A coarser physical partition can inherit the original
  logical columns only where every source header row spans each removed boundary.
  `column_mapping` in continuation evidence records that boundary correspondence;
  physical geometry and boxes remain unchanged, and cell column spans preserve
  alignment with the original header. Other changed columns, missing headers and ambiguous blank
  cells remain blocking continuation warnings. Chunking cites these original spans,
  repeats all governing header rows, and fails if mandatory context cannot fit the
  configured budget.
  Open two-column fragments retain native boxes only with three painted column
  boundaries and exact glyph ownership (`native_fields_with_source_columns`).
  Painted section bands provide `table_section_headers` for the next page, not
  earlier rows. Inherit only the last section and labels contained in its wording;
  never invent borders or copy cells.
- **Notes:** recover recognised or multiply body-referenced markers; join only
  within a supported page/column. Preserve native refs and recompute roles.
  Filename-only notes remain structural; no cross-page attachment is inferred.
- **Figures:** coherent prose is authoritative, diagram labels auxiliary,
  uncertain flow partial. Link figure PNGs and represent repeated native captions once.
- **Text:** restore numeric separators only with local PDF glyph proof; reject
  altered digits. Remove controls, join layout-hyphenated words and exclude publisher
  furniture from authoritative prose. Unresolved decisions remain warnings.
  PDFium's internal hyphen sentinel is recovered only with a positive
  `FPDFText_IsHyphen` result; geometry distinguishes a following line break.
  Unidentified glyphs are never replaced by guessed words or numbers.
  Running furniture is classified after multi-page character-span splitting.
  Short repeated headers require explicit native page-header/furniture evidence;
  identical wording outside the top strip remains body text. Contiguous native
  list groups with a single column and nonoverlapping rows can be ordered locally
  even when pictures or other columns prevent whole-page ordering.

Unchanged bytes replace the same document folder; changed bytes create a new ID
and leave the old folder. Follow [refresh one document](chunking.md#refresh-one-document)
before chunking a revised edition.

## Limits

`partial` PDFs may lack page provenance/crops or contain unresolved text/multi-page
tables. DOCX tracked changes/page numbers are unsupported. Figures preserve layout,
not relationships; Docling may omit element-level OCR origins. Validate OCR on
representative scans; see [evaluation](parsing-evaluation.md).
