# Claim generation and classification

`original chunks → structural chunks → semantic clusters → claims → topics`

The build uses saved original chunks without changing parsing or chunking.
Generation extracts zero, one or multiple draft candidate claims per semantic
cluster. Classification assigns each candidate claim to a curated topic or leaves
it unclassified, preserving its text and exact evidence.

A **candidate claim** is a generated, evidence-linked proposition awaiting topic
classification. For compatibility, JSON fields such as `candidates` and
`candidate_count` count candidate claims; `candidate_id`, `candidate:` IDs,
`claim-candidates.jsonl` and `--candidates-dir` retain their existing names.

## Run

Load `OPENAI_API_KEY` through your shell or `direnv`; see [setup](ingestion.md).
From the repository root, choose fresh output directories:

```sh
PREPARATION_ID=20261016
RUN_ID=20261016-full-01
CHUNKED_DIR=artifacts/chunked-20261013-114244
CONTEXT_DIR="artifacts/structural-chunks-and-semantic-clusters-${PREPARATION_ID}"
CANDIDATES_DIR="artifacts/generated-claims-${RUN_ID}"
CLASSIFIED_DIR="artifacts/classified-claims-${RUN_ID}"

.venv/bin/python scripts/build_structural_chunks_and_semantic_clusters.py \
  --chunked-dir "$CHUNKED_DIR" --output-dir "$CONTEXT_DIR" \
  --cache-dir artifacts/embeddings-and-token-counts

.venv/bin/python scripts/generate_claims.py \
  --context-dir "$CONTEXT_DIR" --output-dir "$CANDIDATES_DIR"

.venv/bin/python scripts/classify_claims.py \
  --candidates-dir "$CANDIDATES_DIR" --output-dir "$CLASSIFIED_DIR"

# Human-review setup; the agent reviewer handles initialization automatically.
.venv/bin/python scripts/evaluate_claims.py \
  --candidates-dir "$CANDIDATES_DIR" --classified-dir "$CLASSIFIED_DIR" \
  --initialize-review
```

Preparation is shared across full and subset runs; its identifier is independent
of the generation/classification run ID. The current saved folders are:

| Scope | Directory |
| --- | --- |
| Full-corpus structural chunks and semantic clusters | `artifacts/structural-chunks-and-semantic-clusters-20261016` |
| Subset candidate claims | `artifacts/generated-claims-20261016-subset` |
| Subset classified claims and review | `artifacts/classified-claims-20261016-subset` |

Use `RUN_ID=20261016-subset` to review the saved subset. Full-run outputs retain
`20261016-full-01`. Folder renaming preserves artifact identities and saved reviews;
it requires no regeneration. Choose fresh identifiers for new builds/runs.

For one agent-review task, use the
[reviewer prompt](../src/knowledge_foundry/prompts/agent_prompt_claim_reviewer): it
initializes missing review files, inspects saved sources, and runs final evaluation.
It validates and reuses existing reviews. For a human reviewer, initialize with the
command above, let them record judgments, then evaluate with `--review-file`.
[Evaluation instructions](claim-evaluation.md) define acceptance.

Generation accepts `--subset-clusters N` for length-stratified selection or
`--cluster-numbers N ...` for explicit one-based final semantic clusters.
`--subset-batches` and `--batch-numbers` are compatibility aliases. Subsets are
selected **after building structural chunks and semantic clusters for the full input**, so they can require whole-input embedding
and request-counting calls. They do not guarantee source/topic coverage.

## Generation request format

Each request covers one final semantic cluster. Structural chunks contain readable
text in source order with evidence labels `[E1]`, `[E2]`, etc. The code assigns
these temporary labels within each request; they link passages to exact source
references and are not labels from the source documents or stable artifact IDs. A separate
`eligible_evidence` map holds exact spans, locations and original-chunk membership
without repeating text. Shared exact source passages appear once, with references
at subsequent occurrences; matching wording at distinct locations is retained.
`passage_ids` preserves membership and reading order. Additional `[C1]`, `[C2]`
context is explicitly non-citable. Only E labels can support claims.

## Building structural chunks and semantic clusters

