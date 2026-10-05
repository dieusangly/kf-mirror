# Knowledge Foundry artifact schemas (Task 2)

The [JSON Schema](../schemas/artifact-bundle.schema.json) defines five records and checks that example JSON has the expected fields and types. In that file, `properties` lists fields, `required` lists mandatory fields, `$defs` holds reusable record definitions, and `$ref` points to one of those definitions. `oneOf` selects a location format; `additionalProperties: false` rejects fields outside the contract.

The bundle has six top-level fields: `schema_version` and the `sources`, `documents`, `elements`, `chunks`, and `builds` arrays. Each array uses a definition in `$defs` for its items. Other definitions describe nested objects such as locations, evidence, and text spans; they are not extra top-level fields.

A **bundle** groups records for validation. It may contain one saved document or several; it does not prescribe one JSON file per PDF. A full PDF parse would usually have one document record and many element and chunk records. The [PDF example](../examples/artifact-bundle-pdf.json) shows just one paragraph and one chunk from the 70-page file. The production export layout is a separate Task 6 decision.

The bundle and each record have `schema_version: "1.0.0"`, which describes their field layout. This differs from a publisher's document version.

## Records

| Record | What it stores | Main links |
| --- | --- | --- |
| Source | A publisher item, its URL, format, and edition or legal revision. | Stable `id` and publisher `version`. |
| Document | One saved file, its SHA-256 hash, media type, and `retrieved_on` date. | `source_id` and `source_version`. |
| Element | One parsed heading, paragraph, table, or other unit, with text and a location in the file. | `document_id` and `locator_key`. |
| Chunk | Text assembled for retrieval, with references to the elements it uses. | `document_id`, `build_id`, and `evidence`. |
| Build | The inputs, settings, outputs, and status of a logical build. | Input document IDs and output chunk IDs. |

`retrieved_on` is when the local file was captured, not when the text was published or legally effective. The [source manifest](../data/manifest.json) lists the pilot's saved files.

## Stable IDs

- A source keeps its `id` when its URL moves. A new edition gets a new `version`; two versions can share the same source ID.
- A document ID depends on the source ID and saved file hash. Different bytes make a different document ID.
- An element ID depends on the document ID and its source location (`locator_key`). Its `revision` changes if the parsed kind or text changes.
- A chunk ID depends on its document, chunker version, evidence, and text.
- A build ID depends on its input documents, pipeline version, and settings hash. The same inputs and settings produce the same logical build ID.

IDs and hashes use SHA-256. The [validator](../scripts/validate_artifact_examples.py) specifies the exact hash inputs and checks references between records. Output-changing pipeline code must use a new pipeline version.

## Locations and supporting passages

An element keeps a location that a reader can check in the saved file:

| Format | Location |
| --- | --- |
| PDF | One-based file page and printed label; optional bounding box in PDF points (`left, top, right, bottom`). |
| HTML | Article ID, selector, and optional clause or anchor. |
| Markdown | Heading path and one-based line range. |
| DOCX | Zero-based block index, with table, row, and cell indices when relevant. |

For a PDF block without a printed label, use a consistent index within the page. A parser that splits or merges blocks may then produce new element IDs.

An `evidence` reference points to a `source_id`, `document_id`, and `element_id`. It may also specify a text `span` with Unicode code-point offsets `[start, end)`, where `0 <= start < end <= len(text)`. Use several references when a claim needs several passages. The element gives the reviewable location; the document gives the exact source version. The pilot's human-readable locators can be mapped to these references after parsing.

## Examples and validation

The [PDF example](../examples/artifact-bundle-pdf.json) uses ACPR–Tracfin page 3 (A1). The [HTML example](../examples/artifact-bundle-html.json) uses Légifrance article L. 561-2-2, clause 1° (L1a). Both use hashes of the saved source files. They illustrate the record structure; they are not parser output or expert-approved answer labels.

Run `python3 scripts/validate_artifact_examples.py` with the `jsonschema` Python package installed. It checks the source files, schema, IDs, references, and text spans. This does not measure parsing or retrieval quality.

## Task 2 completion check

The five records specify stable IDs, schema versions, source locations, and lineage. The validator passes the five-file source manifest and both PDF and HTML example bundles, including their cross-record references. This meets Task 2's completion criteria in the [implementation plan](knowledge-foundry-implementation-plan.md). The examples each cover one passage; full parsing and export validation belong to later tasks.
