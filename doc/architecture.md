# Knowledge Foundry architecture

The Assistant can answer from reviewed document evidence or by running a symbolic case and checking its proof. The two paths meet in the Assistant. Document retrieval reads a published corpus snapshot. Symbolic execution takes an encoding, a query and an engine configuration.

## Package ownership

All Python packages live under `src/knowledge_foundry/`.

| Package | Responsibilities |
| --- | --- |
| [knowledge_engineering](../src/knowledge_foundry/knowledge_engineering/) | Verifies and ingests originals, parses and structures documents, runs source reviews, and produces clusters, claims, topics and summaries. |
| [provenance](../src/knowledge_foundry/provenance/) | Validates bundles and reasoning package receipts, resolves evidence and source passages, and assembles graph data. |
| [reasoning_engine](../src/knowledge_foundry/reasoning_engine/runtime.py) | Verifies the engine executable against its recorded digest, solves an explicit query and checks the result certificate. It runs independently of the Assistant and document store. |
| [retrieval](../src/knowledge_foundry/retrieval/) | Retrieves and ranks passages, claims and summaries, builds answer context and validates citations. Its [offline import adapter](../src/knowledge_foundry/retrieval/adapters/engineering.py) reads engineering outputs. Online queries use published snapshots. |
| [storage](../src/knowledge_foundry/storage/) | PostgreSQL owns corpus snapshots, search records, chat sessions and feedback. SQLite stores contributor accounts, sessions and submissions. |
| [assistant](../src/knowledge_foundry/assistant/) | Routes questions, projects accepted policies, constructs cases from user assertions and composes responses from checked results or cited evidence. |
| [applications](../src/knowledge_foundry/applications/) | Serves the Streamlit Assistant, the Contributors HTTP app and the knowledge provenance graph viewer. Launchers and web assets live under [apps](../apps/). |
| [workflows](../src/knowledge_foundry/workflows/) | Coordinates document stages, review and publication, accepted case replay, and offline Assistant evaluation. Repository [commands](../scripts/) expose these operations. |
| [shared](../src/knowledge_foundry/shared/) | Provides checkout paths, hashing, token counts, vector operations, model transport, embeddings and concurrency. |

## From source to answer

1. **Documents.** Ingestion checks source editions and byte digests against `data/manifest.json`. Structuring produces located elements and chunks. Parsing and chunk reviews bind acceptance to the frozen outputs. Claims and recursive summaries consume the accepted preparation. Import builds a database snapshot, and explicit publication makes it available to retrieval. Citations resolve to the source edition, element revision and, where available, its exact text span. [Document pipeline](aml-cft-pipeline.md) · [Providers](providers.md) · [Downstream data](downstream-uses.md)
2. **Symbolic cases.** Package receipts bind the accepted inputs and results by path and hash. Replay submits each accepted encoding and its separate query to the checked engine. For a new question, the Assistant selects applicable policies, constructs a fresh case and uses the engine result and proof to produce its answer. [Reasoning pilot](aml-cft-reasoning-pilot.md) · [Assistant behavior](aml-cft-reasoning-assistant.md)

## Imports, resources and reproducibility

Use the owning package for new imports. For example, `accept_structure` lives in `knowledge_foundry.knowledge_engineering.structuring.document_structuring`. The [package initializer](../src/knowledge_foundry/__init__.py) resolves supported legacy imports and configured provider names to the same module objects. Four review modules execute at the package root because accepted parsing-review records pin their paths and bytes. Retiring an alias requires checking supported callers, configuration and frozen tools.

Commands use the checkout's existing configuration, schemas, prompts, SQL and application assets. Workflow checkpoints fingerprint code, settings and inputs. A source edit, including a docstring edit, can make a saved run stale. Use the matching checkout for same-code resume. The document workflow reports which stages need repair when inputs change. [Resource lookup](../src/knowledge_foundry/shared/paths.py) · [Workflow controller](../src/knowledge_foundry/workflows/document_pipeline.py)

Accepted reasoning packages and the frozen Assistant evaluation also reference specific executable and source bytes. The offline evaluation checks its recorded preparation independently of the live Assistant quality verdict. [Regression checks](regression-baseline.md) · [Assistant evaluation](aml-cft-reasoning-assistant-evaluation.md)
