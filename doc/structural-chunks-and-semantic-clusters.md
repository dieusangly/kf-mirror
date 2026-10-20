# Structural chunk and semantic cluster build

`reviewed original chunks → structural chunks → semantic clusters`

The structural chunk and semantic cluster build feeds [claims](claim-generation-and-classification.md)
and [summaries](summaries.md) independently. Neither branch requires the other.

## Run

Load `OPENAI_API_KEY`; see [setup](ingestion.md). From the repository root, set a
reviewed input run and a fresh build run:

```sh
INPUT_RUN_ID="your-reviewed-chunking-run-id"
CONTEXT_RUN_ID="your-new-build-run-id"
CHUNKED_DIR="artifacts/chunked-${INPUT_RUN_ID}"
PARSED_DIR="artifacts/parsed-${INPUT_RUN_ID}"
CONTEXT_DIR="artifacts/structural-chunks-and-semantic-clusters-${CONTEXT_RUN_ID}"

.venv/bin/python scripts/build_structural_chunks_and_semantic_clusters.py \
  --chunked-dir "$CHUNKED_DIR" --parsed-dir "$PARSED_DIR" \
  --output-dir "$CONTEXT_DIR" --cache-dir artifacts/embeddings-and-token-counts
```

Exit 0 means a complete, validated build; exit 2 means failure. Validation
checks hierarchy, lineage, unique membership and request budgets. There is no
separate structural agent-review stage; pilot source checks remain regression tests.
Pass the resulting `CONTEXT_DIR` to either output branch.

## Structure and provenance

Structural chunks preserve [explicit document hierarchy](chunking.md#structural-assembly-after-original-chunking)
and keep original chunks focused. Bounded adjacent fragments and supported
sentence/table continuations may join. Section/list parents, repeated context
and same-page note hints are non-merging relationships. Ancestor and note text
is supplied as non-citable context. Only identical evidence spans are deduplicated;
repeated wording at distinct locations remains. Heuristic links require source review.

Semantic clusters can span documents. Each original/structural chunk belongs to
one final cluster. Greedy grouping requires similarity to every member; it is
deterministic but order-sensitive. Similarity does not establish actor identity,
agreement, legal scope or effective dates.

New builds use `structural-chunks-v3`, `compilation-v13` and manifest/schema
v2. For existing chunking runs without `document-structure.json`, add
`--parsed-dir artifacts/parsed-YOUR_RUN` to the build command to snapshot the matching
`parse.json` sidecars. With neither metadata source, source-flow/numbering fallback
is explicit in the report and cannot recover all heading depths.
Use a fresh output directory: structural IDs, semantic memberships
and claim request hashes change. Original chunk IDs and bundle schemas remain
unchanged. Frozen v1/v2 outputs and candidate artifacts remain readable and
evaluable with their original algorithm. New generation requires a fresh v3
build. Existing classified claim views remain supported.

## Configuration and budgets

[`config/structural-chunks-and-semantic-clusters.json`](../config/structural-chunks-and-semantic-clusters.json)
owns structural assembly, embeddings and cluster settings. Select another file with
`--config /path/to/build.json`.

`request_sizing.claim_config` explicitly selects the claim-generation model and
budget used to count complete requests with the claim prompt/schema. That claim
config points back through `structural_build_config`; both references must match
and resolve relative to their containing file. The shared pricing catalogue stays
in the claim config. Summaries consume the frozen plan and check their own budgets.

Frozen manifests contain resolved settings, not file references. Existing combined
configs remain supported; unchanged settings preserve request identities and caches.

| Setting | Default |
| --- | --- |
| Embeddings | `text-embedding-3-small`, 1,536 dimensions, `cl100k_base` |
| Embedding limits | 8,192 tokens/input; 32 inputs and 32,000 tokens/request |
| Clustering | Every-member cosine similarity ≥0.80 |
| Claim-generation input cap | 32,000 tokens, including instructions, schema, metadata and evidence |

Exact provider counts check individual structural chunks before embeddings and
complete cluster requests afterward. Oversized clusters are refined/partitioned;
structural chunks remain indivisible and evidence is never truncated. An oversized
singleton requires inspecting its links or verifying a larger budget, then rebuilding.
The operational cap is not an evaluated optimum. Verify limits before changing models.

## Outputs and reruns

The build freezes the original bundle, hierarchy, structural memberships/links,
embeddings, cluster plan, report and integrity manifest. See
[artifact contracts](artifact-schemas.md). Preserve it for downstream generation and resume.

Reports separate embedding and token-counting usage. `duration_seconds` covers the
current invocation; distribution fields count members per group, and `singletons`
counts one-member clusters. `usable_input_tokens` records the request cap;
`claim_generation_request_token_counting` records counting/cache activity;
`oversized_cluster_splits` records refinements. Cache hits are not new API calls;
unknown costs stay null.

Embedding caches validate hashes and require matching text, model and settings.
A failed build leaves `failure.json`. Inspect saved outputs before rebuilding;
use a fresh output directory and reuse the same cache. The build has no resume flag.
Pricing-only changes preserve caches; classification-only changes preserve the build outputs.

Parser/chunker changes require fresh parsing, chunking, source reviews and downstream
runs. Claim-generation prompt/schema/request/budget changes require a fresh build
and generation; matching embeddings remain reusable. Budget-only changes do not
require repeating parsing/chunking reviews. Summary configuration changes follow
[summary-specific limits](summaries.md#run-and-saved-outputs).
