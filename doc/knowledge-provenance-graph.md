# Knowledge provenance graph

Create a standalone HTML viewer from a saved classified run; set directories
separately when claim-generation and classification run IDs differ. For the
structural-chunk and semantic-cluster pipeline, pass the matching generated
claims directory as well:

```sh
RUN_ID="your-claim-run-id"
CLASSIFIED_DIR="artifacts/classified-claims-${RUN_ID}"
CANDIDATES_DIR="artifacts/generated-claims-${RUN_ID}"

.venv/bin/python scripts/render_knowledge_graph.py \
  --bundle "$CLASSIFIED_DIR/artifact-bundle.json" \
  --claim-generation-dir "$CANDIDATES_DIR" \
  --output "$CLASSIFIED_DIR/knowledge-provenance-graph.html" \
  --force
```

Open the HTML in a browser. `--force` replaces only that file; omit it/use a new
name to preserve an existing view. No model calls or claim artifact edits occur.

## What is included

`--bundle` loads topics, classified claims, original chunks, elements,
documents and sources. `--claim-generation-dir` adds structural chunks,
semantic clusters, candidate-to-cluster links, and the generated candidate
claims. Without it, the viewer omits the structural and semantic stages and
continues to render older bundles.

Candidate nodes are separate from classified claim nodes. Classified
candidates also appear as claims with topic links; candidates that did not
produce a claim have no topic link. The viewer displays candidate status from
the adjacent classification `report.json` and matching claim records.
`--claim-candidates "$CANDIDATES_DIR/claim-candidates.jsonl"` can add candidates
without loading the structural and semantic stages.

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
