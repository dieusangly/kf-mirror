# Retrieval/RAG evaluation — 2026-10-19

The local functional demo passes. Retrieval quality remains below the pilot's
provisional target. [Setup and commands](retrieval-rag.md).

## Inputs and retrieval results

Five source editions produced 791 passages, 1,280 draft claims, 369 draft
summaries and 3,867 evidence projections. All 791 passage vectors were reused;
imports made no model calls. The evaluated snapshot is
`snapshot:6873e80ac3197d6e4ca7bc6504a1cd61446bd5f04e02cf4f5d05d40dc02f3d60`.
Its claim and summary branches share preparation manifest
`bc95df02b81f6f5e60fc99926973819fd3dd2cae36b6fe1a15e66d74e7fd827a`.

Eight draft pilot questions compared methods with the same snapshot, top five
passages and context budgets. A hit means the annotated source/page appeared in
non-context evidence admitted to the request; HTML cases match source identity.
These are location-overlap smoke metrics, not approved relevance or answer-support
labels. The provisional target is 7/8; no tuning used these questions.

| Navigation | Search | Hits / 8 | Median latency, cached queries |
| --- | --- | ---: | ---: |
| Passage baseline | Keyword | 2 | 0.372 s |
| Passage | Vector | 6 | 0.387 s |
| Passage | Combined | 3 | 0.499 s |
| Claim → passages | Keyword | 4 | 0.504 s |
| Summary → passages | Keyword | 2 | 0.574 s |

No method met 7/8. A2 and T1 were missed by all passage methods; combined search
performed below vector search. Keyword excerpts may not answer the question.
Claim/summary vector comparisons were not run: those projections have no vectors.
Final evaluation reused cached query embeddings. No upstream stage was rerun.

## Answers, citations and live usage

Two live A1 answers, one CLI and one streamed through the chat app, used
`gpt-6-luna` with vector retrieval. Each citation resolved exactly to edition
2025-04 of the ACPR–Tracfin guidelines, page 3, paragraph 1 (**1/1 per answer**).
Inspection supports the narrow nonbinding-status proposition. Semantic support
remains unscored; this smoke check is not independent domain review. Both answers
remain draft. End-to-end latencies were 4.076 s and 8.205 s.

| Live calls | Requests | Usage | Estimated USD |
| --- | ---: | --- | ---: |
| Query embeddings | 8 | 162 input tokens | 0.000003240 |
| Answers | 2 | 6,296 input; 109 output tokens | 0.000479675 |
| Total | 10 | Includes 3,145 cache-write and 3,145 cached-input tokens | 0.000482915 |

Costs use dated Standard rates, including cache writes, and are not invoices.
Detailed retrieval reports and answer audits are in the ignored
`artifacts/retrieval-evaluation/` directory. Source-preview QA replayed a recorded
answer without another model call.

## Validation and remaining work

- Full suite with real PostgreSQL/pgvector: **283 passed, 11 skipped, 80 subtests
  passed**, including all 24 retrieval/storage tests. Skips were existing optional
  pipeline checks. Model tests use deterministic fixtures.
- Coverage includes import idempotency/rollback, publication and snapshot isolation,
  lineage, vector compatibility, bounded context, citation rejection, insufficient
  evidence, session isolation, follow-up retrieval, history, reset and feedback.
- Playwright checked the chat app at 1440×1000 and 390×844; the Browser plugin was
  unavailable. Chat, citation previews, feedback, follow-ups, reload and reset
  passed without console/page errors. The provenance graph returned HTTP 200.
- Tested stack: Python 3.12, PostgreSQL 17, pgvector 0.8.2, Haystack 3.4.0 and
  Streamlit 1.66.0. Dependency, compilation and whitespace checks passed.

Next: approve evidence-level labels, investigate retrieval misses and combined
ranking on a separate reviewed set, and budget auxiliary-vector evaluation.
Production still requires authentication/access controls, concurrency protection,
retention/backups and deployment hardening.
