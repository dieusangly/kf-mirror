# AML/CFT pilot sources and evaluation

**13 saved/selected files:** 11 PDFs (801 pages), two official HTML articles;
ten French, three English. All originals are directly in `data/`, with one
[manifest](../data/manifest.json) and pipeline. This scope was agreed on
26 October 2026. Corpus quality approval is pending; commands are in the
[workflow](automated-workflow.md).

## Selected sources

The manifest owns URLs, editions, dates, paths, sizes, hashes and language
relationships. Filenames carry their manifest ID prefix. Coverage:

| ID | Source | File pages | Representative material |
| --- | --- | ---: | --- |
| A | ACPR–Tracfin joint guidelines (FR) | 70 | pp. 3, 5, 7: status, reporting, vigilance; numbered paragraphs and footnotes |
| T | Tracfin threat report 2024–25, Tome III (FR) | 82 | pp. 45–46: case 16 and alerts; p. 59: multi-column alert table; terrorism typologies |
| L1 | Code monétaire et financier, art. L. 561-2-2 (FR) | HTML | Definition of beneficial owner; article ID and numbered clauses |
| L2 | Code monétaire et financier, art. L. 561-6 (FR) | HTML | Ongoing vigilance and cross-references |
| P | ACPR PPE guidance body, 2018 (FR) | 13 | PPE definition and vigilance; read with PANN24 |
| BASEL20 | Basel AML/CFT guidance, revised July 2020 (EN) | 62 | p. 9: three lines of defence |
| NRA23 | French national risk assessment, 2023 (FR) | 194 | p. 137: trusts, domiciliation companies and fiducies |
| R24EN | AMLR, Regulation 2024/1624 (EN) | 111 | p. 96: application dates and exceptions |
| R24FR | AMLR, Regulation 2024/1624 (FR) | 111 | p. 96: corresponding French provisions |
| TFR23EN | Funds-transfer Regulation 2023/1113 (EN) | 39 | pp. 21–23: information accompanying crypto-asset transfers |
| KYC21 | ACPR identification, verification and customer knowledge, 16 December 2021 (FR) | 66 | p. 10: identification; p. 40: retention; pp. 46–47: ownership diagrams |
| GEL26 | ACPR–DG Trésor asset-freezing guidance, 16 March 2026 (FR) | 49 | p. 12: scope; p. 32: alerts; p. 35: immediate notification to DG Trésor |
| PANN24 | ACPR PPE annex, July 2024 (FR) | 4 | p. 1: status does not itself imply suspicion; p. 4: French/foreign PPE functions table |

PDF page numbers are one-based physical file pages, including covers and blank
pages. HTML articles have no fixed page count. Page totals count both AMLR
language variants, because both files are processed.

AMLR variants have distinct source IDs, a shared `work_id` and reciprocal
`language_variant_of` links. The bilingual pair deliberately tests cross-language
retrieval; it is one underlying work. P retains its 2018 body edition and requires
the separately registered PANN24 annex, matching the publisher's in-force pairing.
The prior missing-annex exception is removed. Pairing does not rewrite dated or
transitional wording in the body or certify all cited law as current.

Manifest dates distinguish edition, publication, report period, application,
retrieval and import; unknowns stay unknown. KYC21 is dated 16 December 2021,
although its filename begins `20220404`. GEL26 is dated 16 March 2026 and was
published on 18 March. The three additions were imported from user-downloaded
official originals on 26 October 2026; provenance is declared, with the actual
saved bytes hashed, rather than authenticated by a fresh publisher download.

This is a representative French banking pilot, not exhaustive legal coverage.
The two CMF articles supply narrow primary-law examples. Guidance, reports and
international standards do not replace statutes; distinguish future application
of AMLR from currently applicable obligations.

## Page-count comparison

Physical PDF page counts were checked with `pdfinfo` on 26 October 2026 before
removing the seven excluded files and after importing the three additions.

| Scope | Files | PDFs | HTML articles | PDF pages |
| --- | ---: | ---: | ---: | ---: |
| Previous expanded pilot | 17 | 15 | 2 | 1,122 |
| Agreed core pilot | 13 | 11 | 2 | 801 |
| Reduction | 4 | 4 | 0 | 321 (28.6%) |

