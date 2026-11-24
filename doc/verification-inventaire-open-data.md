# Vérifier l’inventaire des sources ouvertes pour Chrysalide

## Mission

Vérifier **toutes les lignes** de l’inventaire pour les experts-comptables, les commissaires aux comptes et les avocats. Produire un rapport sourcé utilisable par Thomas Serval pour corriger l’inventaire et décider quelles sources tester en premier. La session porte sur la revue et les contrôles en lecture seule.

## Sources de départ

- Dépôt de travail : `/home/dsly/Projects/knowledge-foundry`. Lire `.agents/AGENTS.md` avant de commencer.
- Inventaire du 21 novembre 2026, 13 pages. Utiliser [la copie locale du PDF](/tmp/knowledge-foundry-pdf/Inventaire_sources_open_data_professions_reglementees.pdf) si elle est disponible. Sinon, récupérer la pièce jointe depuis [le courriel d’origine](https://mail.google.com/mail/u/0/#inbox/FMfcgzQjgzsKsFCqTwMdRcqLCTZmZRhP). [Version en ligne](https://claude.ai/artifact/TKEdcr5YRuZGBAgjvG3fth)
- Pour les sources LCB-FT déjà collectées, lire `data/manifest.json`. Vérifier auprès de chaque éditeur toute affirmation qui dépend de la version, de la date ou de l’accès.

## Travail à faire

1. **Recenser les affirmations à vérifier.** Relever le socle commun, les trois blocs métiers, les manques transverses et les priorités d’intégration. Donner à chaque ligne un identifiant et une page. Relier les doublons aux mêmes preuves. Inclure les affirmations des synthèses qui ajoutent un taux, une date, une restriction ou un jugement de couverture.
2. **Vérifier auprès des producteurs.** Pour chaque ligne, confirmer le contenu, le périmètre, les formats, la fraîcheur, l’accès, les conditions de réutilisation affichées et le chemin d’intégration. Indiquer si le MCP découvre la fiche, lit un fichier ou renvoie vers une API externe. Distinguer accès public, inscription, habilitation et licence commerciale. Relever les dates d’application ou d’ouverture et les limites historiques lorsqu’elles changent la réponse.
3. **Tester ce qui est accessible.** Pour les sources prioritaires, consigner un contrôle positif connu et un cas où le résultat est vide ou incomplet. Pour un accès qui exige un compte ou une habilitation indisponible, indiquer précisément ce que la documentation permet d’établir et ce qui reste à tester. Échantillonner la version à deux dates pour LEGI/KALI, et la fidélité des tableaux, notes et renvois sur un extrait du BOSS et un PDF de l’ANC. Consigner la date, le test et l’observation.
4. **Reprendre trois écarts déjà repérés.** Vérifier le [catalogage du registre des gels](https://www.data.gouv.fr/datasets/registre-national-des-gels), la [disponibilité de décisions prud’homales](https://www.courdecassation.fr/toutes-les-actualites/2026/11/06/les-donnees-ouvertes-judiciaires-point-detape) et la distinction faite par l’[INPI entre professionnels assujettis et accès sur intérêt légitime](https://www.inpi.fr/faq?theme=58992). Confirmer séparément la disponibilité en pratique et le périmètre accessible.
5. **Compléter les lacunes LCB-FT.** Confronter le manifeste aux publications de l’ACPR, de Tracfin et de l’AMLA, ainsi qu’aux références propres aux trois professions. Pour chaque complément, indiquer l’usage envisagé et le statut du texte, notamment lorsqu’il s’agit d’un projet ou d’un texte à application future.

## Rapport attendu

Commencer par les corrections qui changent une décision d’intégration. Fournir ensuite un tableau traçable par page et ligne du PDF.

| Page et ligne | Affirmation du PDF | Verdict | Preuve et date | Conséquence pour Chrysalide |
| --- | --- | --- | --- | --- |
| Identifiant de la ligne | Affirmation vérifiée | Confirmée, partielle, à corriger ou non vérifiable | Source officielle, test effectué et résultat quand disponible | Correction proposée ou contrôle restant |

Terminer par l’ordre des premiers tests d’intégration, leurs dépendances et les inconnues qui exigent un accès ou une décision métier. La revue est terminée quand chaque ligne et chaque affirmation transverse importante ont un verdict, une preuve ou un motif précis d’incertitude. Les tests doivent être reproductibles et les déductions signalées comme telles.
