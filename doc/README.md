# Knowledge Foundry documentation

Start with the [source-to-chatbot runbook](pipeline-runbook.md) for commands,
prerequisites, reviews and safe reruns. To reuse the saved pilot immediately,
follow the [RAG setup](retrieval-rag.md).

| Order | Guide | Covers |
| --- | --- | --- |
| 1. Research | [Project benchmark](open-source-project-benchmark.md) | Framework comparison, actual dependencies and design references. |
| 2. Corpus | [AML/CFT catalog](banking-aml-cft-corpus-catalog.md) | Sources and benchmarks. |
| 3. Pilot | [Pilot sources and evaluation](french-aml-cft-pilot-sources-and-evaluation.md) | Files, draft questions, targets. |
| 4. Plan | [Implementation plan](knowledge-foundry-implementation-plan.md) | Tasks, dependencies, estimates. |
| 5. Contract | [Artifact schemas](artifact-schemas.md) | Records, IDs, evidence, examples. |
| 6. Ingestion | [Ingestion](ingestion.md) | Inventory CLI. |
| 7. Parsing | [Parsing](parsing.md) | Setup, CLI, configuration, outputs. |
| 8. Parsing evaluation | [Parsing evaluation](parsing-evaluation.md) | Checks and acceptance. |
| 9. Chunking | [Chunking](chunking.md) | CLI, evidence rules, outputs. |
| 10. Chunk evaluation | [Chunking evaluation](chunking-evaluation.md) | Automatic checks and source review. |
| 11. Structural chunk and semantic cluster build | [Structural chunk and semantic cluster build](structural-chunks-and-semantic-clusters.md) | Shared inputs for claims and summaries, budgets and reruns. |
| 12. Claim generation and classification | [Claim generation and classification](claim-generation-and-classification.md) | Models, CLI, review and exports. |
| 13. Claim evaluation | [Claim evaluation](claim-evaluation.md) | Review and acceptance. |
| 14. Summaries | [Recursive summaries](summaries.md) | Independent cluster summaries, higher levels, provenance, CLI and migration. |
| 15. Knowledge provenance graph | [Knowledge provenance graph](knowledge-provenance-graph.md) | Inspect claim/summary → chunk → source links. |
| 16. Downstream uses | [Claims versus summaries](downstream-uses.md) | Graphs, RAG, candidate training data and reasoning-task uses. |
| 17. Document assistant | [Retrieval/RAG and PostgreSQL](retrieval-rag.md) | Frozen artifact imports, publication, querying, chatbot, evaluation and limits. |
