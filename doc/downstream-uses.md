# Downstream uses

Accepted structural chunks feed semantic clustering, then independent claim and
summary branches. **Claims** express individual propositions linked to exact
source evidence; classification assigns curated topics or null. **Summaries**
compress related chunks or earlier summaries, retaining transitive provenance.

Claim generation/classification, recursive summaries, retrieval, the provenance
graph, PostgreSQL/pgvector storage and the document assistant remain implemented.
They have not been rerun end to end on the current 13-document structuring pilot,
whose source/layout reviews are still pending. Training uses below are prospective.

| Use | Claims | Summaries |
| --- | --- | --- |
| Knowledge provenance graph | Inspect propositions, topics and supporting evidence. | Navigate synthesis levels back to source chunks. |
| Retrieval and assistant | Retrieve precise propositions, then fetch evidence and governing context. | Locate relevant regions for broad questions, then descend to evidence. |
| Comparison and reasoning | Compare propositions while preserving conditions, exceptions and source editions. | Map themes and identify areas to investigate; compression may hide disagreements. |
| Training data | Reviewed source/context → claim and claim/context → topic/null examples. | Reviewed structural text → summary and earlier summaries → higher summary examples. |
| Reasoning-model training | Curate questions, evidence combinations, checked conclusions and verifiable intermediate steps. | Supply context maps or synthesis tasks; check conclusions against underlying evidence. |

The graph records artifact/evidence provenance; domain entity/relation extraction
would require additional implementation and validation. Answer citations resolve
to original evidence. Shared topics or semantic similarity do not establish
agreement or legal applicability. Summaries are not primary evidence for claims.

Before training, review individual targets: a run-level pass does not approve
every claim, and summaries currently have no independent fidelity gate. Retain
source versions and usage rights. Split datasets by source/document family before
deriving overlapping chunks or summary ancestors to prevent evaluation leakage.
`reasoning_effort` does not create verified reasoning traces.

See the [pipeline](README.md), [retrieval implementation](../src/knowledge_foundry/retrieval/),
[graph renderer](../scripts/render_knowledge_graph.py),
[PostgreSQL storage](../src/knowledge_foundry/storage/postgres.py) and
[assistant](../apps/chat/app.py).
