# Pilot parsing evaluation

This document describes the deterministic parsing-quality gate for the [pilot corpus](../data/manifest.json). The gate compares normalized elements with saved Docling output where available, checks source-verified landmarks, and validates artifact bundles. Run-specific status, counts, finding examples, and selected parse run IDs belong in the generated [evaluation report](../artifacts/parsed/parsing-evaluation-report.json).

## Run the evaluation

From the repository root, with the virtual environment installed and parse artifacts in `artifacts/parsed`:

```sh
.venv/bin/python scripts/evaluate_parsing.py \
  --input-dir artifacts/parsed \
  --output artifacts/parsed/parsing-evaluation-report.json
```

The script writes the report before exiting. Exit status `0` means the gate passed; `1` means it failed. Omit `--output` to print JSON to standard output. If a subset rerun lives elsewhere, repeat `--additional-input-dir PATH`. For another corpus, supply its source-checked landmarks with `--reference PATH`.

The evaluator reads parse reports from the supplied directories and selects the newest outcome per manifest source whose saved bundle matches its reported element count. It reports when the selected documents come from different parse runs. `report.json` is the parser's latest-run alias; the dated `report-*.json` files allow the evaluator to assemble a corpus after subset reruns.

## Checks and acceptance criteria

A pass requires every manifest source to have a parsed or partial outcome, every referenced passage and structural landmark to be found, every selected reading-order pair and element location to be valid, every bundle to pass schema and cross-record validation, and zero audit errors. Documented graphical/corrupt OCR, publisher furniture, and uncertain visual labels are warnings because the figure image remains available. Unresolved document-flow text is a `partial` warning; confirmed missing canonical text or a missing figure image is an error. A parse with only documented harmless exclusions is `parsed` and retains its warnings.

The [reference fixture](../tests/fixtures/pilot_parsing_reference.json) contains draft, source-checked locators. They are not expert-approved legal labels. The evaluator also checks:

- **Text coverage:** each substantive document-flow Docling text node appears in normalized content on its source page, including prose accidentally nested under pictures. The general substantive threshold is five normalized words and 30 alphanumeric characters. Short picture text is checked separately. Reliable visual labels are auxiliary children of a figure; corrupt OCR, footer-only URLs, and uncertain visual tokens are diagnosed rather than treated as missing canonical prose. Source fragments and provenance remain in `parse.json` when a phrase spans Docling nodes.
- **Figure assets:** every PDF figure has a linked PNG crop with a safe relative path, PNG media type, matching SHA-256, and page/bbox provenance matching Docling. The image is authoritative for visual layout; auxiliary labels cannot be promoted into independent text evidence.
- **Duplication:** exact substantive text repeated in independent elements at the same source location, picture text emitted more than once, and table parent text overlapping child cells. Identical phrases at separate PDF boxes are counted as genuine source repetitions, not parser duplication. Table overlap is recorded separately as a retrieval duplication risk; repeated values in distinct cells are not treated as independent duplicates.
- **Multi-provenance text:** page-specific elements match Docling `charspan` slices, rather than repeating the full node on each page.
- **Structure:** empty paragraphs, headings, list items, and tables; invalid parent links; broken heading paths; marked document-flow list items nested under pictures that are typed as paragraphs; and heading-depth distribution. An all-depth-one heading tree in a sizeable PDF is a heuristic warning.
- **Coverage of Docling structures:** unknown labels, unrepresented picture or table nodes, nonempty key-value or form collections, parser warnings, and excluded furniture.

The JSON report contains `documents` with per-document metrics, coded findings, and gate status; `corpus` with totals and warning/error codes; the retained landmark and location measurements; selected run IDs; and the overall `gate` result. Finding examples are capped at five per code, while counts cover all detections.

## Limits

This is a Docling-to-normalized comparison. It cannot detect text Docling failed to recognize, verify OCR against original pages, infer visual relationships, judge legal meaning, or certify every ambiguous short graphical token. The landmark fixture samples selected evidence and structure rather than every paragraph or cell. HTML has no Docling source tree, so source-text loss, figure-asset, and picture-text checks are unavailable there; DOM locations, landmarks, normalized structure, duplication, and bundle validation still run. Table overlap indicates duplicated element content, not proven behavior of a later retrieval index.

The generated report is under `artifacts/`, which the repository ignores. To validate changes to the evaluator itself, run:

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_evaluate_parsing.py' -v
```
