# RAG validation — 2026-10-21

[Setup and commands](retrieval-rag.md). Published snapshot:
`snapshot:cddfb128404b5ca71cd905d9ddf2dc5ef11f9f172febe2b3f3c99daa119b21f5`.
It contains vectors for **791 passages, 1,280 claims and 369 summaries** (1536
dimensions, `text-embedding-3-small`). The importer reused 791 passage and 365
summary vectors and embedded the remaining 1,284 records. Frozen upstream files
and earlier snapshots were preserved.

## Search comparison

The [original eight questions](french-aml-cft-pilot-sources-and-evaluation.md) have
**draft annotations, not reviewed ground truth**. Four additional cases cover risk
scope, updates, representatives and digital documents. These twelve development
cases are not independent acceptance tests.

All methods used the same snapshot, budgets, top five results and candidate pool
of 30, without model reranking. Cells show **source/location hits; complete text
anchor hits**, each out of twelve. Anchors measure retrieval, not answer support.

| Retrieve via | Keyword | Vector | Combined |
| --- | --- | --- | --- |
| Passage baseline | 8; 7 | 9; 5 | 9; 8 |
| Claim → passages | 7; 6 | 10; 8 | 8; 7 |
| Summary → passages | 6; 5 | 8; 7 | 10; 8 |

**108/108 checks completed**, using cached query embeddings and no model calls.
All 1,501 deterministic excerpt citation instances resolve exactly to saved text
and locations. This does not establish semantic relevance or answer completeness.
Navigation vectors help some cases and miss others; passage retrieval remains
the default. Summaries never become verified evidence.

Historical comparison (2026-10-20, previous snapshot): combined passage search
with model reranking reached anchors in 12/12 development cases. That result
used recorded ranking selections and is not a fresh 12-case reranker benchmark
for this revision. Historical reports remain in `generic-*.json`.

## Live answer checks

Bounded checks used the shared service and the new snapshot:

- French and English corpus-inventory questions returned all five documents from
  snapshot metadata, without passage search or fabricated citations. The offline
  **Documents in corpus** button uses the same metadata without a model call.
- Case 16 included all four source steps, with 4/4 exact citations. A conversational
  follow-up retrieved its alert criteria again, resolving the reference to case 16.
- Additional checks covered binding status, information updates, representatives,
  and required identity-verification documents. A question about required documents
  correctly followed evidence retrieval rather than the inventory route.
- Claim and summary combined-search checks answered the binding-status question
  using original passage citations, not generated propositions or summaries.
- An unrelated recipe question and nonexistent case 99999 returned insufficient
  evidence. These two probes do not establish general rejection accuracy.
- An initial updates answer applied an AMF qualification without clearly naming
  its authority. A generic source-attribution instruction was added; one recheck
  reused the saved evidence and named ACPR–Tracfin and AMF separately.

All **28/28 citation instances** in generated checks, including that recheck,
resolve exactly. The narrow propositions were inspected against their cited
text. Semantic support is not automatically scored; answers remain drafts.
Active prompts: `rag-answer-v1`, `rag-query-v2`, `rag-rank-v1`; reports retain
prompt hashes to distinguish the scope correction.

## Validation and usage

- Full suite, real PostgreSQL/pgvector: **394 passed, 11 skipped, 80 subtests passed**.
  Ten skips require older parsed/chunked pilot directories; OCR integration is
  opt-in. Current frozen import artifacts were validated separately.
- Command audit checks documented script options against executable CLIs. A
  deterministic Markdown/DOCX/HTML fixture runs ingestion through parsing,
  review-gate mechanics, structural build, claims, recursive summaries, graph,
  vector import/publication, queries, evaluation and rendered chat. It verifies
  saved-vector reuse, missing claim/final-summary vectors, idempotency and unchanged
  frozen files. Native parsing-location and unknown-cost CLI failures were fixed;
  retrieval execution errors now exit 1. No paid calls or pilot rebuild were added.
- Coverage includes import idempotency, publication/snapshot isolation, compatible
  vector reuse, all three navigation modes, exact lineage, filters, atomic context
  budgets, private histories, follow-ups, feedback and rendered Streamlit flows.
- Playwright/Chrome at `http://127.0.0.1:8503`, desktop 1440×1000 and mobile
  390×844: page/content, inventory, graph rendering, record details, summary view,
  feedback and cached claim/summary vector queries passed. Screenshots inspected;
  no application errors or framework overlays. Streamlit's iframe produces nine
  browser capability/sandbox warnings; only trusted configured HTML is embedded.
  Browser plugin unavailable; Playwright was used. Browser checks made no model calls.
- `pip check`, compilation and `git diff --check` passed.

This revision used **31 Responses calls** (12 routing/rewrites, ten rankings,
nine answer calls including the scope recheck): 110,609 input, 2,004 output and
106,777 cache-write tokens, estimated **USD 0.014732325**. Indexing used 41 embedding
requests (91,682 tokens, USD 0.00183364); four query embedding requests added 94
tokens (USD 0.00000188). Total estimate: **USD 0.016567845**, under the configured
2026-10-19 rate cards; not an invoice. Offline comparisons reused caches.

Local reports: ignored `artifacts/retrieval-evaluation/search-20261021.json` and
`live-20261021.json`; older reports retain their historical results. The earlier
2026-10-20 revision used 21 Responses calls (USD 0.015760075), one 101-token
embedding request (USD 0.00000202), plus five separately recorded diagnostic
answer calls (USD 0.002002125).

## Remaining limits

Candidate pools and budgets can exclude useful evidence. Source-excerpt mode has
no semantic reranker or conversational rewrite. Routing and generation can fail;
use explicit source filters and inspect original editions and qualifications.
Exact citations do not prove every assertion. New corpora need reviewed questions,
negative cases and independent answer-support evaluation. Production still needs
authentication, access control, concurrency protection, backups and deployment
hardening. No upstream pilot was regenerated.
