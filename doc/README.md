# Knowledge Foundry

## Find a guide

| Task | Guides |
| --- | --- |
| Understand the package or verify a checkout | [Architecture and import paths](architecture.md), [offline regression checks](regression-baseline.md) |
| Build a document corpus | [Pipeline commands](aml-cft-pipeline.md) for the 13-document AML/CFT corpus, [provider configuration and adapters](providers.md), [downstream uses of claims and summaries](downstream-uses.md) |
| Compare document processing and retrieval | [Structuring benchmarks and quality gates](benchmarks.md), [retrieval results on 123 unreviewed questions](retrieval-evaluation-123q-20261103.md), [survey of approaches](state-of-the-art.md) |
| Work with reasoning cases | [Pilot procedure and accepted datasets](aml-cft-reasoning-pilot.md), [beneficial ownership batch](aml-cft-bo-batch-handoff.md), [KYC batch](aml-cft-kyc-batch-handoff.md) |
| Run or evaluate an application | [Unified assistant](aml-cft-reasoning-assistant.md) with [direct document search](aml-cft-pipeline.md#8-run-the-document-assistant), [reasoning assistant evaluation and final review](aml-cft-reasoning-assistant-evaluation.md), [contributor workspace](../apps/contributors/README.md) |
| Verify the Chrysalide open-data inventory | [Source checks and expected report](verification-inventaire-open-data.md) |

## How the document pipeline works

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

Review uses `deterministic_document_structuring_review.py`: **prepare → agent
source inspection → finalize**. Extraction/chunk audits are internal checks.

`Document` records provenance. Source/document, element, chunk, evidence, claim
and topic ID contracts remain unchanged.

Store executions in `artifacts/YYYYMMDD-provider-corpus-NN/`; share originals and
pinned snapshots in `artifacts/ingested/`. Parsing repairs reuse ingestion;
rechunking reuses parsing. Provider changes invalidate parsing onward; changed
source bytes/editions require ingestion. Keep caches outside dated runs.
