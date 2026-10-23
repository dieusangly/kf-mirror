# AML/CFT pilot sources and evaluation

**17 saved/selected files:** 15 PDFs, two official HTML articles; 11 French, six
English. They share one [manifest](../data/manifest.json) and pipeline. Corpus
quality approval is pending; commands are in the [workflow](resumable-pilot-workflow.md).

## Selected sources

The manifest owns URLs, editions, dates, paths, sizes, hashes and language
relationships. Filenames carry their manifest ID prefix. Coverage:

| ID | Source | Document class | Representative material |
| --- | --- | --- | --- |
| A | ACPR–Tracfin joint guidelines | Regulatory guidance PDF | pp. 3, 5, 7: status, reporting, vigilance; numbered paragraphs and footnotes |
| T | Tracfin threat report 2024–25, Tome III | Typology PDF | pp. 45–46: case 16 and alerts; p. 59: multi-column alert table |
| M | AMF DOC-2019-16 | Sector guidance PDF | p. 18: beneficial-owner rules; p. 10: ownership diagram |
| L1 | Code monétaire et financier, art. L. 561-2-2 | Versioned legal HTML | Definition of beneficial owner; article ID and numbered clauses |
| L2 | Code monétaire et financier, art. L. 561-6 | Versioned legal HTML | Ongoing vigilance and cross-references |
| P | ACPR PPE body (FR) | Historical guidance PDF | p. 4: PPE definition; 2018 body only, without annex |
| T2 | Tracfin 2024 activity report, Tome II (FR) | Activity report PDF | p. 5: three missions |
| R24EN | AMLR, Regulation 2024/1624 (EN) | EU legislation PDF | p. 96: application dates and exceptions |
| R24FR | AMLR, Regulation 2024/1624 (FR) | EU legislation PDF | p. 96: corresponding French provisions |
| D24EN | AMLD6, Directive 2024/1640 (EN) | EU legislation PDF | p. 86: transposition |
| AR24EN | AMLAR, Regulation 2024/1620 (EN) | EU legislation PDF | p. 21: Authority tasks |
| TFR23EN | Funds-transfer Regulation 2023/1113 (EN) | EU legislation PDF | p. 21: information accompanying crypto-asset transfers |
| NRA23 | French national risk assessment, 2023 (FR) | Risk assessment PDF | p. 137: trusts, domiciliation companies and fiducies |
| AMFRA24 | AMF sector risk assessment, June 2024 (FR) | Risk assessment PDF | p. 3: risk-rating scale |
| COLB24 | COLB annual report 2024 (FR) | Activity report PDF | p. 19: beneficial-owner register access |
| BASEL20 | Basel AML/CFT guidance, revised July 2020 (EN) | Supervisory guidance PDF | p. 9: three lines of defence |
| EUROPOL23 | Europol 2023 threat assessment, executive summary (EN) | Threat assessment PDF | p. 2: professional enablers |

PDF page numbers are one-based file pages.

AMLR variants have distinct source IDs, a shared `work_id` and reciprocal
`language_variant_of` links. The accepted historical PPE body lacks its unresolved
annex and is not complete current guidance. Reports/international guidance do not
legally replace French statutes; AMF material has its own sector scope.

Manifest dates distinguish edition, publication, report period, application,
retrieval and import; unknowns stay unknown. Europol is the 2023 executive summary,
listed by the Publications Office in 2024.

Check all saved files and the active selection directly from the manifest:

```sh
.venv/bin/python scripts/ingest.py --config config/pipeline.json
```

This verifies saved originals offline; see [ingestion](ingestion.md).

## Draft passage annotations

The [development cases](../tests/fixtures/retrieval-expanded-development.json)
contain 22 French/English questions across all 17 files, including cross-language
queries and the AMLR pair. These draft locators/paraphrases require independent
source review before acceptance; none is configured. BankLLM supplied read-only
reference metadata, never approved authored Q&A.

## Provisional quality and cost targets

- **Evidence:** resolve development questions to cited pages/paragraphs/articles;
  generated answers must cite support.
- **Parsing:** retain passages/reading order and ≥90% of checked headings, numbered
  paragraphs and table headers/rows on selected pages; log failures.
- **Retrieval:** retain the 0.875 combined top-five hit-rate target in each mode;
  compare lexical/vector baselines. Development cases are smoke checks.
- **Topics:** review grounding/assignments; record ambiguity, version conflicts and
  unsupported summaries. Approved labels and a numeric classification target are absent.
- **Cost:** no paid model/API calls for selection/parser comparison. Record time/page,
  peak memory, parser/OCR settings and later token cost; set hardware-specific
  runtime limits after measurement.
