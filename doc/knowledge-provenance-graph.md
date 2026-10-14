# Knowledge provenance graph

`render_knowledge_graph.py` creates a standalone interactive HTML view of a
normalized artifact bundle. The bundle remains the source of truth; the viewer
preserves and displays the saved record links without changing them.

The default screen shows topics and a page of their claims. Select a claim and
choose **Explore provenance** to follow its chunks, evidence elements,
documents, and sources. The document-derived view starts at a selected document
and shows the connected claims and evidence around it. **Full graph / debug**
exposes all records; large results ask before rendering.

Other selected provenance records can open a one-step local graph. Selecting a
claim or document switches to its corresponding deeper view.

The topic, document, build, review, and node type filters apply to each view.
The detail panel contains the selected record's text and metadata, evidence
references, related records, and source location where available. Topic claims
are paged so the initial canvas stays small even when a topic has hundreds of
claims. Every provenance record remains available through the local views or
full graph.

## Render a bundle

Run the renderer against a classified bundle, choosing a new output file:

```sh
.venv/bin/python scripts/render_knowledge_graph.py \
  --bundle artifacts/classified-claims-20261013-114244/artifact-bundle.json \
  --output artifacts/classified-claims-20261013-114244/knowledge-provenance-graph.html
```

For the supplied example:

```sh
.venv/bin/python scripts/render_knowledge_graph.py \
  --bundle examples/artifact-bundle-pdf.json \
  --output /tmp/knowledge-provenance-graph.html
```

Open the generated HTML file in a browser. It has no external dependencies and
can be shared with the matching artifact bundle. The renderer combines the
viewer template, styles, and JavaScript modules under `scripts/kpg_viewer/` into
one deterministic HTML file. It refuses to overwrite an existing output; pass
`--force` only when replacing that file is intentional. Use `--title` to set
the browser-page title.

## Render generated claim candidates

Generation-stage bundles contain chunks but no normalized claims or topics.
Pass the adjacent `claim-candidates.jsonl` using `--claim-candidates` to add
unclassified candidate-claim nodes and their links to chunks and evidence:

```sh
.venv/bin/python scripts/render_knowledge_graph.py \
  --bundle artifacts/generated-claims-20261013-114244/artifact-bundle.json \
  --claim-candidates artifacts/generated-claims-20261013-114244/claim-candidates.jsonl \
  --output artifacts/generated-claims-20261013-114244/knowledge-graph.html
```

Candidate claims are distinct from classified claims and have no topic edge.
They remain unclassified candidates even when their grounding review status is
shown as verified.

The graph displays only record links available in the bundle. It does not
infer entity relationships, legal conclusions, or unrecorded evidence. The
full graph is available for debugging, though very large bundles can still
produce a dense canvas when that mode is explicitly rendered.
