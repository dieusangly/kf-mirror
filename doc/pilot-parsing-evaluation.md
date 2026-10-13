# Pilot parsing evaluation

The parsing-quality gate compares normalized elements with saved Docling output where available, checks source-verified landmarks, and validates bundles. Run-specific results belong in the generated `parsing-evaluation-report.json`; the pilot uses this [manifest](../data/manifest.json).

## Run the evaluation

From the repository root, with saved output in `artifacts/parsed`:

```sh
.venv/bin/python scripts/evaluate_parsing.py \
  --input-dir artifacts/parsed \
  --output artifacts/parsed/parsing-evaluation-report.json
```

The report is written before exit: `0` means pass, `1` fail. Omit `--output` for stdout, add `--additional-input-dir PATH` for subset reruns, or supply another corpus's source-checked landmarks with `--reference PATH`.

The evaluator selects the newest valid outcome per manifest source across supplied directories and reports mixed parse runs. `report.json` is the latest-run alias; dated reports retain subset runs.

## Checks and acceptance criteria

A pass requires a parsed or partial outcome for each manifest source; valid landmarks, reading-order pairs, locations, bundles, and links; and zero audit errors. Documented graphical/OCR exclusions, furniture, and uncertain visual labels are warnings. Unresolved document-flow text makes a parse `partial`; confirmed missing canonical text or a missing figure image is an error. Harmless exclusions may leave it `parsed` with warnings.

The pilot [reference fixture](../tests/fixtures/pilot_parsing_reference.json) has draft source-checked locators, not expert-approved legal labels. Other checks include:

- **Text coverage:** substantive Docling text appears on its source page, including prose nested under pictures; the threshold is five normalized words and 30 alphanumeric characters. Short picture text is checked separately. Uncertain visual tokens and corrupt OCR are diagnosed, not treated as canonical prose; multi-node fragments retain provenance in `parse.json`.
- **Figure assets:** each PDF figure has a safe linked PNG with matching hash and page/box provenance. Auxiliary labels are not independent text evidence.
- **Duplication:** repeated content at one source location, repeated picture text, and table-parent/cell overlap. Identical text at distinct PDF boxes is genuine repetition; table overlap is a separate retrieval risk.
- **Multi-provenance text:** page elements match Docling `charspan` slices, without repeating a full node on each page.
- **Structure:** empty semantic elements, invalid parents/heading paths, mis-typed marked list items, and heading depth; an all-depth-one tree in a sizeable PDF is a warning.
- **Docling coverage:** unknown labels, unrepresented pictures/tables, nonempty key-value/form collections, warnings, and excluded furniture.

The JSON report has per-document and corpus metrics/findings, landmark and location measurements, selected run IDs, and `gate`. Examples are capped at five per finding code; counts are complete.

## Limits

Docling-to-normalized checks cannot find text Docling missed, verify OCR against original pages, interpret diagrams, or judge legal meaning. Landmarks sample rather than certify every element. HTML lacks Docling source-tree, figure, and picture-text checks, but still gets DOM locations, landmarks, structure, duplication, and bundle validation. Table overlap is content duplication, not proven retrieval behavior.

Generated reports under `artifacts/` are ignored by Git. To test evaluator changes:

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_evaluate_parsing.py' -v
```
