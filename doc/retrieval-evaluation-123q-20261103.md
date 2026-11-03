# Retrieval evaluation: 123 questions, 2026-11-03

**Recommendation: prefer `passage-and-claim` with vector search for this development benchmark, while retaining `claim` as a cheaper option.** Fusion improves measured grounding with essentially unchanged vector-search latency. The evidence does not justify a blanket replacement across search methods or a claim of better semantic answers.

## Evidence and comparability

Sources: saved [report](../artifacts/20261028-docling-aml-cft-01/retrieval-evaluation/123q-20261103/retrieval-report-rerank-four-modes.json), [checkpoint manifest](../artifacts/20261028-docling-aml-cft-01/retrieval-evaluation/123q-20261103/retrieval-report-rerank-four-modes.checkpoint/manifest.json), and [question dataset](../tests/fixtures/retrieval-evaluation-123q.json).

All 12 configurations contain the same 123 question IDs: **1,476 unique, completed results**, with no evaluation errors. Every report row matches its checkpoint row.

- Canonical dataset SHA-256 (parsed cases): `1eba04e2a4411c22cc1780a70cc84c9718125cec793c7bfb62ca2cf2ca84cb91`.
- Shared snapshot: `snapshot:02dd78fd1e2ca7ed4e9205f889a8c809b03865d0c674da3c86378f3265d15997`, corpus `aml-cft-pilot-13`.
- Shared settings: reranking enabled, 30 candidates, top five results, 12,000-token input budget, 48 evidence elements, context depth eight, and the same embedding configuration.
- All 1,409 actual reranking calls use `gpt-6-luna` and the same prompt hash. Other cases have no search candidates.
- Four evaluation workers; no resumed cases, conversation history, or query rewriting. Extractive checks enabled.

The configuration block records default method/mode values; the manifest and individual rows establish the actual 12 combinations.

**Anchor hits are mechanical text-and-location checks, not semantic answer correctness.** Retrieved anchors include governing context. Complete grounding requires every annotated grounding's locator and anchors. Extractive checks audit saved quotations and citations; generated answer quality is unscored.

## Retrieval quality and latency

Anchor percentages below apply equally to retrieved and extractive anchors: their outcomes are identical in every configuration. Complete-grounding counts also match between retrieved and extractive results.

| Search | Mode | Anchor hits /123 | Complete grounding /123 | Complete multi-document /10 | Mean / median / P95, seconds |
|---|---|---:|---:|---:|---:|
| Keyword | passage | 66 · 53.7% | 66 · 53.7% | 1 | 10.724 / 10.403 / 17.160 |
| Keyword | claim | 58 · 47.2% | 58 · 47.2% | 1 | 5.311 / 6.483 / 9.474 |
| Keyword | summary | 62 · 50.4% | 61 · 49.6% | 0 | 3.598 / 3.534 / 5.518 |
| Keyword | passage-and-claim | 81 · 65.9% | 81 · 65.9% | 2 | 10.531 / 9.915 / 17.472 |
| Vector | passage | 99 · 80.5% | 96 · 78.0% | 2 | 2.386 / 2.225 / 3.744 |
| Vector | claim | 104 · 84.6% | 100 · 81.3% | 3 | 2.443 / 2.299 / 3.603 |
| Vector | summary | 89 · 72.4% | 85 · 69.1% | 0 | 2.663 / 2.510 / 3.965 |
| Vector | passage-and-claim | **110 · 89.4%** | **108 · 87.8%** | 3 | **2.442 / 2.295 / 3.533** |
| Combined | passage | 100 · 81.3% | 97 · 78.9% | 2 | 9.975 / 9.661 / 15.046 |
| Combined | claim | 104 · 84.6% | 100 · 81.3% | 4 | 6.481 / 6.948 / 10.763 |
| Combined | summary | 92 · 74.8% | 87 · 70.7% | 0 | 4.361 / 4.176 / 6.600 |
| Combined | passage-and-claim | **112 · 91.1%** | **110 · 89.4%** | **5** | 12.302 / 11.636 / 19.158 |

There are **15 questions with multiple groundings, but only ten span distinct documents**. Vector fusion completes the same three multi-document questions as vector claim; combined fusion adds V2-123 to combined claim's four.

Fusion covers **86/138, 121/138, and 123/138 individual groundings** under keyword, vector, and combined search, respectively. Corresponding claim counts are 62, 113, and 114; passage counts are 71, 110, and 110. One additional retrieved grounding is lost during extraction for vector summary, despite unchanged complete-question counts.

Latency measures retrieval through context assembly, excluding subsequent extractive auditing. P95 uses the evaluator's nearest-rank convention. These are observations from one concurrent run, not isolated service benchmarks.

## Reranking usage and estimated cost

Tokens are totals across 123 questions. Cached reads and cache writes are subsets of input tokens.