[Configuration](../config/claim-generation-and-classification.json) groups settings under
`steps.structural_chunks`, `steps.embeddings`, `steps.semantic_clusters`,
`steps.claim_generation`, and `steps.topic_classification`. Generation and
classification each have their own model, reasoning, retries and version.
Generation's `budget` stays with its step; unknown settings fail.

`pricing` is a shared provider/model catalogue with one snapshot `version` and
per-model verification dates/URLs. Embedding and inference rates remain distinct.
Pricing-only edits do not invalidate embedding caches or generation checkpoints;
reports record the pricing snapshot used. Reuse the same frozen context when
resuming. Classification-only settings do not invalidate structural/semantic context.

| Setting | Default |
| --- | --- |
| Embeddings | OpenAI `text-embedding-3-small`, 1,536 dimensions |
| Embedding tokenizer | `cl100k_base`; 8,192 tokens per structural chunk |
| Embedding requests | At most 32 inputs / 32,000 tokens, 90 s timeout, two retries |
| Semantic clustering | Greedy all-members cosine similarity ≥0.80 |
| Generation | `gpt-6-luna`, medium reasoning |
| Context capacity | 1,050,000 tokens |
| Output/reasoning reserve | 32,768 tokens, sent as `max_output_tokens` |
| Safety margin / operational input cap | 4,096 / 24,000 tokens |

