# Measured pipeline benchmarks

**Docling remains the stronger measured baseline.** Unstructured `hi_res`
recovers tables and figures absent from `fast`, but still fails source-quality
checks. The historical runs below were not accepted. The subsequent
[Docling run](../artifacts/20261028-docling-aml-cft-01/document-structuring/document_structuring_review_report.json)
is accepted for downstream processing.

## Runs and method

All runs share one immutable ingestion snapshot: **13 documents, 11 PDFs / 801
pages and two HTML articles**. L1/L2 use the same internal HTML reader, so this
compares PDF engines. No paid services or downstream model calls were used ($0).

| Run | Execution |
| --- | --- |
| [Docling baseline](../artifacts/20261028-docling-aml-cft-01/inspection/baseline/run.json) | Historical source-bound native replay; superseded output removed, reports archived. |
| [Unstructured fast](../artifacts/20261027-unstructured-aml-cft-01/fast-baseline/run.json) | 0.27.35; embedded text, no table inference. |
| [Unstructured hi_res](../artifacts/20261027-unstructured-aml-cft-01/run.json) | 0.27.35; YOLOX, table inference, French/English OCR, 350 DPI; three CPU workers. |

The active `20261027-unstructured-aml-cft-01` output is hi_res; fast is archived
under `fast-baseline/`. Parsing ran on 27 October 2026; final validation completed
on 28 October. At that time, all 388 Docling/shared-ingestion files and 105 original
fast files were verified preserved; two path-adjusted fast metadata files have exact
original copies. Run descriptors retain settings, environments, model hashes,
worker reports and preservation evidence; live config consolidation does not
rewrite that history. Hi-res weights were cached in a four-page preflight;
corpus execution used `HF_HUB_OFFLINE=1`.

## Results

| Measurement | Docling | Unstructured fast | Unstructured hi_res |
| --- | ---: | ---: | ---: |
| Normalized elements | 14,700 | 16,672 | 12,782 |
| Authoritative elements covered | 9,202 / 9,202 | 14,194 / 14,194 | 11,570 / 11,570 |
| Structural chunks | 4,515 | 6,297 | 4,838 |
| Tables / canonical cells | 87 / 4,489 | 0 / 0 | 89 / 0 (native HTML) |
| Figure elements / saved assets | 148 / 148 | 0 / 0 | 140 / 135 |
| Headings / list items / footnotes | 1,419 / 4,881 / 935 | 4,827 / 2,290 / 0 | 981 / 4,067 / 0 |
| Citable development anchors | 38 / 38 | 35 / 38 | 38 / 38 |
| Legal-scope regressions | 3 / 3 | 3 / 3 | 3 / 3 |
| Parsing automatic audit | Pass: 0 errors, 506 warnings | Fail: 420 empty elements, 201 invalid boxes | Fail: 206 invalid boxes, no empty elements |
| Chunking automatic audit | Pass | Pass | Pass after source-order repair |
| Source/layout acceptance | Rejected in final baseline review | Rejected | Rejected |

Evidence: [Docling validation](../artifacts/20261028-docling-aml-cft-01/inspection/baseline/validation.json),
[fast validation](../artifacts/20261027-unstructured-aml-cft-01/fast-baseline/validation.json),
[hi_res validation](../artifacts/20261027-unstructured-aml-cft-01/validation.json),
[Docling–hi_res comparison](../artifacts/20261027-unstructured-aml-cft-01/comparison.json),
[fast–hi_res comparison](../artifacts/20261027-unstructured-aml-cft-01/comparison-fast.json).
Counts and development regressions are not independent accuracy measurements.

## Source-quality findings

[Fast evidence](../artifacts/20261027-unstructured-aml-cft-01/fast-baseline/source-review/findings.json)
covers eight targeted pages (six formal failures, two supplemental).
[Hi_res evidence](../artifacts/20261027-unstructured-aml-cft-01/source-review/findings.json)
covers nine failures: five critical, two major, two minor; 423 of 432 parsing
review units and the chunk source reviews remain pending. These defect-focused
samples do not estimate a corpus-wide failure rate.

| Issue | Fast → hi_res |
| --- | --- |
| Raster content | A p62 has no fast extraction; hi_res adds figures on pp62–63, but table OCR is corrupted and readable image prose remains non-citable. Empty pages fall from eight to six (T/BASEL20); they require inspection. |
| Tables | Hi_res detects tables, but T p65 loses a row and corrupts rotated headers/cells; BASEL20 p40 splits/omits rows; PANN24 p4 invents columns/rows and omits a long row from HTML despite surviving plain text. |
| Citable text | Hi_res restores Article 90 application-date sentences on R24EN/FR p96, recovering three anchors. Substantive footnotes still become non-citable `Footer` text; no canonical footnotes are emitted. |
| Numbers and hierarchy | NRA23 p31 still joins year `2010` and note `31` as `201031`. Hi_res improves list separation, R24EN p35 labels and PANN24 p1 headings, but detaches/omits markers; 979 of 981 headings are level 1. |
| Geometry | Both retain 200 invalid boxes in TFR23EN and one in KYC21. Hi_res adds five invalid figure boxes (T: four; NRA23: one), preventing crops. All 135 successful crops are linked and preserved. |

SDK text, classifications and geometry are preserved rather than silently
repaired. Native HTML may disagree with plain text; authoritative-element
coverage cannot detect substantive text misclassified as a footer.

## Measurement limits

- **Chunker versions differ.** Hi_res exposed stale hierarchy links that moved
  NRA23 p57 ahead of pp54–56. `structural-v9` prevents joins across intervening
  sections; rechunking saved parsing fixed the audit without reparsing. Original
  4,830-chunk output and failed audit remain in `attempts/before-reading-order-fix/`.
  Docling/fast retain v8, so final chunk counts include a packing confounder.
  Docling also has source-aware PDF repairs absent from the Unstructured bridge.
- **Text-layer agreement is a proxy.** PDFium omits raster text, includes page
  furniture and may share superscript errors; its order cannot certify columns.
  Word agreement (411,633 tokens) is 97.226% / 96.613% / 96.650%; numeric agreement
  (17,745 tokens) is 90.950% / 89.276% / 90.149% for Docling / fast / hi_res.
  Source-aware corrections may lower agreement while improving fidelity.
- **Timings are not comparable throughput.** Docling's 387.679 s is replay;
  fast took 356.781 s for final executions (410.759 s including retries), peak
  process RSS 782.5 MiB, with initial NLP warmup. Hi_res took 4,563.231 s wall
  time (76 min); overlapping document times sum to 13,534.577 s. Maximum worker
  RSS was 1,984.4 MiB, not total concurrent memory. Its 96.873 s preflight and
  interrupted ~855 s serial attempt are separate; package installation is excluded.
- **Implementation validation is not extraction acceptance.** The retained
  hi_res implementation check passed 601 tests (35 skipped, 126 subtests).
  Source repairs and fresh reviews are still required. No `ocr_only`, hosted
  offering, claims, summaries, indexing or assistant-answer comparison was run.

For another provider, reuse the same snapshot/corpus in a fresh run directory;
record versions, native output, settings, cost, fresh timing and memory. Compare
source text/numbers, order, hierarchy, complete tables/lists, figures, locations
and coverage; apply identical source-review and acceptance gates. See
[commands](aml-cft-pipeline.md), [providers](providers.md) and
[approach survey](state-of-the-art.md).
