# AML/CFT pilot sources and evaluation

One corpus of **17 saved and selected files**: 15 PDFs and two official HTML
articles, in French (11) and English (6). All use the same manifest, ingestion
function and pipeline selection. See the [workflow](resumable-pilot-workflow.md)
for run/status/resume commands. Quality approval of the 17-file corpus is pending.

## Selected sources

The [manifest](../data/manifest.json) records IDs, titles/classes, URLs, versions,
saved files, dates, media types, sizes and hashes. Selection rationale:

All saved filenames carry their manifest ID prefix.

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

AMLR's language variants have distinct source IDs, a shared `work_id` and reciprocal
`language_variant_of` references. The historical PPE body is accepted for this pilot
without its intended annex pairing; it is not complete current guidance. Reports
and international guidance do not substitute legally for French statutory provisions.
AMF material covers its own sector, rather than banking guidance.

Edition, publication, reporting period, application, retrieval and import dates remain
separate in the manifest; unknown dates remain unknown. Europol's document is the
2023 assessment's executive summary, listed by the Publications Office in 2024.

Check all saved files and the active selection directly from the manifest:

```sh
.venv/bin/python scripts/ingest.py --config config/pipeline.json
```

Ingestion verifies presence, media type, size and SHA-256 against the manifest.
It uses saved originals without network requests; see [ingestion](ingestion.md).

## Draft passage annotations

**Draft paraphrases/locators, not approved gold data.** A French AML/CFT reviewer
must verify them before acceptance testing.

The [development cases](../tests/fixtures/retrieval-expanded-development.json)
cover all 17 files with 22 French/English questions, including cross-language
queries and the AMLR language pair. The examples below cover French source passages.
BankLLM was used only as read-only reference metadata; authored Q&A was not imported
as approved evaluation data.

| ID | French evaluation question | Expected answer, paraphrased | Supporting passage |
| --- | --- | --- | --- |
| A1 | Les lignes directrices ACPR–Tracfin ont-elles un caractère contraignant ? | Elles sont explicatives et non contraignantes. | A, p. 3, introduction §1 |
| A2 | Une COSI exige-t-elle un soupçon, comme une déclaration de soupçon ? | Non. Elle est systématique lorsque ses conditions sont remplies, indépendamment d'un soupçon. | A, p. 5, introduction §12 |
| T1 | Quel schéma illustre le cas n° 16 de Tracfin ? | Des sociétés éphémères ouvrent des comptes de paiement, reçoivent des virements puis transfèrent rapidement les fonds vers l'étranger. | T, p. 45, cas n° 16, étapes 1–4 |
| T2 | Quels signes d'alerte du cas n° 16 concernent les flux d'une société récente ? | Volume élevé pour une société récente, mouvements rapides au crédit et au débit, et sorties vers l'étranger figurent parmi les signes cités. | T, pp. 45–46, « Critères d'alerte » du cas n° 16 |
| M1 | Quelle différence la doctrine AMF fait-elle entre identifier et vérifier un bénéficiaire effectif ? | Identifier consiste à recueillir les éléments d'identité; vérifier consiste à les confirmer avec des documents probants avant la relation d'affaires. | M, p. 18, §§2.2, 2.2.1 et 2.2.2 |
| M2 | La doctrine AMF prévoit-elle une exception d'identification pour une société cotée ? | Oui, sous les conditions de cotation ou de transparence précisées dans la doctrine, que l'assujetti doit pouvoir justifier. | M, p. 18, §2.2.1 |
| L1a | Qui peut être bénéficiaire effectif au sens du code ? | Une ou plusieurs personnes physiques contrôlant finalement le client, directement ou indirectement, ou pour lesquelles une opération ou activité est réalisée. | L1, art. L. 561-2-2, 1°–2° |
| L2a | Pendant combien de temps la vigilance sur une relation d'affaires s'exerce-t-elle ? | Pendant toute la relation d'affaires, avec examen des opérations à la lumière d'une connaissance actualisée de cette relation. | L2, art. L. 561-6 |

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
