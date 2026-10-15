# Knowledge provenance graph

Create a standalone HTML viewer from a saved classified run; set directories
separately if candidate/classification run IDs differ:

```sh
RUN_ID="your-compilation-run-id"
CLASSIFIED_DIR="artifacts/classified-claims-${RUN_ID}"
CANDIDATES_DIR="artifacts/generated-claims-${RUN_ID}"

.venv/bin/python scripts/render_knowledge_graph.py \
  --bundle "$CLASSIFIED_DIR/artifact-bundle.json" \
  --output "$CLASSIFIED_DIR/knowledge-provenance-graph.html" \
  --force
```

Open the HTML in a browser. `--force` replaces only that file; omit it/use a new
name to preserve an existing view. No model calls or compilation edits occur.

## What is included

`--bundle` loads topics, claims, chunks, elements, documents and sources.
`candidates: 0` means no candidate file was supplied, not that generation was empty.

Add `--claim-candidates "$CANDIDATES_DIR/claim-candidates.jsonl"` for **all candidates**,
including classified ones. Candidate nodes lack topic links and appear as
“unclassified candidate” regardless of outcome; check `unclassified_candidate_ids`
in classification’s `report.json` for actual status.

## Inspect evidence

1. Select a topic and claim in **Topics and claims**.
2. Choose **Explore provenance** and follow claim → chunk → element → document → source.
3. Read the exact evidence spans and source locations in the detail panel;
   compare the passage in the saved PDF or HTML. A publisher link is not the saved file.

Use **Document-derived knowledge** for one document or **Full graph / debug** for
all records. Filters/paged claims support large views.

Links do not establish completeness/legal correctness. Claims/topics remain
drafts; retrieval dates do not prove legal effectiveness.
