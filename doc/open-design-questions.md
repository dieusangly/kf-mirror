# Open design questions

## Supporting passages (Task 2 — resolved for schema v1)

- Use the format-independent `evidence` reference in the [artifact schemas](artifact-schemas.md). An answer may require several references.
- Resolve `source_id`, `document_id`, `element_id`, and an optional text span through versioned records. Native PDF, HTML, Markdown, and DOCX locations remain on elements for review.
- Derive element IDs from the exact document ID and a deterministic native locator key. The [PDF and HTML examples](artifact-schemas.md#examples-and-validation) validate against schema v1 and cross-record checks.

The corpus table retains human-readable locators until a parser produces normalized elements; those locators should then map to Task 2 evidence references.
