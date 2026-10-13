# Open design questions

## Supporting passages (resolved in schema v1)

- Use the format-independent `evidence` reference in the [artifact schemas](artifact-schemas.md). It names a source, document, element, required element revision, and optional text span; an answer may need several references.
- Keep native PDF, HTML, Markdown, and DOCX locations on elements. Derive element IDs from the document ID and a deterministic native locator key. The [examples](artifact-schemas.md#examples-and-validation) pass schema and cross-record checks.

Map the pilot's human-readable locators to evidence references after parsing.

## Topic knowledge base

- The [artifact schema](artifact-schemas.md) defines the path from saved source to topic. A topic combines supported claims across documents; retrieval, graph, and dataset outputs are downstream views.
- LLM classification is planned for candidate claims. Claims require a `topic_id`; classification method/version and review status are separate from generation and grounding. Pilot examples use manual classification and remain drafts.
- Resolved for Task 6: compilation writes a new `artifacts/compiled-*` directory with a manifest, normalized bundle, JSONL, and draft topic views. `config/topics-v1.json` is the curated stable-ID taxonomy. Claims retain source-scoped qualifications and conflicts as separate evidence-linked statements; no effective date is inferred from `retrieved_on`. Grounding and classification reviews remain separate, and generated output begins as draft.
