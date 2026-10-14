# Knowledge graph

`render_knowledge_graph.py` creates a standalone, interactive HTML view of a
normalized artifact bundle. It is an inspection view: the bundle remains the
source of truth and the renderer never changes its records.

The graph follows the source-grounding path:

```text
Topic → Claim → Chunk → Element → Document → Source
```

Claims can cite multiple chunks and a chunk can cite multiple elements, so the
result is a graph rather than a strict tree. Dashed connections mark direct
evidence links. The topic selector narrows the visible graph to one topic and
everything reachable from it; selecting any node reveals its full identifier
and saved record detail.

## Render a bundle

Run the renderer against the classified bundle, choosing a new output file:

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
can be shared with the matching artifact bundle. The command refuses to
overwrite an existing output; pass `--force` only when replacing that file is
intentional. Use `--title` to set the browser-page title.

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

The graph displays the record links available in the bundle. It does not infer
entity relationships, legal conclusions, or unrecorded evidence. A future
domain knowledge-graph export may add typed entity and relation edges, but it
must preserve the same source provenance.
