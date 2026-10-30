# Structuring providers

## Configuration

`config/pipeline.json` stores complete specs under `structuring.profiles`;
`structuring.profile` defaults to `docling`. Override one parse with
`scripts/parse.py --profile unstructured`. Each spec contains `provider`,
`options` and optional `source_overrides` (manifest ID → provider/options).
Legacy direct `structuring.provider` / `.options` configs remain supported.

The internal provider uses Docling for PDF, lxml for HTML, markdown-it-py for
Markdown and python-docx for DOCX. Settings live under the selected profile's
`options.pdf`, `.ocr`, `.html`, `.markdown` and `.docx`. Rendering, preprocessing
and DPI belong to providers; ingestion supplies verified originals.

## Adapter contract

Implement `StructureProvider` from
[src/knowledge_foundry/structure_providers.py](../src/knowledge_foundry/structure_providers.py)
and select `"provider": "my_package.my_adapter:Provider"`:

```python
from knowledge_foundry.structure_providers import ProviderResult

class Provider:
    version = "my-adapter-v1"  # bump for output-affecting changes

    def structure(self, artifact, options):
        original = artifact["artifact_file"]  # verified local bytes
        # Invoke the engine, then return located literal source text.
        return ProviderResult(elements=[{
            "locator_key": "page-1:block-1", "kind": "paragraph",
            "text": "Literal source text",
            "location": {"format": "pdf", "page": 1, "label": "p. 1"},
            "details": {"confidence": 0.98, "evidence_role": "authoritative"}
        }], native={"original_response": "..."})
```

Constructors take no arguments. `artifact` supplies source/document identity,
frozen metadata and the original path. Adapters own SDKs, credentials, retries
and replay compatibility; errors become per-document failures.

- Elements need unique stable `locator_key`, `kind`, literal `text`, `location`;
  optional `parent_key` references a preceding parent. Normalization adds IDs
  and revisions without rewriting text.
- PDF pages are one-based; boxes use top-left PDF points. Preserve table
  cells/HTML/spans, lists, hierarchy, figures, confidence and vendor IDs in
  `details`/native output.
- Evidence roles are `authoritative`, `structural`, `auxiliary`. Only authoritative
  text becomes citable evidence; figure crops remain hashed visual assets.
- Optional `ProviderResult.chunks` preserves provider text/boundaries without
  merging/rechunking. Entries use the format below; evidence `span` is optional
  and half-open (`start`, `end`), defaulting to the whole element. A chunks-only
  provider may instead supply `kind`, `location`, optional `details` per chunk;
  normalization creates located evidence anchors.

```json
{"text": "Literal source text", "evidence": [{"locator_key": "page-1:block-1"}]}
```

Coverage/fragmentation audits and source/layout reviews still apply. Confidence
and native replay consistency do not certify extraction completeness.

## Saved provider responses

The `export` provider reads `<response_dir>/<manifest_id>.json`, bound to the
original by `{"source_sha256": "<ingested hash>", "native": ...}`:

```json
{"provider": "export", "options": {"response_dir": "/absolute/responses", "format": "unstructured"}}
```

| Format | Supported response |
| --- | --- |
| `unstructured` | Partition array, metadata, HTML and coordinates; CompositeElement/TableChunk boundaries retained. |
| `llamaparse` | Expanded `items.pages` or legacy `pages` with `items`; nested lists, tables, captions, point boxes. |
| `mineru` | Content-list array or `content_list`; zero-based `page_idx`, 0–1000 boxes, table HTML. MiddleJson needs another adapter. |
| `mistral` | OCR `pages` with Markdown/separate tables; block/image/confidence metadata retained. |
| `normalized` (default) | Envelope with `elements`, optional `chunks`, `native`, `warnings` matching `ProviderResult`. |

These are offline integrations. Hosted calls need an SDK adapter; OCRia can use
`normalized` or a custom factory, with no built-in vendor mapping. Unknown native
element types fail explicitly.

## Local open-source Unstructured

`knowledge_foundry.unstructured_provider:Provider` runs local `partition_pdf` on
verified bytes, with telemetry disabled and no URL, credentials or hosted API.
PDF engines are pinned in `requirements.txt`; see [commands](aml-cft-pipeline.md).
First use may download NLP/model weights. Keep caches outside dated runs and
record environment/model versions.

The `unstructured` profile selects `hi_res`, YOLOX, French/English OCR and table
inference. L1/L2 explicitly use the internal HTML reader/article selector.

| Option | Values / constraints |
| --- | --- |
| `strategy` | `fast`, `hi_res`, `ocr_only`; `auto` and fallback fail explicitly. |
| `languages` | OCR languages, currently `fra`, `eng`. |
| `infer_table_structure` | Requires `hi_res`. |
| `hi_res_model_name` | Defaults to `yolox`; requires `hi_res`. |
| Environment `PDF_RENDER_DPI` | Rasterization resolution; 350 in the measured hi_res run. |

`fast` reads embedded text without OCR, table inference or figure assets. Hi_res
adds layout/table detection, not guaranteed fidelity. Empty pages warn and mark
partial outcomes; they are not presumed blank. Native data stays in `provider.json`;
boxes convert to PDF points, duplicate IDs receive stable order-based locators,
and ambiguous/missing/forward parents warn rather than create invalid links.
The shared pipeline saves figure crops from originals.

The SDK labels predicted footnotes `Footer`, potentially excluding source
evidence; supported `Footnote` elements cannot be inferred from that label.
Table HTML may omit cells or disagree with plain text. See
[benchmarks](benchmarks.md) for measured failures and acceptance status; retained
`configuration.json`/`environment.txt` describe the historical pilot environment.

Response references: [Unstructured](https://docs.unstructured.io/api-reference/partition/text-as-html),
[LlamaParse](https://developers.llamaindex.ai/llamaparse/parse/guides/response-format/),
[MinerU](https://github.com/opendatalab/MinerU/blob/master/docs/en/reference/output_files.md),
[Mistral](https://docs.mistral.ai/studio/document-processing/basic_ocr).
