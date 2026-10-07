# Pilot parsing quality

Run: 7 October 2026, five [pilot sources](../data/manifest.json) against [reference landmarks](../tests/fixtures/pilot_parsing_reference.json). The eight passages are draft locators, not expert-approved legal labels. Structural landmarks were checked against saved pages; the sample targets difficult content.

## Configuration and coverage

Python 3.12.13 and Docling 2.135.0 parsed three born-digital PDFs with OCR `off` and two HTML articles with configured selectors. PDF layout and table models ran locally; no paid API was used. See the [pilot definition](french-aml-cft-pilot-sources-and-evaluation.md).

| Source | Format | Pages | Elements | Seconds | Seconds/page |
| --- | --- | ---: | ---: | ---: | ---: |
| A, ACPR–Tracfin | PDF | 70 | 940 | 219.541 | 3.136 |
| T, Tracfin Tome III | PDF | 82 | 1,589 | 367.606 | 4.483 |
| M, AMF DOC-2019-16 | PDF | 33 | 558 | 85.044 | 2.577 |
| L1, L. 561-2-2 | HTML | — | 5 | 0.042 | — |
| L2, L. 561-6 | HTML | — | 2 | 0.016 | — |

Total: 185 PDF pages, 3,094 elements. Peak process resident memory was **about 4.0 GiB** across the sequential run, not per source.

## Measured fidelity

| Check | Result | What was checked |
| --- | ---: | --- |
| Draft evidence passages | **8/8** | Distinctive text occurs in an element at the cited file page or legal article. |
| Structural landmarks | **17/17** | Selected headings, numbered items, table headers and a row, legal clauses, and the AMF diagram's figure location. |
| Reading order | **9/9 pairs** | Expected order of selected markers on A p. 3, T p. 59, and L1. |
| Location integrity | **3,094/3,094** | PDF page exists and optional bounding box lies within page bounds; HTML selector resolves uniquely in the saved DOM. |
| Artifact contract | **5/5 bundles** | Existing schema, ID, and cross-record validator passes for every parsed document. |

All five outcomes were `parsed` without reported omissions, meeting the provisional passage and checked-structure targets. The checks do not cover every paragraph or cell, every printed label or box, or legal interpretation. On page 59, they cover two table headers and one row, not the full grid.

## OCR and other formats

A rasterized French-text fixture parsed with Docling `auto` OCR and Tesseract 5.3.4 (`fra`, `eng`) at **0% character and word error**; `force` also scored 0%. This checks integration on clean text, not accuracy on real scans.

Markdown and DOCX are absent from the pilot. Fixtures check headings, paragraphs, tables, order, locations, nested DOCX tables, and Markdown code blocks. External-document quality is unmeasured.

## Reproduce

Copy the configuration, set `parsing.ocr.mode` to `off`, then run:

```sh
cp config/pipeline.json config/pipeline-ocr-off.json
# Set parsing.ocr.mode to "off" in config/pipeline-ocr-off.json.
python scripts/parse.py --config config/pipeline-ocr-off.json --output-dir artifacts/parsed
python scripts/evaluate_parsing.py --input-dir artifacts/parsed --output artifacts/parsed/quality-report.json
PYTHONPATH=src python -m unittest discover -s tests -v
python scripts/validate_artifact_examples.py
```

The supplied configuration defaults to `auto`. Keep the copy in `config/` so its relative `root` resolves. Timings depend on hardware and cached model weights; per-source times include loading and conversion. See the [parsing guide](local-parsing.md) for setup and outputs.
