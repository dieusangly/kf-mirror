# Knowledge provenance graph

Create a standalone HTML viewer from matching classified/generated runs; override
directories when their run IDs differ:

```sh
RUN_ID="your-claim-run-id"
CLASSIFIED_DIR="artifacts/classified-claims-${RUN_ID}"
CANDIDATES_DIR="artifacts/generated-claims-${RUN_ID}"

direnv exec . .venv/bin/python scripts/render_knowledge_graph.py \
  --bundle "$CLASSIFIED_DIR/artifact-bundle.json" \
  --claim-generation-dir "$CANDIDATES_DIR" \
  --output "$CLASSIFIED_DIR/knowledge-provenance-graph.html"
```

Open the HTML locally; `--force` replaces an existing viewer. No model calls or
artifact edits occur. Register it as `graph_path` in
[`retrieval-corpora.json`](../config/retrieval-corpora.json) for matching chatbot
snapshots; no separate server is needed.

Add `--summary-dir "artifacts/summaries-${RUN_ID}"` for [summaries](summaries.md);
both branches must share the frozen structural build. For summaries alone, use
their `preparation/artifact-bundle.json` as `--bundle` and omit
`--claim-generation-dir`. Original records must match.

## What is included

`--bundle` loads topics, classified claims, original chunks, elements,
documents and sources. `--claim-generation-dir` adds structural chunks,
semantic clusters, candidate-to-cluster links, and the generated candidate
claims. Without it, the viewer omits the structural and semantic stages and
continues to render older bundles.

**Summaries** lists levels highest-first with filters, chunk counts and review
status. **Document-derived knowledge** combines claims/summaries; **Topics and claims**
is claim-only.

Choose a summary and **Explore provenance** to follow summary → summary cluster →
input summaries → semantic cluster → structural chunk → original chunk → element
→ document → source. Level-one summaries connect directly to semantic clusters.
The detail panel labels inherited passages **Source provenance**, separate from
claim evidence. Related-record buttons expose direct input and output links.
Full/local graphs also show structural context and element parent relationships;
these do not assert that an entire section was merged into one chunk.

Summary columns separate levels/clusters: generation flows right-to-left,
provenance left-to-right. Ungrouped inputs can skip levels.

Candidates are distinct from classified claims; only the latter link to topics.
Status comes from classification `report.json` and claim records. Local/document
views hide candidates when their classified claim is visible; unclassified ones
remain. **Connected records**, candidate filters and debug views expose all candidates.
`--claim-candidates "$CANDIDATES_DIR/claim-candidates.jsonl"` can add candidates
without loading the structural and semantic stages.

**Connected records** labels describe relationships from the selected record:
`groups`/`grouped by`, `uses`/`used by`, `supported by`/`supports`,
`generated from`/`generates`, and `classified as`/`classified from`. The latter
connects a candidate to its classified claim; topic membership uses `groups`.

## Inspect evidence

1. Select a topic and claim in **Topics and claims**.
2. Choose **Explore provenance**. With a generation directory loaded, follow
   claim → semantic cluster → structural chunk → original chunk → element →
   document → source. Without one, the older claim → chunk path remains.
3. Read the exact evidence spans and source locations in the detail panel;
   compare the passage in the saved PDF or HTML. A publisher link is not the saved file.

Use **Document-derived knowledge** for one document or **Full graph / debug** for
all records. Filters/paged claims support large views.

Links do not establish completeness/legal correctness. Claims/topics remain
drafts; retrieval dates do not prove legal effectiveness.
