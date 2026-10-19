# Parsing evaluation

Check normalized elements against Docling output/source-verified landmarks and
validate bundles against the configured [manifest](../data/manifest.json).
Save findings in `parsing-evaluation-report.json`.

## Run

From the repository root, select a saved parsing run:

```sh
RUN_ID="your-parsing-run-id"
PARSED_DIR="artifacts/parsed-${RUN_ID}"

.venv/bin/python scripts/evaluate_parsing.py \
  --input-dir "$PARSED_DIR" \
  --output "$PARSED_DIR/parsing-evaluation-report.json"
```

| Option | Use |
| --- | --- |
| Omit `--output` | Print the report to stdout. |
| `--additional-input-dir PATH` | Include subset reruns; select the newest valid outcome per manifest source. |
| `--source-root PATH` | Resolve saved source paths against another root (default: repository root). |
| `--reference PATH` | Opt into source-checked landmarks matching the selected corpus; omit when no matching fixture exists. |

Write the report before exit: `0` pass, `1` fail. When combining runs, record the input directories and selected source outcomes.

## Checks and acceptance

Passing requires parsed/partial outcomes for every manifest source, valid
locations/bundles/links, any supplied landmarks/order checks and zero errors.
Without `--reference`, `reference_status` is `not_supplied`: no source-landmark
coverage is claimed. Missing canonical text/
figure images are errors. Unresolved flow means `partial`; documented graphics/
OCR, furniture and uncertain labels may remain warnings on `parsed` outcomes.

Reference fixtures contain source-checked locators, not approved domain labels.
Use only fixtures that match the selected source versions. Checks cover:

- **Coverage:** substantive Docling text (at least five normalized words and 30
  alphanumeric characters), including picture-contained prose. Short picture text
  is checked separately; assembled fragments retain provenance in `parse.json`.
- **Figures:** safe linked PNGs with matching hashes, pages and boxes. Auxiliary
  labels are not independent text evidence.
- **Duplication:** repeated same-location text, repeated picture text and table/cell
  overlap. Identical text at distinct PDF boxes is genuine repetition.
- **Provenance:** multi-page elements match Docling `charspan` slices rather than
  repeating the full node on each page.
- **Structure:** empty elements, invalid parents/headings, list typing and depth.
  A sizeable PDF with only depth-one headings produces a warning.
- **Docling output:** unknown labels, missing pictures/tables, nonempty forms or
  key-value collections, warnings and excluded furniture.

Reports include document/corpus metrics, landmark/location measurements, run IDs
and `gate`. Findings show at most five examples per code; counts are complete.

## Limits and development

Checks cannot find text Docling missed, verify OCR, interpret diagrams or judge
legal meaning. Diagnose corrupt OCR/uncertain labels rather than treating them
as prose. Landmarks sample the source. HTML gets DOM-location, landmark, structure,
duplication and bundle checks, without Docling/figure checks. Table overlap does
not establish retrieval behaviour.

Reports under `artifacts/` are ignored by Git. Test evaluator changes with
`.venv/bin/python -m unittest discover -s tests -p 'test_evaluate_parsing.py' -v`.
