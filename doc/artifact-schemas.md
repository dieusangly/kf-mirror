# Knowledge Foundry artifact schemas (Task 2)

The [JSON Schema](../schemas/artifact-schema-v1.0.0.json) defines five records and checks that example JSON has the expected fields and types. In that file, `properties` lists fields, `required` lists mandatory fields, `$defs` holds reusable record definitions, and `$ref` points to one of those definitions. `oneOf` selects a location format; `additionalProperties: false` rejects fields outside the contract.

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

A **source ID** is assigned once as a readable, stable name, such as `src:acpr-tracfin-joint-guidelines`. It is not hashed or copied from the pilot manifest's short labels (`A`, `T`, etc.). Keep it when the publisher URL moves or a new edition appears; the separate source `version` identifies the edition.

The other four IDs are calculated as follows. `H(text)` means the lowercase hexadecimal SHA-256 digest of the text encoded as UTF-8. `\n` is one newline character, and `content_sha256` is the SHA-256 digest of the saved file's raw bytes. In both examples, the document's `content_sha256` equals the corresponding source entry's `sha256` in the manifest; that file hash is an input to, not the whole value of, the document ID.

```text
document.id = "doc:" + H(source_id + "\n" + content_sha256)
element.id  = "el:" + H(document_id + "\n" + locator_key)
chunk.id    = "chunk:" + H(document_id + "\n" + chunker_version + "\n" + canonical_evidence + "\n" + text)
build.id    = "build:" + H(pipeline_version + "\n" + config_sha256 + "\n" + "\n".join(sorted(input_document_ids)))
```

`canonical_evidence` is the chunk's `evidence` array serialized as compact JSON with keys sorted within each object, Unicode characters unescaped, and array order preserved (`json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`). `config_sha256` is the supplied digest of the pipeline settings; this schema does not yet define how those settings are serialized before hashing. The build ID uses sorted input document IDs, so their array order does not change it; output chunk IDs are recorded in the build but are not part of its ID.

An element's separate `revision` is `H(kind + "\n" + text)`. Within the same document ID, its ID stays the same when text at the same `locator_key` changes, while its revision changes. Different saved file bytes change the document ID and its dependent element and chunk IDs. Output-changing pipeline code must use a new `pipeline_version`. The [validator](../scripts/validate_artifact_examples.py) implements these rules and checks references between records.

## How the records connect

An ID in a link field must match the `id` of the record it names. A source edition is selected by **both** its stable `id` and its `version`. One source can have several editions; one document can have many elements and chunks; one build can take several documents and produce several chunks. [This diagram](images/artifact-record-links-v1.0.0.png) shows the links in the PDF example.

| Link field | Must match | Meaning |
| --- | --- | --- |
| `document.source_id` and `source_version` | `source.id` and `version` | The publisher item and edition from which the saved file came. |
| `element.document_id` | `document.id` | The saved file containing this parsed unit and its location. |
| `chunk.document_id` | `document.id` | The saved file represented by this retrieval text. |
| `chunk.evidence[].element_id` | `element.id` | Each parsed unit supporting the chunk. Its `document_id` must equal the chunk's. |
| `chunk.evidence[].document_id` and `source_id` | `document.id` and that document's `source_id` | Explicit provenance for each supporting passage; these values must agree with the element and chunk links. |
| `chunk.build_id` | `build.id` | The build that produced the chunk. |
| `build.input_document_ids[]` | `document.id` | Files processed by the build, including every document named by its chunks. |
| `build.output_chunk_ids[]` | `chunk.id` | All chunks whose `build_id` names this build. |

The manifest and artifact bundle use different IDs: the manifest's `A` and `L1` are pilot corpus labels, while the examples assign stable `src:...` IDs. The [validator](../scripts/validate_artifact_examples.py) matches each example to one manifest entry by publisher URL and file SHA-256, then checks source version and retrieval date. The PDF example therefore connects manifest `A` to `src:acpr-tracfin-joint-guidelines`, and the HTML example connects `L1` to `src:cmf-L561-2-2`.

For the PDF example, the path through the five records is: source `src:acpr-tracfin-joint-guidelines` → document `doc:485e1794…` → element `el:498756bd…` → chunk `chunk:b85bec76…`. The build `build:fcc1a570…` names that document as input and that chunk as output; the chunk points back to the build. The chunk's `evidence` points to the element, which carries the PDF page and printed label. The chunk ID does not include `build_id`, and the build ID does not include output chunk IDs, so these IDs can be calculated without a circular hash dependency.

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
