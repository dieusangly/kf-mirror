# Claims versus summaries downstream

**Claims** express individual propositions linked to exact source evidence.
**Summaries** compress related chunks or earlier summaries, retaining provenance.
Use either, both or passages alone in [snapshot-scoped RAG](retrieval-rag.md).
The graph supports both; training uses below remain prospective.

| Use | Claims | Summaries |
| --- | --- | --- |
| Knowledge graph | Inspect propositions, topics and supporting evidence; possible inputs to validated entity/relation extraction. | Navigate broad themes and follow summary levels back to source chunks. |
| RAG | Retrieve precise propositions, then fetch evidence and governing context for answers. | Select relevant regions for broad questions, then descend to detailed evidence. |
| Comparison and reasoning | Combine or compare evidenced propositions while preserving conditions, exceptions and source editions. | Map topics and find areas to investigate; compression may hide disagreements. |
| Training data | Reviewed source/context → claim and claim/context → topic/null examples. | Reviewed structural-text → summary and earlier-summaries → higher-summary examples. |
| Reasoning-model training | Curate questions, evidence combinations, checked conclusions and verifiable intermediate steps. | Supply context maps or synthesis tasks; validate conclusions against underlying evidence. |

All answer citations resolve to original evidence. Shared topics or semantic
similarity do not prove agreement or legal applicability.

Before training, review individual targets: a run-level pass does not approve
every claim, and summaries currently have no independent fidelity gate. Retain
source versions and usage rights. Split datasets by source/document family before
deriving overlapping records, so chunks or summary ancestors cannot leak between
training and evaluation.

Reasoning training needs curated tasks and checked targets; `reasoning_effort`
does not create verified reasoning traces.
