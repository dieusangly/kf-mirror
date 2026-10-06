# Open design questions

## Supporting passages (Task 2 — resolved for schema v1)

- Use the format-independent `evidence` reference in the [artifact schemas](artifact-schemas.md). An answer may require several references.
- Resolve `source_id`, `document_id`, `element_id`, and an optional text span through versioned records. Native PDF, HTML, Markdown, and DOCX locations remain on elements for review.
- Derive element IDs from the exact document ID and a deterministic native locator key. The [PDF and HTML examples](artifact-schemas.md#examples-and-validation) validate against schema v1 and cross-record checks.

The corpus table retains human-readable locators until a parser produces normalized elements; those locators should then map to Task 2 evidence references.

## Topic knowledge base

- The canonical flow is saved source → document → parsed element → chunk → grounded claim → topic. A topic is a stable knowledge unit that can combine supported claims from several source documents; it is not merely a tag. Retrieval, graph, and dataset outputs are downstream views. See the [implementation plan](knowledge-foundry-implementation-plan.md).
- The [artifact schema](artifact-schemas.md) defines claims and topics alongside source, document, element, chunk, and build records. Task 3 ingestion reports contain source and document records.
- LLM classification is the planned way to assign candidate claims to topics. The final claim record requires a `topic_id`; its `classification` metadata records the assignment method and version separately from claim generation. Pilot examples use manual classification and remain drafts.
- Open for Task 6: define the export directory layout, topic curation rules, legal scope and effective-date qualifiers, conflict representation, and review workflow. Do not infer legal effectiveness from `retrieved_on`.
