# French AML/CFT pilot sources and evaluation

Scope: public French banking AML/CFT (LCB-FT) materials in born-digital PDF and official HTML. OCR awaits a representative scan. The AMF document tests cross-authority handling, not banking guidance.

## Selected sources

IDs match the [source manifest](../data/manifest.json), which records stable source IDs, titles, classes, URLs, versions, saved files, dates, media types, sizes, and hashes. This table explains the selection.

| ID | Source | Document class | Representative material |
| --- | --- | --- | --- |
| A | ACPR–Tracfin joint guidelines | Regulatory guidance PDF | pp. 3, 5, 7: status, reporting, vigilance; numbered paragraphs and footnotes |
| T | Tracfin threat report 2024–25, Tome III | Typology PDF | pp. 45–46: case 16 and alerts; p. 59: multi-column alert table |
| M | AMF DOC-2019-16 | Sector guidance PDF | p. 18: beneficial-owner rules; p. 10: ownership diagram |
| L1 | Code monétaire et financier, art. L. 561-2-2 | Versioned legal HTML | Definition of beneficial owner; article ID and numbered clauses |
| L2 | Code monétaire et financier, art. L. 561-6 | Versioned legal HTML | Ongoing vigilance and cross-references |

PDF page numbers are one-based file pages.

## Draft passage annotations

These paraphrases and locators are **not expert-approved gold data**. A French AML/CFT reviewer must verify them before acceptance testing.

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

- **Evidence:** resolve all eight questions to the cited page/paragraph or article; generated answers must cite the supporting source.
- **Parsing:** retain cited passages and reading order. Preserve at least 90% of checked headings, numbered paragraphs, and table headers/rows on selected pages; log failures.
- **Retrieval:** find at least seven of eight supporting passages in the top five, alongside a lexical baseline. Eight questions are only a smoke test.
- **Topic knowledge:** review grounding and topic assignments; record ambiguity, version conflicts, and unsupported summaries. The draft annotations lack approved topic labels and a numeric classification target.
- **Cost:** use no paid model/API calls for selection or parser comparison. Record time per page, peak memory, parser/OCR settings, and later model-token cost; set a hardware-specific runtime limit after measurement.