Limits were checked against the official [embedding guide](https://developers.openai.com/api/docs/guides/embeddings),
[embedding API](https://developers.openai.com/api/reference/resources/embeddings/methods/create),
[model limits](https://developers.openai.com/api/docs/models/gpt-6-luna), and
[token-counting guide](https://developers.openai.com/api/docs/guides/token-counting)
on 2026-10-16. Embedding pricing is separately configured at $0.02 per million input
tokens; see [provider pricing](https://developers.openai.com/api/docs/models/text-embedding-3-small).
Verify limits/tokenization before configuring another model.

Embedding requests batch separate inputs, producing one vector per structural
chunk. `max_input_tokens` limits each input; `max_request_tokens` limits their
combined token total. Both are separate from the claim-generation budget.

The default claim-generation input budget is:

```text
min(1,050,000 − 32,768 − 4,096, 24,000) = 24,000 tokens
```

The values are model context capacity, reserved output/reasoning allowance,
unused safety margin, and optional operational input cap, respectively. The
24,000-token cap is a conservative initial choice, not an evaluated optimum.

Assembly progress announces its start and resulting structural-chunk count.
`checking_individual_structural_chunks_fit_claim_generation` then reports successfully checked
chunks: each indivisible structural chunk must fit a complete generation request
before embedding calls begin. Embedding text limits are checked separately.
`checking_semantic_clusters_fit_claim_generation` reports full cluster-request
checks with `phase: initial` before refinement and `phase: final` afterward.
`splitting_oversized_semantic_clusters` announces each oversized cluster's split
start and completion, including its resulting cluster count. `completed` counts
finished checks, including those that found an oversized cluster; it does not
mean every checked cluster fits.

`short_context_limit_tokens` affects cost estimation only. Provider token counting includes all input-bearing request fields:
instructions, schema, metadata, structural text and eligible evidence. The [counting request](https://developers.openai.com/api/reference/python/resources/responses/subresources/input_tokens/methods/count)
omits `max_output_tokens`; the generation request retains that output and
reasoning allowance. Counts are cached by the full generation payload and counting
version. Permanent HTTP errors such as 400 fail immediately with provider details;
transient errors use bounded retries. Changing the counting version refreshes
cached counts without deleting saved embeddings. Offline fixtures use a labelled UTF-8 byte
estimate; they do not establish real model capacity.

Oversized semantic clusters are reclustered with thresholds increasing by 0.05,
for at most four attempts. If they do not shrink enough, deterministic
similarity-aware capacity partitioning enforces the limit. Structural chunks stay
indivisible. An oversized singleton fails with its ID and measured limit; inspect
its structural links or configure verified larger capacity. There is no splitting,
truncation, omission or legacy packing fallback. Retry feedback is budget-checked.

## Structure and similarity

Structural assembly stays within each document. Shared substantive element IDs and
explicit context references provide identity links. Ordered list introductions,
adjacent table continuations and unique same-page note markers provide separately
recorded heuristic links. Headings alone never join content. The bundle has no
universal list-parent or reliable footnote-attachment field; no sidecars are
silently discovered. Source review must check heuristic attachments and missed
boundaries.

Derived text follows source order and deduplicates only identical evidence spans,
retaining every original membership. Equal text at different locations remains.
Known upstream title/header defects remain unchanged.

Semantic clustering spans documents. Each structural chunk belongs to exactly one
final semantic cluster; every original chunk appears once in the full plan.
Chunks enter in stable ID order and join the eligible cluster with strongest
minimum cosine similarity, with stable ties. Similarity to **every** member blocks
unrestricted chaining. This greedy algorithm is deterministic but order-sensitive
and may create extra singletons. The 0.80 threshold needs source-reviewed calibration.
Similarity establishes potential relevance, not actor identity, agreement, scope
or effective dates. Missed relationships and duplicate claims remain possible.

## Artifacts, cache and resume

The build saves the original bundle, structural memberships and link reasons,
embedding vectors/cache provenance, final semantic clusters, diagnostics and an
integrity manifest. Generation copies this frozen build and writes candidate
claims, `candidate-clusters.json`, a report and manifest. Classification retains existing
bundle/JSONL/topic exports and provenance-graph compatibility.

Embedding cache keys include exact text, provider/model/dimensions and preprocessing
version; fixture identity is included for offline vectors. Each entry is validated
before reuse. Failures after output-directory creation leave `failure.json`.
Rerun a failed build into a **fresh** directory with the same cache to reuse
completed embeddings and counts.

Resume generation with the same command plus `--resume`. Inputs, frozen-build and
cluster-plan hashes, execution settings, prompt, schema, request format and
selection must match. Completed checkpoints are validated against their actual
semantic cluster and reused. Changed prompts, schemas, request formats or budget
assumptions require a fresh build and generation output; request counts refresh
and refinement may change cluster membership or numbering. Unchanged embeddings
can be reused.

Legacy candidate claim artifacts and frozen builds remain readable for
classification and evaluation. Compatibility names such as `compilation_scope`,
`compilation-review.json` and `compilation-evaluation-report.json` are retained;
commands do not rename saved files.

Report fields:

- `structural_chunk_original_size_distribution`: original chunks per structural
  chunk; `"2": 74` means 74 structural chunks each contain two originals.
- `cluster_structural_size_distribution`: structural chunks per final semantic
  cluster; `"2": 54` means 54 clusters each contain two structural chunks.
- `singletons`: final semantic clusters containing one structural chunk.
- `usable_input_tokens`: maximum complete claim-generation request input size.
- `claim_generation_request_token_counting`: provider counting requests, cached
  count reuses and failures used to check generation budgets and guide cluster
  partitioning. These calls count input tokens; they do not generate claims.
- `oversized_cluster_splits`: affected clusters and their final partitions;
  `claim_generation_input_budget_exceeded` identifies the split reason.

Reports separate embedding, claim-generation request counting, generation and
classification usage. Embedding text is counted locally with its tokenizer.
Older reports retain `token_counting` and split reason `generation_input_budget`;
existing saved artifacts remain readable and are never rewritten.
Cache hits are not new API calls. Unknown costs remain null; count-endpoint pricing
is not assumed. Subset projections report whole-input embedding cost separately
from projected generation/classification. Processing success and citation coverage
do not establish semantic completeness.

## Compare before full generation

1. Build structural chunks and semantic clusters for the full input; inspect cluster diagnostics and heuristic links against sources.
2. Select a small set of final semantic clusters covering multiple documents,
   singletons, lists, tables and footnotes. Generate/classify only that selection.
3. Compare with preserved prior outputs over the **same original chunk IDs**;
   record evidence outside that common scope separately.
4. Review atomicity, support, omitted propositions/qualifications, conflicts,
   duplicate claims and topic choices against saved sources. Record stage costs,
   including the full structural chunk and semantic cluster build, separately.
5. Apply existing review thresholds before scaling up. Fixture tests establish
   mechanics only; a subset pass is not a whole-corpus quality estimate.