| Search | Mode | Input tokens | Cached reads | Cache writes | Output tokens | Estimated USD |
|---|---|---:|---:|---:|---:|---:|
| Keyword | passage | 852,436 | 0 | 843,175 | 3,862 | $0.10825 |
| Keyword | claim | 216,847 | 0 | 204,124 | 1,746 | $0.02766 |
| Keyword | summary | 679,325 | 0 | 672,665 | 4,888 | $0.08719 |
| Keyword | passage-and-claim | 863,930 | 235,804 | 620,197 | 3,699 | $0.08253 |
| Vector | passage | 1,015,856 | 0 | 1,015,487 | 2,549 | $0.12825 |
| Vector | claim | 570,883 | 0 | 570,514 | 3,361 | $0.07303 |
| Vector | summary | 981,073 | 0 | 980,704 | 4,327 | $0.12479 |
| Vector | passage-and-claim | 818,082 | 0 | 817,713 | 3,084 | $0.10379 |
| Combined | passage | 1,134,235 | 49,494 | 1,084,372 | 2,836 | $0.13750 |
| Combined | claim | 598,053 | 268,694 | 328,990 | 4,069 | $0.04588 |
| Combined | summary | 1,052,011 | 74,580 | 977,062 | 3,658 | $0.12474 |
| Combined | passage-and-claim | 1,007,205 | 39,993 | 966,843 | 3,127 | $0.12286 |

These reproduce the artifact's dated rate card: **$0.10/M ordinary input, $0.01/M cached input, $0.125/M cache writes, $0.50/M output**. They are historical estimates, not independently verified current prices or invoices.

Recorded embedding cost is zero; query embeddings were served without billed input. No answer-generation cost is included. Total recorded retrieval cost is **$1.16647**.

Caching materially affects comparisons: combined claim has substantially more cached input than combined fusion. Its unusually low cost should not be treated as an intrinsic property of combined search.

## Paired gains, regressions, and efficiency

| Fusion versus baseline | Anchor gains / losses | Complete-grounding gains / losses |
|---|---:|---:|
| Keyword claim | 23 / 0 | 23 / 0 |
| Keyword passage | 15 / 0 | 15 / 0 |
| Vector claim | 11 / 5 | 11 / 3 |
| Vector passage | 11 / 0 | 12 / 0 |
| Combined claim | 12 / 4 | 13 / 3 |
| Combined passage | 14 / 2 | 15 / 2 |

Question IDs below omit the `V2-` prefix.

- **Vector fusion versus claim:** anchor gains on 012, 013, 016–018, 020, 027, 038, 060, 072, 078; losses on **035, 049, 056, 120, 123**. Only 035, 049, 056 are complete-grounding regressions: claim already failed complete grounding on 120 and 123.
- **Combined fusion versus claim:** anchor gains on 012, 013, 016–020, 023, 027, 038, 060, 072; losses on **011, 026, 049, 120**. Complete grounding additionally gains 123; 120 was already incomplete.
- Combined fusion loses **078 and 092** relative to passage. Vector and keyword fusion lose no passage anchor successes.

Vector fusion offers the strongest practical trade-off: **+4.9 percentage points in anchors and +6.5 points in complete grounding versus claim**, essentially equal measured latency, and **42.1% higher cost**—about **$0.84 versus $0.59 per 1,000 queries** at these recorded rates.

Combined fusion buys only two net additional anchor/complete-grounding successes over vector fusion, while taking **5.0× mean latency** and costing **18.4% more**. Its multi-document result is better, but six anchor gains accompany four losses. Keyword fusion improves recall substantially, yet remains slower and less effective than vector fusion.

## Candidate and context constraints

The [retrieval implementation](../src/knowledge_foundry/retrieval/pipeline.py) alternates passage and claim candidates into **one shared 30-record pool**, typically 15 per branch. It does not union both standalone final result sets. Claims and passages sharing evidence can occupy separate slots; deduplication occurs later.

Observed ranking-budget exclusions, counted as questions with at least one candidate excluded:

| Search | passage | claim | summary | passage-and-claim |
|---|---:|---:|---:|---:|
| Keyword | 49 | 0 | 28 | 49 |
| Vector | 22 | 0 | 8 | 3 |
| Combined | 76 | 0 | 26 | 47 |

Combined fusion admits an average **28.35/30** candidates, versus **30/30** for claim. Vector fusion admits **29.97/30**. The ranker selects only about **2.41 vector-fusion** or **2.24 combined-fusion** records per question, despite the five-result allowance.

Final context shows seven questions with evidence-count exclusions across all configurations, and **no recorded token- or depth-based context exclusions**. Ranking input limits are therefore the more frequent observed budget constraint. Summary resolution also frequently discards supporting passages at the five-passage cap: 45/78/74 questions across keyword/vector/combined.

## Failure analysis

Observed failures:

- **Missed by every configuration:** V2-052, V2-088, V2-115 fail the top-level locator and anchor checks. V2-115 still retrieves its second document under some configurations, so it is not a total evidence failure.
- **Never completely grounded:** additionally V2-116, V2-118, V2-120, V2-121. These expose missing counterpart documents or required details despite partial hits.
- **Candidate displacement:** baseline-selected records disappear from fusion's candidate pool for vector V2-035 and combined V2-078/092.
- **Budget exclusion:** combined V2-026 contains a baseline-selected claim at candidate position 30, but excludes it from the reranking prompt.
- **Selection differences:** relevant baseline-selected claims are admitted but not selected for vector V2-056 and combined V2-049.

These traces identify where baseline-successful records are lost; they do not prove those records were the sole route to the required evidence.

**Hypotheses:** cross-language terminology, competing provisions, asymmetric retrieval of compound questions, and duplicated navigation evidence may contribute. The saved IDs alone cannot establish whether every universally missed target was absent from candidates or rejected later.

## Decision

Support a **provisional vector-fusion preference** for aggregate development-set grounding. Retain claim for lower-cost use and preserve the identified regressions as review cases. Combined fusion is justified only when its modest additional coverage warrants its latency. A production-wide replacement remains unsupported without semantic answer assessment and independent validation.