The seven exclusions remove 440 pages; KYC21 (66), GEL26 (49) and PANN24 (4)
add 119 pages: **1,122 − 440 + 119 = 801**. The same two HTML articles are
retained and excluded from both page totals.

## Excluded sources and reuse conditions

These seven originals and their manifest entries were removed from the repository,
not archived under `data/`. They are optional extensions, not dependencies of the
13-file pilot. Publisher links below preserve the selection rationale and a route
to reacquisition; recheck availability, edition and hashes before reintroducing them.
They are not all duplicates of retained documents.

| Former ID | Source | PDF pages removed | Why excluded / when to use |
| --- | --- | ---: | --- |
| M | [AMF DOC-2019-16](https://www.amf-france.org/sites/institutionnel/files/private/2023-07/DOC-2019-16_LD%20Clients%20et%20b%C3%A9n%C3%A9ficiaires%20effectifs_VF3_0.pdf) | 33 | Customer/beneficial-owner guidance overlaps with KYC21; add for AMF-sector rules or supervisor comparisons. |
| AMFRA24 | [AMF sector risk assessment, June 2024](https://www.amf-france.org/sites/institutionnel/files/private/2024-06/202406_analyse_sectorielle_lab-ft_fr.pdf) | 50 | NRA23 supplies broad risk coverage; add for asset management, advisers, crowdfunding and other AMF-supervised activities. |
| T2 | [Tracfin activity report 2024, Tome II](https://www.economie.gouv.fr/files/files/directions_services/tracfin/media-document/TRACFIN_2024_Tome2_FR_WEB_0.pdf) | 46 | Institutional reporting rather than operational procedures; add for Tracfin missions, investigations and annual results. |
| COLB24 | [COLB annual report 2024](https://www.tresor.economie.gouv.fr/Institutionnel/Niveau2/Pages/58b72725-da59-4fc3-a355-40cea35befab/files/ea198438-3d69-4649-9699-f30dfb7ce9fb) | 116 | Broad institutional reporting; add for national coordination, supervision, enforcement and statistics. |
| D24EN | [AMLD6, Directive 2024/1640 (EN)](https://eur-lex.europa.eu/eli/dir/2024/1640/oj/eng) | 94 | Distinct from AMLR, but outside the operational core; add for transposition, registers, FIUs and national supervisory arrangements. |
| AR24EN | [AMLAR, Regulation 2024/1620 (EN)](https://eur-lex.europa.eu/eli/reg/2024/1620/oj/eng) | 90 | Distinct institutional legislation; add for AMLA governance, responsibilities and supervisory powers. |
| EUROPOL23 | [Europol 2023 threat assessment, executive summary (EN)](https://op.europa.eu/o/opportal-service/download-handler?identifier=03fe32d1-d9db-11ee-b9d9-01aa75ed71a1&format=pdf&language=en&productionSystem=cellar&part=) | 11 | Broad threat context overlaps with T; add for European comparisons or English threat-report retrieval. |

## Saved originals and evaluation scope

Check the active files offline:

```sh
.venv/bin/python scripts/ingest.py --config config/pipeline.json
```

See [ingestion](ingestion.md). The manifest and pipeline selection must contain
exactly the same 13 source IDs. No paid API calls are needed for this check.

The [development cases](../tests/fixtures/retrieval-expanded-development.json)
contain 17 French/English questions across all 13 files, including cross-language
queries and the AMLR pair. Locators and anchors for the additions were checked
against the supplied originals; labels remain `development_unreviewed` until
independent acceptance review. BankLLM supplied read-only reference metadata,
never approved authored Q&A.

The [mandatory parsing pages](../tests/fixtures/parsing-expanded-review-pages.json)
retain known defects on active sources and add KYC21, GEL26 and PANN24 pages.
Historical five-document smoke/reference fixtures and saved artifacts remain for
regression and snapshot reproduction, including their AMF cases; they do not
define or approve the active 13-file corpus. Published retrieval snapshots have
not been rebuilt by this source migration. Start a fresh workflow run for the
new manifest; prior run identities and approvals must not be reused.

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
