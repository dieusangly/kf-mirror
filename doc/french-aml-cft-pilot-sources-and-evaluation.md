# French AML/CFT pilot sources and evaluation

Scope: public French materials relevant to banking AML/CFT (LCB-FT). The reference set covers born-digital PDF and official HTML. OCR is outside this pilot until a representative scan is selected. The AMF document tests cross-authority handling and must not be treated as banking guidance.

## Selected sources

The IDs below match `id` entries in the [source manifest](../data/manifest.json). The manifest is the single inventory for each publisher URL and version, local snapshot, retrieval date, media type, size, and hash; this table records why the source was selected.

| ID | Source | Document class | Representative material |
| --- | --- | --- | --- |
| A | ACPR–Tracfin joint guidelines | Regulatory guidance PDF | pp. 3, 5, 7: status, reporting, vigilance; numbered paragraphs and footnotes |
| T | Tracfin threat report 2024–25, Tome III | Typology PDF | pp. 45–46: case 16 and alerts; p. 59: multi-column alert table |
| M | AMF DOC-2019-16 | Sector guidance PDF | p. 18: beneficial-owner rules; p. 10: ownership diagram |
| L1 | Code monétaire et financier, art. L. 561-2-2 | Versioned legal HTML | Definition of beneficial owner; article ID and numbered clauses |
| L2 | Code monétaire et financier, art. L. 561-6 | Versioned legal HTML | Ongoing vigilance and cross-references |

PDF page numbers are one-based file pages.

## Draft passage annotations

These are paraphrased answer labels with exact source locators, **not yet expert-approved gold data**. A French AML/CFT reviewer must verify the legal interpretation and locators before they are used for acceptance tests.

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

- **Evidence:** all eight reference questions must resolve to the cited page/paragraph or article; a generated answer must not cite a different source for the same claim.
- **Parsing:** retain all cited passages and their reading order. On the selected pages, preserve at least 90% of manually checked headings, numbered paragraphs, and table headers/rows; log every failed page.
- **Retrieval:** aim for at least seven of eight supporting passages in the top five results, with a simple lexical baseline reported alongside. Eight questions are a smoke test, not a statistical performance claim.
- **Cost:** use no paid model/API calls for corpus selection or the core parser comparison. Record elapsed time per page, peak memory, parser/OCR settings, and any later model-token cost; set a hardware-specific runtime limit after the first measured run.

## Task 1 completion check

The selected French banking AML/CFT scope, supported pilot classes (born-digital PDF and versioned official HTML), representative pages/passages, and provisional quality/cost targets satisfy Task 1's written completion criteria. The eight question rows are **draft annotations**, not expert-approved gold data. French AML/CFT expert review of their interpretation and locators is a later gate before using them for acceptance tests or making retrieval-quality claims; it does not block Task 1 or schema design.

Independent source checks found the cited passages in the selected 70-page ACPR PDF (A1–A2), 82-page Tracfin PDF (T1–T2), 33-page AMF PDF (M1–M2), and the two revision-specific Légifrance articles (L1a–L2a). These checks establish locator plausibility, not legal approval or a measured parsing/retrieval result.
