# Knowledge Foundry

```text
Sources → Ingestion → Document Structuring → Validated Structural Chunks
        → Semantic Clustering → Claims / Topics / Summaries
```

`Document` remains a provenance record, not a processing stage. Source/document,
element, chunk, evidence, claim and topic IDs retain their existing contracts.

Ingestion checks or explicitly fetches originals, validates format/size/SHA-256,
and saves content-addressed original bytes plus versioned metadata snapshots.
`ingest.py` without `--output-dir` checks sources without persisting them.
With `--output-dir`, its `snapshots/<hash>.json` selects an immutable inventory;
`report.json` points to the most recent selection. Existing objects are verified
and reused. Fetching missing originals requires `--fetch` and declared hashes.

Retain provider executions as `artifacts/YYYYMMDD-provider-corpus-NN/`.
Original bytes and snapshots are shared in `artifacts/ingested/`; each retained
run can pin one snapshot through `run.json`. Dates label executions, while hashes
identify inputs. Switching provider or day does not require reacquisition.

Document Structuring consumes that artifact. Its provider performs any rendering,
OCR, DPI selection and preprocessing. Normalization preserves located elements
and rich sidecar/native structure. The structural chunker groups complete tables,
lists, headings/content, figure captions and source-proven continuations.
`chunking.max_tokens` is a packing target: indivisible units can exceed it.
Downstream model limits fail explicitly instead of truncating evidence.
Provider-built chunks retain their boundaries and text for review.

Parsing and chunking are checkpoints **inside** Document Structuring. Reviews
inspect saved originals and layout; repair starts at the earliest affected
checkpoint. Parsing repair reuses ingestion. Rechunking reuses parsing. Provider
changes invalidate parsing onward. Changed source bytes/editions require ingestion.
An acceptance artifact binds both passed reviews to the exact structural output.
Unreviewed/failed output cannot enter semantic clustering. Test-only acceptance
works exclusively with fixture generation and embedding providers.

Semantic clustering reads accepted structural chunks without rebuilding them.
It retains the existing embeddings, every-member similarity rule, request sizing
and refinement behavior. Claim generation, topic classification and recursive
summaries retain their evidence contracts and independent output branches.

| Component | Decision |
| --- | --- |
| Source/document/evidence models, source manifest | KEEP |
| Format parsers, PDF glyph/table recovery, source audits | KEEP behind provider boundary |
| Ingestion, workflow invalidation, acceptance | REFACTOR |
| Hard-size splitting and cached list-introduction chunker | REPLACE with complete structural units |
| Structural assembly | REFACTOR into Document Structuring |
| Semantic grouping, claims/topics/summaries, retrieval/storage | KEEP; consume accepted structure |
| Frozen v1/v2 assemblers, duplicate schemas, old combined builder | REMOVE |
| Superseded runbooks, evaluation narratives, generated legacy outputs | REMOVE |

See [providers](providers.md), [the 13-document pilot](aml-cft-pipeline.md),
[downstream uses](downstream-uses.md) and [project comparisons and benchmarks](benchmarks.md).
