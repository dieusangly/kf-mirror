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

The 13-document Docling pilot has accepted structure, generated claims/summaries
and a [retrieval evaluation](../artifacts/20261028-docling-aml-cft-01/indexing/retrieval-report.json).
Its [claim-review exception](../artifacts/20261028-docling-aml-cft-01/claim-review/acceptance-exception.json)
retains failed review findings and does not authorize a downstream gate bypass
or publication. Retrieval checks do not certify answer quality.

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
