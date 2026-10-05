# Banking and AML/CFT corpus catalog

**Checked:** 1 October 2026. This catalog separates source documents from labeled benchmarks. Language and jurisdiction matter: an English or Belgian benchmark does not validate answers about French AML/CFT obligations. For each collection, record the downloaded version, publication date, and reuse terms before ingestion.

## French-language sources

| Resource | Jurisdiction; material | Useful for | Reuse note |
| --- | --- | --- | --- |
| [ACPR–Tracfin joint guidelines](https://acpr.banque-france.fr/system/files/2025-04/20250423_Lignes_directrices_obligations_Tracfin.pdf) | France; supervisory PDF on vigilance and suspicious-activity reporting | Core pilot corpus; obligation extraction and cited Q&A | Public document; verify reuse terms. April 2025 edition. |
| [Tracfin publications](https://www.economie.gouv.fr/tracfin/les-publications-de-tracfin) and [2024–25 threat report](https://www.economie.gouv.fr/tracfin/lcb-ft-etat-de-la-menace-2024-2025) | France; reports, typologies, alert indicators | Typology extraction and source-grounded summaries | Public documents; verify reuse terms and report year. |
| [Code monétaire et financier, L.561](https://www.legifrance.gouv.fr/codes/section_lc/LEGITEXT000006072026/LEGISCTA000006154830/) | France; AML/CFT legislation | Article-level retrieval, obligations, version tracking | Use a dated legal snapshot; verify reuse terms. |
| [AMF DOC-2019-16](https://www.amf-france.org/fr/reglementation/doctrine/DOC-2019-16) | France; guidance on customer and beneficial-owner due diligence | Sector-specific comparison | Public document; verify reuse terms and current edition. |
| [FATF/GAFI Recommendations](https://www.fatf-gafi.org/fr/publications/Recommandationsgafi/Recommandations-gafi.html) and [France evaluation](https://www.fatf-gafi.org/fr/countries/detail/france.html) | International / France; standards and evaluation reports in French | Cross-jurisdiction terminology and policy context | Standards are not French law; verify reuse terms and edition. |
| [BSARD](https://huggingface.co/datasets/maastrichtlawtech/bsard) | Belgium; French legal articles and 1,100+ expert-labeled questions | French legal retrieval baseline | Dataset: **CC BY-NC-SA 4.0**; non-commercial restriction. Not AML-specific. |

## English-language document benchmarks and sources

| Resource | Jurisdiction; material | Useful for | Reuse note |
| --- | --- | --- | --- |
| [ObliQA](https://github.com/RegNLP/ObliQADataset) | ADGM/UAE; structured financial regulations and 27,869 synthetic question–passage pairs | Multi-passage regulatory retrieval and Q&A | Dataset license needs confirmation; synthetic labels and foreign law limit transfer. |
| [SARSum](https://research.feedzai.com/publication/sarsum-a-relevance-and-comprehensiveness-aware-abstractive-summarization-dataset-for-suspicious-activity-reports/) ([data](https://www.kaggle.com/datasets/leonardovalves/sarsum)) | AML; 2,000 synthetic analyst-note sets with key facts and summary variants | Summary completeness and relevance | Confirm dataset terms before use; synthetic, not real reports. |
| [FinCEN advisories](https://www.fincen.gov/resources/advisoriesbulletinsfact-sheets) | US; AML/CFT alerts, red flags, and guidance | Typology extraction and ingestion tests | Public documents; verify reuse terms and advisory status. |
| [FinanceBench](https://github.com/patronus-ai/financebench) | US; public-company filings and 150 open, evidence-annotated Q&A cases | Financial-document citation and Q&A evaluation | Open sample only; not AML-specific. Verify dataset/PDF terms. |
| [FinQA](https://github.com/czyssrs/FinQA) | Financial reports; text, tables, questions, and reasoning programs | Table parsing and numerical reasoning | Not AML-specific; verify dataset terms. |

## Transaction datasets (separate evaluation track)

| Resource | Material | Useful for | Reuse note |
| --- | --- | --- | --- |
| [IBM AML-Data](https://github.com/IBM/AML-Data) | Synthetic, labeled transaction CSVs | Transaction/graph detection experiments | Data: **CDLA-Sharing-1.0**; repository code: Apache-2.0. |
| [IBM AMLSim](https://github.com/IBM/AMLSim) | Simulator of synthetic transactions and laundering patterns | Generating transaction scenarios | Simulator, not a document corpus; check output and code terms. |

## Pilot selection

Start with a dated French corpus from ACPR–Tracfin, Tracfin, Légifrance, and the AMF. Create a small expert-checked set of French questions with exact supporting passages, plus a few typology and obligation-extraction examples. Among the resources reviewed here, none provides an annotated benchmark specifically for **French AML/CFT document understanding**. Use BSARD to test French legal retrieval mechanics and ObliQA/SARSum for task-specific methods, while keeping their jurisdiction and synthetic-data limits explicit. Keep transaction datasets out of the document-ingestion benchmark.
