# Knowledge Foundry

```text
Sources → Ingestion → Document Structuring → Accepted Structural Chunks
        → Semantic Clustering → Claims / Topics / Summaries
```

- **Ingestion** verifies format, size and SHA-256, saving content-addressed
  originals and immutable metadata snapshots. Fetching requires `--fetch` and
  declared hashes. Without `--output-dir`, `ingest.py` only checks sources.
- **Document Structuring** runs a provider's rendering/OCR/parsing, normalizes
  located elements and native metadata, then assembles structural chunks.
  Tables, lists and other indivisible units may exceed `chunking.max_tokens`;
  model limits fail explicitly rather than truncating evidence. Provider-built
  chunks retain their boundaries.
- **Acceptance** binds passed parsing and chunking source/layout reviews to the
  exact frozen output. Automatic audits alone cannot authorize downstream use.
  Test-only acceptance requires fixture generation and embedding providers.
- **Downstream processing** consumes accepted chunks: semantic clustering keeps
  every-member similarity checks; claims/topics and recursive summaries form
  independent branches with source evidence.

Review follows **prepare → agent source inspection → finalize** using
`deterministic_document_structuring_review.py`. Extraction and chunk audits remain
internal checks of Document Structuring.

`Document` is a provenance record, not a stage. Source/document, element, chunk,
evidence, claim and topic ID contracts remain unchanged.

Store executions in `artifacts/YYYYMMDD-provider-corpus-NN/`; share originals and
pinned snapshots in `artifacts/ingested/`. Parsing repairs reuse ingestion;
rechunking reuses parsing. Provider changes invalidate parsing onward; changed
source bytes/editions require ingestion. Keep caches outside dated runs.

| Guide | Purpose |
| --- | --- |
| [Pipeline commands](aml-cft-pipeline.md) | Run the 13-document AML/CFT pilot. |
| [Providers](providers.md) | Configure engines and implement adapters. |
| [Benchmarks](benchmarks.md) | Compare retained runs, quality gates and limitations. |
| [State of the art](state-of-the-art.md) | Survey relevant projects and approaches. |
| [Downstream uses](downstream-uses.md) | Claims, summaries, retrieval and training constraints. |
