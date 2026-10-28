# Downstream uses

Accepted structural chunks feed semantic clustering, then independent branches:
**claims** express propositions with exact evidence and curated topic/null labels;
**summaries** compress chunks or earlier summaries with transitive provenance.

| Use | Claims | Summaries |
| --- | --- | --- |
| Provenance graph | Inspect propositions, topics and evidence. | Trace synthesis levels to source chunks. |
| Retrieval/assistant | Retrieve propositions with evidence and governing context. | Find broad regions, then descend to evidence. |
| Comparison/reasoning | Preserve conditions, exceptions and editions. | Map themes; compression may hide disagreements. |
| Training (prospective) | Reviewed context → claim; claim/context → topic/null. | Reviewed text → summary; summaries → higher summaries. |
| Reasoning training (prospective) | Curate questions, evidence, checked conclusions and verifiable steps. | Supply context maps; verify against underlying evidence. |

Claims/classification, recursive summaries, retrieval, provenance graph,
PostgreSQL/pgvector and the assistant are implemented, but have not been rerun
end to end on this unaccepted 13-document pilot.

- Citations resolve to original evidence; summaries are not primary claim evidence.
  Shared topics/similarity do not establish agreement or legal applicability.
- The graph tracks artifact/evidence provenance; domain entity/relation extraction
  needs additional implementation and validation.
- Training requires target-level review, source versions/rights and document-family
  splits before deriving overlapping chunks or summary ancestors. Run acceptance
  does not approve every claim; summaries have no independent fidelity gate.
  `reasoning_effort` does not create verified reasoning traces.

Implementation: [retrieval](../src/knowledge_foundry/retrieval/),
[graph](../scripts/render_knowledge_graph.py),
[storage](../src/knowledge_foundry/storage/postgres.py),
[assistant](../apps/chat/app.py). See [pipeline commands](aml-cft-pipeline.md).
