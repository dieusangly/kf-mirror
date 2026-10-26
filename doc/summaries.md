# Recursive summaries

Summaries compress semantic clusters independently of claims.

## Models and inputs

Configured in [`config/summaries.json`](../config/summaries.json):

| Task | Model | Input |
| --- | --- | --- |
| Write level-one summaries | `gpt-6-luna` | All structural chunk texts in one semantic cluster, evidence references, document metadata and separate non-citable parent context |
| Write higher-level summaries | `gpt-6-luna` | Selected earlier summaries' IDs, levels and text |
| Group earlier summaries | `text-embedding-3-small` | Summary text, converted into vectors for similarity comparisons |

Higher levels read earlier summaries, not original chunks. Provenance is preserved
by the pipeline; [instructions](../src/knowledge_foundry/prompts/summary_generation_v1.txt)
require scope, conditions, exceptions and uncertainty.

## How levels grow and when they stop

1. Write one level-one summary for each semantic cluster.
2. Group related summaries and write one new summary for each group.
3. Repeat using the new summaries and any summaries that could not be grouped.

A group needs at least two summaries. Every pair must meet the similarity
threshold (default **0.80**), the group must have at most **eight** members, and
its complete LLM request must fit the token budget. An ungrouped summary is kept
unchanged; it does not get a duplicate at the next level.

Stop with one root, no eligible groups or the level limit (default four;
`summary_clusters.max_levels` accepts 1–20). Ungrouped lower-level summaries remain
roots; the highest level alone need not cover the corpus.

Saved run 20261019 produced 321/33/11/4 summaries at levels 1–4 and stopped at the
limit with 270 roots across levels. `root_summary_ids` lists roots; `stop_reason`
is `single_root`, `no_eligible_merges` or `max_levels`.

## Run and saved outputs

After the [structural chunk and semantic cluster build](structural-chunks-and-semantic-clusters.md), set its
completed run ID and a fresh summary run ID:

```sh
CONTEXT_RUN_ID="your-completed-structural-chunk-and-semantic-cluster-run-id"
RUN_ID="your-new-summary-run-id"
CONTEXT_DIR="artifacts/${CONTEXT_RUN_ID}/structural-chunks-and-semantic-clusters"

direnv exec . .venv/bin/python scripts/generate_summaries.py \
  --context-dir "$CONTEXT_DIR" --output-dir "artifacts/${RUN_ID}/summary-generation" \
  --config config/summaries.json --cache-dir artifacts/summary-cache
```

Exit 0 means complete; exit 2 means failure. Inputs, outputs and mutable caches
must be separate. Retry into a fresh output directory with the same response
cache; there is no summary `--resume` flag.

Requests are counted in full, without truncating source text. The default input
cap is 32,000 tokens with 4,096 tokens reserved for output. An oversized level-one
cluster fails: adjust the summary budget or rebuild the shared cluster plan.
`--fixture` provides offline summaries and embedding vectors for mechanical tests.

| Artifact | Contents |
| --- | --- |
| `summaries.jsonl` | Text, level, direct inputs, original chunk/document/semantic-cluster IDs, evidence and draft status |
| `summary-clusters.json` | Higher-level groups and their similarity |
| `summary-embeddings.jsonl` | Vectors and text/provider hashes |
| `preparation/` | Frozen structural chunk and semantic cluster build outputs |
| `summary-prompt.txt`, `model-output-schema.json`, `build-manifest.json` | Frozen prompt, schema, configuration and integrity hashes |

[Summary IDs](../schemas/summary-artifact-schema-v1.json) are content-addressed;
changed text or generation/provenance metadata changes identity. `load_summaries()`
checks hashes, membership, budgets and transitive provenance offline. Frozen v1/v2
structural chunks and semantic clusters remain readable; structural-v3 builds
supply the improved hierarchy.

For RAG, import these summaries with the same frozen structural build and
`--embed-missing`: grouping vectors do not cover every final summary. The importer
reuses compatible saved vectors and fills gaps, including claim vectors. Continue
with [indexing and publication](manual-workflow.md#7-import-vectors-into-an-unpublished-snapshot).

Summaries remain drafts: provenance identifies their inputs, not proof of every
sentence. Compression errors can accumulate across levels. There is currently no
independent summary-fidelity review gate. See [downstream uses](downstream-uses.md).
