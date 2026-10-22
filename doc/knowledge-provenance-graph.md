# Knowledge provenance graph

Create a standalone HTML viewer from a saved classified run; set directories
separately when claim-generation and classification run IDs differ. For the
structural-chunk and semantic-cluster pipeline, pass the matching generated
claims directory as well:

```sh
RUN_ID="your-claim-run-id"
CLASSIFIED_DIR="artifacts/classified-claims-${RUN_ID}"
CANDIDATES_DIR="artifacts/generated-claims-${RUN_ID}"

direnv exec . .venv/bin/python scripts/render_knowledge_graph.py \
  --bundle "$CLASSIFIED_DIR/artifact-bundle.json" \
  --claim-generation-dir "$CANDIDATES_DIR" \
  --output "$CLASSIFIED_DIR/knowledge-provenance-graph.html"
```

Open the HTML in a browser. Add `--force` only to replace an existing viewer,
or choose a new output name to preserve it. No model calls or claim artifact edits occur.
For chatbot access, register this exact file as the run's `graph_path` in
[`retrieval-corpora.json`](../config/retrieval-corpora.json). The chatbot displays
it directly when the selected snapshot matches; no separate HTTP server is needed.

Add `--summary-dir "artifacts/summaries-${RUN_ID}"` to include the independent
[summary branch](summaries.md). It verifies and loads the frozen summary run and
its structural chunk and semantic cluster build outputs. When both branches
are provided, their structural chunks and semantic clusters must match.
To view summaries before any claims exist, use the summary run's
`preparation/artifact-bundle.json` as `--bundle`, pass `--summary-dir`, and omit
`--claim-generation-dir`. Original bundle records must match the summary inputs.

## What is included

`--bundle` loads topics, classified claims, original chunks, elements,
documents and sources. `--claim-generation-dir` adds structural chunks,
semantic clusters, candidate-to-cluster links, and the generated candidate
claims. Without it, the viewer omits the structural and semantic stages and
continues to render older bundles.

The **Summaries** view lists all summary levels, with highest levels first and
document/review/type filters. Summary cards show level, original-chunk count and
review status. **Document-derived knowledge** includes summaries alongside claims;
**Topics and claims** retains its existing claim-only behavior.

Choose a summary and **Explore provenance** to follow summary → summary cluster →
input summaries → semantic cluster → structural chunk → original chunk → element
→ document → source. Level-one summaries connect directly to semantic clusters.
The detail panel labels inherited passages **Source provenance**, separate from
claim evidence. Related-record buttons expose direct input and output links.
Full/local graphs also show structural context and element parent relationships;
these do not assert that an entire section was merged into one chunk.

Summary provenance uses separate columns for each summary level and its input
cluster. Read right to left to follow generation: semantic cluster → level-one
summary → cluster for level two → level-two summary. Read left to right to trace
a summary back to its inputs. Ungrouped lower-level inputs can skip levels.

Candidate nodes are separate from classified claim nodes. Classified
candidates also appear as claims with topic links; candidates that did not
produce a claim have no topic link. The viewer displays candidate status from
the adjacent classification `report.json` and matching claim records.
Local provenance and document views omit candidate nodes whose classified claim
is available under the active filters. Unclassified candidates remain visible.
Candidates remain accessible through **Connected records**, an explicit candidate
selection/type filter, and full/debug views.
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
