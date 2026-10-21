# RAG validation — 2026-10-20

[Setup and commands](retrieval-rag.md). Results use published snapshot
`snapshot:6873e80ac3197d6e4ca7bc6504a1cd61446bd5f04e02cf4f5d05d40dc02f3d60`.
Frozen pipeline artifacts and passage vectors were reused; no upstream rebuild,
reimport or migration was needed for these changes.

## Scope and findings

The [original eight questions](french-aml-cft-pilot-sources-and-evaluation.md) have
**draft annotations, not reviewed ground truth**. Four additional questions cover
risk-management scope, information updates, representatives and digital documents
([fixture](../tests/fixtures/retrieval-additional-smoke.json)). Their text anchors
were recorded from saved passages before running retrieval. All twelve are now
development smoke cases; none constitute independent acceptance testing.

Search alone sometimes preferred generic reporting material or another numbered
case. A bounded model reranker now selects relevant candidates without language
patterns, source aliases or identifier-specific filters. A list split across
structural passages also lost its final clause; context now completes bounded
lists through saved direct governing relationships. Headings consistently supply
scope; body passages supply citations. Active answer prompt: `rag-answer-v2`.

## Measured retrieval

Top five results, candidate pool 30, identical snapshot/budgets. Cells show
**locator hits / cases; complete text-anchor hits / cases**. Anchor presence is
separate from semantic answer support. All comparisons used cached query vectors.

| Passage retrieval | Original eight | Four additional |
| --- | --- | --- |
| Keyword, no reranker | 4/8; 3/8 | 4/4; 4/4 |
| Vector, no reranker | 6/8; 5/8 | 3/4; 0/4 |
| Combined, no reranker | 6/8; 5/8 | 3/4; 3/4 |
| Combined + model reranker | 8/8; 8/8 | 4/4; 4/4 |

The reranker was called live once per question. Final context checks replayed
those recorded selections after completing cross-passage lists. Deterministic
excerpt anchors matched the final retrieval anchors in every row. This measures
these cases only; it does not establish reliability on arbitrary questions.
Live median retrieval latency, including reranking: **2.36 s** for the original
set and **3.69 s** for the additional set. One ranking request admitted 28/30
complete candidates because of its input budget; the others admitted all 30.

Optional navigation, keyword without reranking: claims → passages reached 3/8
locators and 2/8 complete anchors; summaries → passages reached 3/8 for both.
Claim/summary vectors were unavailable, so their vector retrieval was not run.

## Answers, generality and usage

Final generated checks covered case 16, information updates and representatives.
Case 16 includes all four steps; its **4/4 citations resolve exactly**. The other
two answers each resolve **3/3 citations**. Their narrow propositions were
inspected against the saved text; broader semantic entailment remains unscored.
A diagnostic answer before list completion omitted the final step. Earlier
unranked checks also missed relevant evidence; those results are retained in the
ignored reports rather than described as successful answers.

Two live rewrite checks separated a standalone topic change from a French
follow-up. Automated tests additionally cover new validated equipment,
horticulture and transport documents in English, French and German through all
three search methods, ranking and citation resolution. Fixture vectors and model
responses are deterministic oracles: these tests prove mechanics, not real-model
semantic quality across those languages. Japanese follow-up rewriting is also
fixture-tested. Unsupported questions, unknown citation IDs, missing evidence,
rewrite failures and request budgets have deterministic coverage.

This revision used **21 bounded Responses calls** (12 rankings, two rewrites,
seven answer checks): 121,898 input, 1,069 output and 121,431 cache-write tokens;
estimated **USD 0.015760075** under the configured 2026-10-19 rate card.
One embedding request covered four new questions: **101 tokens, USD 0.00000202**.
All other embeddings were cached. Earlier diagnostic work used five answer calls
(USD 0.002002125), separately recorded. Estimates are not invoices.
Reports: ignored `artifacts/retrieval-evaluation/generic-*.json` and earlier
`regressions-*.json`; the latter describe superseded implementations.

## Validation and remaining limits

- Full suite with real PostgreSQL/pgvector: **330 passed, 11 skipped, 80 subtests passed**.
- Coverage includes idempotent imports, snapshot/session isolation, original lineage,
  source filters, list completion, complete budgets, fail-closed citations, model
  fixtures, offline fallback and rendered Streamlit interactions.
- Playwright/Chrome at `http://127.0.0.1:8503`, desktop 1440×1000 and mobile
  390×844: page identity, content, no framework overlay, citation links, feedback,
  reset and history restoration passed; no console errors or warnings. Screenshots
  were inspected. Browser plugin unavailable; recorded model responses and cached
  embeddings kept browser QA free of external model calls.
- Compilation and `git diff --check` passed. No commit was created.
- Generated answers remain drafts. Exact citations do not prove that their text
  supports every assertion. The reranker can miss evidence outside its candidate
  pool/budget; unranked/offline retrieval has the measured gaps above.
- New corpora need reviewed questions, negative cases and independent answer-support
  assessment. Production authentication, access control and deployment hardening
  remain outside this local demo.
