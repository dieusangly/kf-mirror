# Recursive summaries

Semantic clusters feed two independent outputs: **claims** and **summaries**.
Claims express atomic propositions; summaries compress related source material.
Summaries never consume claims.

## Models and inputs

Configured in [`config/summaries.json`](../config/summaries.json):

| Task | Model | Input |
| --- | --- | --- |
| Write level-one summaries | `gpt-6-luna` | All structural chunk texts in one semantic cluster, evidence references, document metadata and separate non-citable parent context |
| Write higher-level summaries | `gpt-6-luna` | Selected earlier summaries' IDs, levels and text |
| Group earlier summaries | `text-embedding-3-small` | Summary text, converted into vectors for similarity comparisons |

The embedding model groups text; the LLM writes summaries. Higher levels do not
reread original chunks. The pipeline preserves source provenance independently
of the LLM. [Generation instructions](../src/knowledge_foundry/prompts/summary_generation_v1.txt)
require scope, conditions, exceptions and uncertainty to be retained.

## How levels grow and when they stop

1. Write one level-one summary for each semantic cluster.
2. Group related summaries and write one new summary for each group.
3. Repeat using the new summaries and any summaries that could not be grouped.

A group needs at least two summaries. Every pair must meet the similarity
threshold (default **0.80**), the group must have at most **eight** members, and
its complete LLM request must fit the token budget. An ungrouped summary is kept
unchanged; it does not get a duplicate at the next level.

Stop as soon as any of these is true:

- **One summary remains:** everything has been combined into one final summary.
- **Nothing can be grouped:** no group meets the similarity and size limits.
- **The level limit is reached:** default **four levels**, even if more grouping
  might be possible. Set `summary_clusters.max_levels` to change this limit (1–20).

For example, if five summaries form two pairs and one stands alone, the next
round has two new summaries plus the unchanged one: **three summaries to consider**.
The process does not force unrelated material into one summary.

Run 20261019 stopped because it reached level four, not because grouping was
exhausted. It created 321 / 33 / 11 / 4 summaries at levels 1 / 2 / 3 / 4.
Its final set contains **270 summaries across those levels**; the four level-four
summaries cover only part of the corpus. `root_summary_ids` lists that final set;
`stop_reason` is `single_root`, `no_eligible_merges` or `max_levels`.

## Run and saved outputs

After successful structural/semantic preparation:

```sh
.venv/bin/python scripts/generate_summaries.py \
  --context-dir "$CONTEXT_DIR" --output-dir "artifacts/summaries-${RUN_ID}" \
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
| `preparation/` | Unchanged input preparation |
| `summary-prompt.txt`, `model-output-schema.json`, `build-manifest.json` | Frozen prompt, schema, configuration and integrity hashes |

[Summary IDs](../schemas/summary-artifact-schema-v1.json) are content-addressed;
changed text or generation/provenance metadata changes identity. `load_summaries()`
checks hashes, membership, budgets and transitive provenance offline. Frozen v1/v2
preparations remain readable; structural-v3 builds supply the improved hierarchy.

Summaries remain drafts: provenance identifies their inputs, not proof of every
sentence. Compression errors can accumulate across levels. There is currently no
independent summary-fidelity review gate. See [downstream uses](downstream-uses.md).
