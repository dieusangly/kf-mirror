# Local topic compilation

Compilation consumes an existing reviewed chunk bundle. It never parses,
rechunks, or changes the input directory. It writes a new directory and refuses
to overwrite one.

## Setup

Copy `.envrc.example` to `.envrc`, replace its placeholder with the local
`OPENAI_API_KEY`, and load it with your shell or `direnv`. `.envrc` is ignored;
do not commit it. The default provider is the OpenAI Responses API and the
default model is `gpt-6-luna` with `medium` reasoning effort. Both values are
configured in `config/compilation.json` and are recorded through its build
configuration digest.

The configuration's versioned rate catalogue contains entries for the available
models. When changing `model`, select an existing entry or add its published
rate card; do not replace historical rates. Each report records the model and
specific rate card used. CLI costs are estimates derived from API-reported token
usage and that card, not invoice totals.

`batch_size_chunks` is the number of saved chunks sent in one claim-generation
request (the pilot value is `4`). Topic classification remains one request per
candidate claim.

Curate stable topic IDs in `config/topics-v1.json` before compiling. IDs must
remain stable across builds; title or scope changes are taxonomy revisions, not
new generated knowledge.

The pilot uses French prompts and requires French claim text, topic labels, and
draft views. Stable topic IDs and JSON field names remain unchanged. Claims must
preserve the source's terminology and qualifications; a translation or
paraphrase must never be presented as a verbatim citation.

## Run

Choose a fresh output directory:

```sh
.venv/bin/python scripts/compile_topics.py \
  --chunked-dir artifacts/chunked-20261013-114244 \
  --output-dir artifacts/compiled-20261013-114244
```

To inspect real claims and topic views before compiling all chunks, run a
length-stratified sample in a different fresh directory:

```sh
.venv/bin/python scripts/compile_topics.py \
  --chunked-dir artifacts/chunked-20261013-114244 \
  --output-dir artifacts/compiled-sample-20261013-114244 \
  --sample-batches 10
```

The sample selects ten original generation batches (up to forty chunks), spread
across the cumulative character volume of the exact serialized generation
requests. It makes real model calls and produces normal draft views, while
retaining all input chunk records for lineage. Only its claims and topics come
from the selected chunks. Its manifest and report identify it as a sample and
its build ID differs from a full compilation.

The compiler batches saved chunks, asks the model for atomic candidates, then
classifies each candidate separately. A candidate can cite several chunks, but
each citation must select an existing chunk evidence reference with an exact
element span. Bounded retries apply to each model request. The output reports
failed batches and unclassified candidates without assigning them to a topic.

Progress is printed coarsely to stderr at the start of each phase and at roughly
10% intervals. At completion, stderr reports the elapsed seconds and the OpenAI
cost estimate. The JSON report remains on stdout, so it can still be redirected
or processed by another command.

A sample also reports a projected whole-build cost. Generation cost is scaled
from observed usage by the known serialized-request character volume of all
batches; classification cost is scaled from the observed candidates and
classification usage. It is an estimate: claim yield, output/reasoning tokens,
and token-to-character ratios can vary. Use more sampled batches for a more
representative estimate; do not multiply a short run's cost by the number of
chunks.

For a deterministic test run, set `provider` to `fixture` in a copied
compilation config and supply a fixture response file with `--fixture`. Tests
use this mode; CI has no live model dependency.

## Output and review

The output directory contains:

- `artifact-bundle.json`: normalized source through topic records and separate
  upstream chunk and compilation build records.
- `chunks.jsonl` and `claims.jsonl`: collection records suitable for streaming.
- `topics/<topic-id>.json` and `.md`: draft inspection views.
- `build-manifest.json`: input, taxonomy, prompt, configuration, and export
  hashes; `report.json`: scope, applied rate card, usage, estimated cost,
  provider failures, and unclassified candidates.

All generated claims begin with both review statuses set to `draft`; topic views
are also `draft`. A review file passed with `--review-file` may independently
set each claim's grounding and classification status to `verified` or
`rejected`, with an optional rejection reason preserved in `report.json`. A
topic cannot become verified unless every assigned claim is verified in both
dimensions.

Topic grouping never resolves inconsistent editions or sources. Claim text must
retain stated conditions, exceptions, scope, and effective-date uncertainty. A
capture date (`retrieved_on`) is never evidence that a legal rule is effective.
The topic summary is deliberately a non-substantive draft notice, not a legal
interpretation or verified synthesis.

## Validation

Compilation calls the artifact validator before writing. Independently validate
the normalized bundle with:

```sh
.venv/bin/python scripts/validate_artifact_examples.py \
  artifacts/compiled-20261013-114244/artifact-bundle.json
```

The validator checks schemas, IDs, exact evidence spans, source/document links,
upstream chunk lineage, inverse claim/topic links, and build outputs.
