# Structuring providers

Select `structuring.provider` and `structuring.options` in `config/pipeline.json`.
The internal provider uses Docling for PDF, lxml for HTML, markdown-it-py for
Markdown and python-docx for DOCX. PDF/OCR settings live under
`structuring.options.pdf` and `.ocr`; HTML settings under `.html`.
Rendering, preprocessing and DPI are adapter concerns, never ingestion concerns.
`structuring.source_overrides` can map a manifest ID to another `provider` and
`options`, or to per-source `options` overrides. For example, an external PDF
provider can use the internal provider for the two HTML articles.

Every adapter implements `StructureProvider` in
`src/knowledge_foundry/structure_providers.py`:

```python
from knowledge_foundry.structure_providers import ProviderResult

class Provider:
    version = "my-adapter-v1"  # change when output-affecting logic changes

    def structure(self, artifact, options):
        original = artifact["artifact_file"]  # verified saved bytes
        # Call an SDK/local engine here; normalize its response.
        return ProviderResult(elements=[{
            "locator_key": "page-1:block-1", "kind": "paragraph",
            "text": "Literal source text", "parent_key": None,
            "location": {"format": "pdf", "page": 1},
            "details": {"confidence": 0.98, "evidence_role": "authoritative"}
        }], native={"original_response": "..."})
```

Configure `"provider": "my_package.my_adapter:Provider"`. Constructor takes no
arguments; document-specific settings arrive in `options`. The artifact contains
source/document identity, frozen manifest metadata and a local original path.
Return a `ProviderResult`, not a changed source or Document model. Errors become
per-document failures. Each adapter owns its SDK, credentials, retries and native
replay compatibility; the core has no vendor SDK dependencies.

Elements use `kind`, literal `text`, a unique stable `locator_key`, `location`,
optional `parent_key`, and `details`. PDF pages are one-based; boxes are top-left
PDF points. Keep table cells/HTML, row/column spans, lists, hierarchy, figures,
confidence, layout and vendor identifiers in details/native output. Parents must
precede children. Evidence roles are `authoritative`, `structural`, `auxiliary`.
Only authoritative text becomes citable chunk evidence; figure crops remain
hashed visual assets. Normalization adds canonical IDs and revisions without
rewriting the supplied text.

To supply ready chunks, set `ProviderResult.chunks` to a list of:

```json
{"text": "Literal source text", "evidence": [{"locator_key": "page-1:block-1"}]}
```

Evidence can specify a half-open character `span` (`start`, `end`); omission
means the whole element. A chunks-only provider can instead return each chunk with `kind`, `location`
and optional `details`; normalization creates one evidence anchor per chunk.
Supply located elements as evidence anchors when
partitioning/chunking was already done by the provider. No internal rechunking
or merging is performed for these chunks. Coverage audits reject missing source
text and fragmented table/list elements. Human/agent source review is still
required; confidence is not acceptance.

## Saved provider responses

The `export` provider supports a source-bound response file for each manifest ID:
`<response_dir>/<manifest_id>.json`. Example options:

```json
{"provider": "export", "options": {"response_dir": "/absolute/responses", "format": "unstructured"}}
```

The file envelope is `{"source_sha256": "<ingested original hash>", "native": ...}`.
Supported native dialects:

- `unstructured`: Partition element array; metadata, table HTML and coordinates
  survive. CompositeElement/TableChunk output is retained as provider chunks.
- `llamaparse`: expanded `items.pages` (or legacy `pages` with `items`), including
  nested lists, tables, image captions and point-based boxes.
- `mineru`: content-list array (or `content_list`); zero-based `page_idx`, normalized
  0–1000 boxes and table HTML. Newer MiddleJson contracts need a custom adapter.
- `mistral`: OCR `pages` with Markdown and optional separated tables; native block,
  image and confidence data remains available for review.
- `normalized` (default): envelope has `elements`, optional `chunks`, `native`
  and `warnings` already matching `ProviderResult`.

These are **offline response integrations**, not built-in hosted API clients.
Use an SDK adapter for live Unstructured, LlamaParse/OCRia, MinerU or Mistral calls.
OCRia can use the normalized bridge or its own `module:factory`; no unverified
OCRia-specific response mapping is built in. Unknown native element types fail
explicitly so an adapter can be updated without silently losing structure.

Response references: [Unstructured](https://docs.unstructured.io/api-reference/partition/text-as-html),
[LlamaParse](https://developers.llamaindex.ai/llamaparse/parse/guides/response-format/),
[MinerU](https://github.com/opendatalab/MinerU/blob/master/docs/en/reference/output_files.md),
[Mistral](https://docs.mistral.ai/studio/document-processing/basic_ocr).
