# Knowledge provenance graph

Create a standalone HTML viewer from the full classified bundle:

```sh
.venv/bin/python scripts/render_knowledge_graph.py \
  --bundle artifacts/classified-claims-v8-full/artifact-bundle.json \
  --output artifacts/classified-claims-v8-full/knowledge-provenance-graph.html \
  --force
```

Open the HTML in a browser. `--force` replaces only the named HTML file; omit it
and choose a new filename to keep the existing view. Rendering makes no model
calls and does not change the compilation records.

## What is included

`--bundle` loads the saved topics, classified claims, chunks, parsed elements,
documents and sources. It does **not** load the separate candidate file.
The printed `candidates: 0` therefore means no candidate records were supplied
to the renderer; it does not mean generation produced no candidates.

To include candidates, add this option to the command:

```sh
--claim-candidates artifacts/generated-claims-v8-full/claim-candidates.jsonl
```

This loads **every candidate in that file**, including those already classified.
It does not select only unclassified candidates. Candidate nodes have no
topic links; the viewer's “unclassified candidate” label does not establish the
candidate's actual classification outcome. Check `unclassified_candidate_ids`
in the classification `report.json` for that outcome.

## Inspect evidence

1. Select a topic and claim in **Topics and claims**.
2. Choose **Explore provenance** and follow claim → chunk → element → document → source.
3. Read the exact evidence spans and source locations in the detail panel;
   compare the passage in the saved PDF or HTML. A publisher link is not the saved file.

Use **Document-derived knowledge** to inspect one document, or **Full graph / debug**
for all loaded records. Filters and paged topic claims keep large views manageable.

The graph shows saved links, not proof of completeness or legal correctness.
This compilation's claims and topics are drafts, not verified legal interpretations.
A retrieval date is not evidence of legal effectiveness.
